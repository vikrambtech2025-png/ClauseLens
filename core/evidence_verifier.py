"""Independent evidence verification.

The verifier does not trust the analyzer. It re-checks every quote against the
original extracted span, validates page/paragraph metadata, recomputes policy
consistency for deterministic rules, and downgrades failures instead of turning
them into successful findings.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from core.text_utils import normalize_for_match, normalize_whitespace
from models.schemas import (
    Document,
    Finding,
    FindingStatus,
    PolicyConfig,
    VerificationStatus,
    utc_now_iso,
)


class EvidenceVerifier:
    MIN_QUOTE_CHARS = 8

    def verify(
        self,
        findings: list[Finding],
        document: Document,
        policy: Optional[PolicyConfig] = None,
    ) -> list[Finding]:
        return [self._verify_one(finding.model_copy(deep=True), document, policy) for finding in findings]

    # ------------------------------------------------------------------ one
    def _verify_one(
        self,
        finding: Finding,
        document: Document,
        policy: Optional[PolicyConfig],
    ) -> Finding:
        record: dict[str, Any] = {
            "checked_at": utc_now_iso(),
            "quote_match": None,
            "location_checked": False,
            "location_matches": None,
            "policy_support_checked": False,
            "policy_support_result": None,
            "source_verified": False,
        }

        # Category-level coverage findings have no quotation by design.
        if not finding.exact_quote.strip():
            if finding.status in {FindingStatus.NOT_FOUND, FindingStatus.NOT_VERIFIED}:
                finding.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE
                finding.evidence_quality = "C · no quotation; category not verified"
                finding.policy_support_verified = False
                record["reason"] = "Category-level finding has no source quotation."
                record["policy_support_result"] = "not_applicable"
                finding.raw_evidence = {**finding.raw_evidence, "verification": record}
                return finding
            finding.status = FindingStatus.NOT_VERIFIED
            finding.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE
            finding.evidence_quality = "C · evidence insufficient"
            finding.uncertainty_reason = "No quotation was supplied for this finding."
            finding.raw_evidence = {**finding.raw_evidence, "verification": record}
            return finding

        if not finding.source_id:
            return self._fail(
                finding,
                record,
                VerificationStatus.FAILED_LOCATION,
                "No source identifier was supplied.",
            )

        span = document.span_by_id(finding.source_id)
        location = document.location_by_id(finding.source_id)
        if span is None or location is None:
            return self._fail(
                finding,
                record,
                VerificationStatus.FAILED_LOCATION,
                f"Source identifier '{finding.source_id}' does not exist in the document.",
            )
        record["source_verified"] = True

        location_ok, location_detail = self._validate_location(finding, document, location)
        record["location_checked"] = True
        record["location_matches"] = location_ok
        record["location_detail"] = location_detail
        if not location_ok:
            return self._fail(
                finding,
                record,
                VerificationStatus.FAILED_LOCATION,
                location_detail,
            )

        quote_result = self._find_quote(span.text, finding.exact_quote, finding.span_start, finding.span_end)
        record["quote_match"] = quote_result
        if quote_result["status"] != "matched":
            reason = quote_result.get("reason", "The quotation could not be matched to the source.")
            return self._fail(
                finding,
                record,
                VerificationStatus.FAILED_QUOTE_MATCH,
                reason,
            )

        # Canonicalize to the exact source slice. This is derived from the source,
        # never from model output.
        canonical_start = quote_result["span_start"]
        canonical_end = quote_result["span_end"]
        finding.exact_quote = span.text[canonical_start:canonical_end].strip()
        finding.span_start = canonical_start
        finding.span_end = canonical_end
        record["verified_span_start"] = canonical_start
        record["verified_span_end"] = canonical_end
        record["quote_match_type"] = quote_result["match_type"]

        policy_ok, policy_detail = self._check_policy_support(finding, policy, span.text)
        record["policy_support_checked"] = policy_detail.get("checked", False)
        record["policy_support_result"] = policy_detail
        finding.policy_support_verified = policy_ok is True

        if policy_ok is False:
            finding.verification_status = VerificationStatus.FAILED_POLICY_SUPPORT
            finding.status = FindingStatus.NOT_VERIFIED
            finding.evidence_quality = "C · quote/location verified, but policy support failed"
            finding.uncertainty_reason = (
                "The quoted passage did not independently support the stated policy "
                f"comparison: {policy_detail.get('reason', 'unsupported comparison')}"
            )
            finding.raw_evidence = {**finding.raw_evidence, "verification": record}
            return finding

        if finding.is_ai_generated:
            # AI interpretation is supplementary only. It may never create a
            # confirmed conflict, even if the quote is exact.
            if finding.status not in {FindingStatus.REVIEW_REQUIRED, FindingStatus.NOT_VERIFIED}:
                finding.status = FindingStatus.REVIEW_REQUIRED
            finding.evidence_quality = (
                "B · exact quote and location verified; AI interpretation requires review"
            )
            finding.verification_status = VerificationStatus.VERIFIED
            finding.raw_evidence = {**finding.raw_evidence, "verification": record}
            return finding

        if finding.status in {FindingStatus.HIGH, FindingStatus.MEDIUM, FindingStatus.LOW}:
            if policy_ok is not True:
                return self._fail(
                    finding,
                    record,
                    VerificationStatus.FAILED_POLICY_SUPPORT,
                    "A confirmed policy conflict requires independently verifiable policy support.",
                )
            finding.verification_status = VerificationStatus.VERIFIED
            finding.evidence_quality = (
                "A · exact quote + validated location + deterministic policy rule"
            )
        elif finding.status == FindingStatus.NO_CONFLICT_FOUND:
            if policy is not None and policy_ok is not True:
                return self._fail(
                    finding,
                    record,
                    VerificationStatus.FAILED_POLICY_SUPPORT,
                    "The quoted provision did not support the no-conflict comparison.",
                )
            finding.verification_status = VerificationStatus.VERIFIED
            finding.evidence_quality = (
                "A · exact quote + validated location + deterministic comparison"
            )
        elif finding.status == FindingStatus.REVIEW_REQUIRED:
            finding.verification_status = VerificationStatus.VERIFIED
            finding.evidence_quality = (
                "B · exact quote and location verified; interpretation requires review"
            )
        else:
            finding.verification_status = VerificationStatus.VERIFIED
            finding.evidence_quality = "B · exact quote and location verified"

        finding.raw_evidence = {**finding.raw_evidence, "verification": record}
        return finding

    # --------------------------------------------------------------- helpers
    def _validate_location(self, finding: Finding, document: Document, location) -> tuple[bool, str]:
        if finding.page_number is not None:
            if location.page_number is None:
                return False, "Finding cites a page, but the source span is not page-based."
            if finding.page_number != location.page_number:
                return False, (
                    f"Finding cites page {finding.page_number}, but source "
                    f"{finding.source_id} is on page {location.page_number}."
                )
            if document.stats.num_pages is not None and not (
                1 <= finding.page_number <= document.stats.num_pages
            ):
                return False, "Finding cites a page outside the document page range."
        else:
            finding.page_number = location.page_number

        if finding.paragraph_index is not None:
            if location.paragraph_index is None:
                return False, "Finding cites a paragraph, but the source is not paragraph-based."
            if finding.paragraph_index != location.paragraph_index:
                return False, (
                    f"Finding cites paragraph {finding.paragraph_index}, but source "
                    f"{finding.source_id} is paragraph {location.paragraph_index}."
                )
        else:
            finding.paragraph_index = location.paragraph_index

        if location.page_number is None and location.paragraph_index is None:
            if location.table_index is None:
                return False, "Source has no page, paragraph, table, or row location."
        return True, "Source location matches the extraction map."

    def _find_quote(
        self,
        span_text: str,
        quote: str,
        span_start: Optional[int],
        span_end: Optional[int],
    ) -> dict[str, Any]:
        quote_stripped = quote.strip()
        if len(normalize_whitespace(quote_stripped)) < self.MIN_QUOTE_CHARS:
            return {
                "status": "failed",
                "reason": "Quotation is too short to be meaningful evidence.",
            }

        direct = span_text.find(quote_stripped)
        if direct >= 0:
            start, end, match_type = direct, direct + len(quote_stripped), "exact"
        else:
            start, end = self._whitespace_normalized_find(span_text, quote_stripped)
            match_type = "whitespace_normalized" if start >= 0 else "none"

        if start < 0:
            return {
                "status": "failed",
                "reason": "The quoted text does not appear verbatim in the cited source span.",
            }

        if span_start is not None:
            provided_end = span_end if span_end is not None else span_start + len(quote_stripped)
            if provided_end > len(span_text):
                return {
                    "status": "failed",
                    "reason": "Provided source-span offsets exceed the source text.",
                }
            provided = span_text[span_start:provided_end]
            if normalize_whitespace(provided) != normalize_whitespace(quote_stripped):
                return {
                    "status": "failed",
                    "reason": (
                        "Provided source-span offsets do not point to the quoted text."
                    ),
                }
            start, end = span_start, provided_end

        return {
            "status": "matched",
            "match_type": match_type,
            "span_start": start,
            "span_end": end,
        }

    @staticmethod
    def _whitespace_normalized_find(span_text: str, quote: str) -> tuple[int, int]:
        collapsed_chars: list[str] = []
        mapping: list[int] = []
        previous_space = False
        for index, char in enumerate(span_text):
            if char.isspace():
                if collapsed_chars and not previous_space:
                    collapsed_chars.append(" ")
                    mapping.append(index)
                previous_space = True
            else:
                collapsed_chars.append(char)
                mapping.append(index)
                previous_space = False
        while collapsed_chars and collapsed_chars[-1] == " ":
            collapsed_chars.pop()
            mapping.pop()
        collapsed = "".join(collapsed_chars)
        target = normalize_whitespace(quote)
        position = collapsed.find(target)
        if position < 0 or not target:
            return -1, -1
        start = mapping[position]
        end = mapping[position + len(target) - 1] + 1
        return start, end

    def _check_policy_support(
        self,
        finding: Finding,
        policy: Optional[PolicyConfig],
        quote: str,
    ) -> tuple[Optional[bool], dict[str, Any]]:
        if policy is None:
            return None, {"checked": False, "reason": "No policy supplied to the verifier."}

        rule = finding.rule_id or ""
        evidence = finding.raw_evidence
        checked = True
        ok: Optional[bool] = None
        reason = ""

        # Review-only rules are not confirmed conflicts; quote/location is enough.
        review_rules = {
            "payment.conditional_deadline",
            "payment.business_day_ambiguity",
            "renewal.business_day_notice",
            "liability.uncapped_with_carveout",
            "liability.indemnity_cap_unknown",
            "liability.cap_unknown",
            "jurisdiction.ambiguous_forum",
        }
        if rule in review_rules or rule.startswith("ai."):
            return None, {
                "checked": True,
                "result": "review_required",
                "reason": "This rule intentionally produces a review finding, not a confirmed conflict.",
            }

        if rule == "payment.days_exceed_limit":
            observed = _as_int(evidence.get("observed_days"))
            limit = policy.max_payment_days
            ok = observed is not None and observed > limit
            reason = f"Observed payment days {observed} must exceed policy maximum {limit}."
        elif rule == "payment.within_limit":
            observed = evidence.get("observed_days")
            observed_int = _as_int(observed)
            limit = policy.max_payment_days
            approx = _as_int(evidence.get("approximate_calendar_days"))
            compared = approx if approx is not None else observed_int
            ok = compared is not None and compared <= limit
            reason = f"Observed/computed payment days {compared} must be <= policy maximum {limit}."
        elif rule == "payment.business_days_within_limit":
            approx = _as_int(evidence.get("approximate_calendar_days"))
            ok = approx is not None and approx <= policy.max_payment_days
            reason = "Approximate business-day conversion must fit the calendar-day policy."
        elif rule == "payment.above_preference":
            observed = _as_int(evidence.get("observed_days"))
            ok = (
                observed is not None
                and observed <= policy.max_payment_days
                and observed > policy.preferences.preferred_payment_days
            )
            reason = "Observed days must be within the hard limit but above the preference."
        elif rule == "renewal.auto_renewal_prohibited":
            ok = not policy.allow_auto_renewal and bool(evidence.get("auto_renewal"))
            reason = "Policy must prohibit automatic renewal and the quote must contain it."
        elif rule == "renewal.notice_below_minimum":
            notice = _as_int(evidence.get("notice_days"))
            ok = notice is not None and notice < policy.min_renewal_notice_days
            reason = "Observed notice must be below the policy minimum."
        elif rule == "renewal.notice_missing":
            ok = policy.min_renewal_notice_days > 0 and evidence.get("notice_days") is None
            reason = "No notice period was extracted while a minimum is required."
        elif rule == "renewal.no_auto_trigger":
            ok = not self._has_auto_renewal(quote)
            reason = "The quoted provision must not contain a positive automatic-renewal trigger."
        elif rule == "renewal.within_policy":
            notice = _as_int(evidence.get("notice_days"))
            ok = (
                policy.allow_auto_renewal
                and notice is not None
                and notice >= policy.min_renewal_notice_days
            )
            reason = "Automatic renewal must be allowed and notice must meet the minimum."
        elif rule == "renewal.preference_flag":
            ok = policy.preferences.flag_auto_renewal_even_if_allowed
            reason = "The preference to flag automatic renewal must be enabled."
        elif rule == "liability.uncapped_prohibited":
            ok = policy.liability_must_be_capped and bool(evidence.get("uncapped"))
            reason = "Policy must require a cap and the quote must contain uncapped wording."
        elif rule == "liability.uncapped_allowed":
            ok = not policy.liability_must_be_capped and bool(evidence.get("uncapped"))
            reason = "The policy must allow uncapped liability."
        elif rule == "liability.cap_present":
            ok = policy.liability_must_be_capped and bool(evidence.get("capped"))
            reason = "The quote must contain a liability cap and the policy must require one."
        elif rule == "liability.cap_not_required":
            ok = not policy.liability_must_be_capped and bool(evidence.get("capped"))
            reason = "A cap must be present and not required by policy."
        elif rule == "liability.one_way_indemnity":
            ok = (
                policy.preferences.prefer_mutual_indemnity
                and bool(evidence.get("indemnity"))
                and not evidence.get("mutual")
            )
            reason = "One-way indemnity must be present and mutual indemnity preferred."
        elif rule == "jurisdiction.not_approved":
            forum = str(evidence.get("found_forum") or "")
            approved = self._approved(forum, policy.approved_jurisdictions)
            ok = bool(forum) and not approved
            reason = f"Found forum '{forum}' must not be in the approved list."
            if finding.status == FindingStatus.HIGH and not evidence.get("exclusive"):
                # High severity for jurisdiction is reserved for exclusive or hard policy
                # violations. A non-exclusive unapproved forum is medium.
                ok = False
                reason = "High severity requires an exclusive unapproved forum."
        elif rule == "jurisdiction.approved":
            forum = str(evidence.get("found_forum") or "")
            approved = self._approved(forum, policy.approved_jurisdictions)
            exclusive = bool(evidence.get("exclusive"))
            ok = approved and (not exclusive or policy.allow_exclusive_jurisdiction)
            reason = "Forum must be approved and exclusivity must be permitted."
        elif rule == "jurisdiction.exclusive_prohibited":
            ok = not policy.allow_exclusive_jurisdiction and bool(evidence.get("exclusive"))
            reason = "Policy must prohibit exclusive jurisdiction."
        else:
            checked = False
            return None, {
                "checked": False,
                "reason": f"No independent policy-support rule registered for '{rule}'.",
            }

        # Secondary quote-level check: the quote must contain a trigger keyword for
        # confirmed rules, so a mismatch between quote and rule is rejected.
        if ok and not self._quote_supports_rule(rule, quote, evidence):
            ok = False
            reason = "The quoted passage does not contain the trigger for the stated rule."

        return ok, {
            "checked": checked,
            "result": "pass" if ok else "fail",
            "reason": reason,
            "rule": rule,
        }

    @staticmethod
    def _has_auto_renewal(quote: str) -> bool:
        from core.policy_engine import PolicyEngine

        for match in PolicyEngine.AUTO_RENEW.finditer(quote):
            prefix = quote[max(0, match.start() - 45) : match.start()].casefold()
            if re.search(
                r"\b(?:not|no|never|shall\s+not|will\s+not|does\s+not)\b", prefix
            ):
                continue
            return True
        return False

    @staticmethod
    def _approved(forum: str, approved: list[str]) -> bool:
        found_norm = normalize_for_match(forum)
        for item in approved:
            item_norm = normalize_for_match(item)
            if item_norm and (
                found_norm == item_norm or found_norm in item_norm or item_norm in found_norm
            ):
                return True
        return False

    @staticmethod
    def _quote_supports_rule(rule: str, quote: str, evidence: dict[str, Any]) -> bool:
        text = quote.casefold()
        if rule.startswith("payment."):
            return any(
                term in text
                for term in ("pay", "invoice", "remit", "net ", "fee", "compensation", "settlement")
            )
        if rule.startswith("renewal."):
            return any(term in text for term in ("renew", "successive", "evergreen", "terminat", "expir"))
        if rule.startswith("liability."):
            return any(term in text for term in ("liab", "indemn", "hold harmless", "damages"))
        if rule.startswith("jurisdiction."):
            forum = str(evidence.get("found_forum") or "").casefold()
            if forum and forum in text:
                return True
            return any(
                term in text
                for term in ("govern", "jurisdiction", "venue", "forum", "arbitrat", "dispute", "court")
            )
        return True

    def _fail(
        self,
        finding: Finding,
        record: dict[str, Any],
        verification: VerificationStatus,
        reason: str,
    ) -> Finding:
        finding.verification_status = verification
        finding.status = FindingStatus.NOT_VERIFIED
        finding.policy_support_verified = False
        finding.confidence = None
        finding.evidence_quality = "C · verification failed"
        finding.uncertainty_reason = reason
        record["failure_reason"] = reason
        finding.raw_evidence = {**finding.raw_evidence, "verification": record}
        return finding


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
