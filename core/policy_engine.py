"""Deterministic policy-compliance engine.

Every confirmed conflict originates from a transparent rule with a preserved
source segment. The engine distinguishes numeric violations, ambiguity,
business-day conversions, conditional deadlines, and compliant provisions.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from models.schemas import (
    CategoryRetrieval,
    ClauseCategory,
    Finding,
    FindingStatus,
    PaymentBasis,
    PolicyConfig,
    RetrievedPassage,
    RiskLevel,
    VerificationStatus,
)
from core.text_utils import normalize_for_match, normalize_whitespace

STATUS_TO_RISK: dict[FindingStatus, RiskLevel] = {
    FindingStatus.HIGH: RiskLevel.HIGH,
    FindingStatus.MEDIUM: RiskLevel.MEDIUM,
    FindingStatus.LOW: RiskLevel.LOW,
    FindingStatus.REVIEW_REQUIRED: RiskLevel.MEDIUM,
    FindingStatus.NO_CONFLICT_FOUND: RiskLevel.LOW,
    FindingStatus.NOT_FOUND: RiskLevel.LOW,
    FindingStatus.NOT_VERIFIED: RiskLevel.LOW,
}

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "one hundred twenty": 120,
}

_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_UNITS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}

# Legal drafting frequently writes the period twice: "ninety (90) calendar
# days". The extractor must understand both the word and the parenthetical
# numeral. Ignoring that form would miss real deadlines and produce a false
# "no conflict" result.
_WORD_DAY = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?"
)
_DAY_NUMBER = (
    rf"(?P<number>\d{{1,3}}|{_WORD_DAY})"
    r"(?:\s*\(\s*(?P<parenthetical>\d{1,3})\s*\))?"
)
_DAY_UNIT = (
    rf"\b{_DAY_NUMBER}\s*"
    r"(?:(?P<basis>calendar|business|working)[-\s]+)?days?\b"
)
_DAYS_RE = re.compile(_DAY_UNIT, re.IGNORECASE)


def _word_to_int(value: str) -> Optional[int]:
    cleaned = re.sub(r"\s+", " ", value.strip().casefold().replace("-", " "))
    if cleaned in _TEENS:
        return _TEENS[cleaned]
    if cleaned in _TENS:
        return _TENS[cleaned]
    if cleaned in _UNITS:
        return _UNITS[cleaned]
    parts = cleaned.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS:
        return _TENS[parts[0]] + _UNITS[parts[1]]
    return None


def parse_days_match(match: re.Match[str]) -> Optional[tuple[int, str]]:
    """Parse a ``_DAYS_RE`` match into (days, basis)."""
    parenthetical = match.groupdict().get("parenthetical")
    raw = match.groupdict().get("number") or ""
    days: Optional[int]
    if parenthetical:
        try:
            days = int(parenthetical)
        except (TypeError, ValueError):
            days = None
    else:
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = _word_to_int(raw)
    if days is None:
        return None
    basis_raw = (match.groupdict().get("basis") or "calendar").casefold()
    basis = "business" if basis_raw in {"business", "working"} else "calendar"
    return days, basis


class PolicyEngine:
    RULE_VERSION = "2026.10"

    PAYMENT_NET = re.compile(r"\bnet\s*[-:]?\s*(\d{1,3})\b", re.IGNORECASE)
    PAYMENT_DAYS = (_DAYS_RE,)
    PAYMENT_TERMS = re.compile(
        r"\b(?:payment|payable|paid|pay|invoice|invoiced|remittance|remit|fee|fees|"
        r"compensation|settlement|net\s*\d+)\b",
        re.IGNORECASE,
    )
    OPEN_ENDED_PAYMENT = re.compile(
        r"\b(?:upon receipt|on receipt|immediately|promptly|as soon as practicable|"
        r"from time to time|sole discretion|at any time|withhold|withholding|pending|"
        r"when invoiced|after approval|upon approval|subject to approval)\b",
        re.IGNORECASE,
    )
    CONDITIONAL_PAYMENT = re.compile(
        r"\b(?:pending|subject to|after (?:written )?(?:approval|acceptance|completion|"
        r"certification|sign-?off)|upon (?:written )?(?:approval|acceptance|completion|"
        r"certification|sign-?off)|sole discretion|as determined by|satisfactory completion)\b",
        re.IGNORECASE,
    )

    AUTO_RENEW = re.compile(
        r"\b(?:automatically\s+renew(?:s|ed|al)?|auto-?renew(?:s|ed|al)?|"
        r"renew(?:s|ed)?\s+automatically|successive\s+(?:renewal\s+)?terms?|evergreen|"
        r"(?:shall|will)\s+be\s+(?:automatically\s+)?extended)\b",
        re.IGNORECASE,
    )
    RENEWAL_CONTEXT = re.compile(
        r"\b(?:renew|renewal|renewed|termination|terminate|expiration|expiry|"
        r"successive\s+terms?|evergreen)\b",
        re.IGNORECASE,
    )

    UNCAPPED = re.compile(
        r"\b(?:uncapped|unlimited\s+liability|no\s+(?:aggregate\s+)?limitation\s+of\s+liability|"
        r"without\s+(?:any\s+)?limitation\s+of\s+liability|no\s+cap\s+on\s+liability|"
        r"liability\b.{0,50}\b(?:shall|will)\s+not\s+be\s+(?:limited|capped))\b",
        re.IGNORECASE,
    )
    CARVEOUT = re.compile(
        r"\b(?:except|excluding|other than|for (?:fraud|gross negligence|willful misconduct|"
        r"confidentiality|indemnif))\b",
        re.IGNORECASE,
    )
    CAPPED = re.compile(
        r"\b(?:shall|will)\s+not\s+exceed\b|\blimited\s+to\b|\bliability\s+cap\b|"
        r"\bcap\s+on\s+liability\b|\bmaximum\s+aggregate\s+liability\b|"
        r"\baggregate\s+liability\b.{0,80}\b(?:limited|capped|not\s+exceed)\b",
        re.IGNORECASE,
    )
    INDEMNITY = re.compile(r"\b(?:indemnif(?:y|ies|ication)|hold\s+harmless)\b", re.IGNORECASE)
    MUTUAL_INDEMNITY = re.compile(
        r"\b(?:each\s+party|both\s+parties|mutual(?:ly)?|reciprocal)\b", re.IGNORECASE
    )

    JURISDICTION_SIGNAL = re.compile(
        r"\b(?:governing\s+law|governed\s+by|jurisdiction|venue|forum|arbitration|"
        r"arbitrator|dispute(?:s)?|courts?|laws?\s+of|conflict\s+of\s+laws?)\b",
        re.IGNORECASE,
    )
    FORUM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "exclusive_jurisdiction",
            re.compile(
                r"\bexclusive\s+jurisdiction\s+of\s+(?:the\s+)?(?:courts?\s+of\s+)?"
                r"(?:the\s+)?([A-Z][A-Za-z .'\-]{1,60}?)"
                r"(?=\s*(?:,|\.|;|\band\b|\bfor\b|\bwithout\b|\bthe\s+parties\b|$))"
            ),
        ),
        (
            "courts",
            re.compile(
                r"\bcourts?\s+of\s+(?:the\s+)?([A-Z][A-Za-z .'\-]{1,60}?)"
                r"(?=\s*(?:,|\.|;|\band\b|\bfor\b|\bwithout\b|$))"
            ),
        ),
        (
            "governing_law",
            re.compile(
                r"\b(?:governed\s+by|construed\s+(?:in\s+accordance\s+)?with|subject\s+to)\b"
                r".{0,40}?\blaws?\s+of\s+(?:the\s+)?([A-Z][A-Za-z .'\-]{1,60}?)"
                r"(?=\s*(?:,|\.|;|\band\b|\bwithout\b|\bthe\s+parties\b|$))"
            ),
        ),
        (
            "venue",
            re.compile(
                r"\b(?:venue|forum)\s+(?:shall\s+be|will\s+be|is)\s+(?:in\s+)?(?:the\s+)?"
                r"([A-Z][A-Za-z .'\-]{1,60}?)"
                r"(?=\s*(?:,|\.|;|\band\b|$))"
            ),
        ),
        (
            "arbitration",
            re.compile(
                r"\b(?:arbitration|arbitral\s+tribunal)\b.{0,80}?\b(?:seat(?:ed)?\s+in|"
                r"conducted\s+in|held\s+in)\s+(?:the\s+)?([A-Z][A-Za-z .'\-]{1,60}?)"
                r"(?=\s*(?:,|\.|;|\band\b|$))"
            ),
        ),
    )
    AMBIGUOUS_FORUM = re.compile(
        r"\b(?:to be agreed|as agreed|as set forth herein|mutually agreed|"
        r"procedure to be agreed|applicable law|as the parties may agree)\b",
        re.IGNORECASE,
    )

    # ---------------------------------------------------------------- public API
    def evaluate(
        self,
        retrieval: dict[str, CategoryRetrieval],
        policy: PolicyConfig,
        document=None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for category in ClauseCategory:
            result = retrieval.get(category.value)
            if not result or not result.passages:
                continue
            category_findings: list[Finding] = []
            for passage in result.passages:
                if category == ClauseCategory.PAYMENT:
                    category_findings.extend(self._evaluate_payment(passage, policy))
                elif category == ClauseCategory.RENEWAL_TERMINATION:
                    category_findings.extend(self._evaluate_renewal(passage, policy))
                elif category == ClauseCategory.LIABILITY_INDEMNITY:
                    category_findings.extend(self._evaluate_liability(passage, policy))
                elif category == ClauseCategory.JURISDICTION_DISPUTE:
                    category_findings.extend(self._evaluate_jurisdiction(passage, policy))
            findings.extend(self._dedupe(category_findings))
        return findings

    # ------------------------------------------------------------------ payment
    def _evaluate_payment(self, passage: RetrievedPassage, policy: PolicyConfig) -> list[Finding]:
        text = passage.segment.text
        if not self.PAYMENT_TERMS.search(text):
            return []

        numeric = self._extract_payment_days(text)
        conditional = bool(self.CONDITIONAL_PAYMENT.search(text))
        open_ended = bool(self.OPEN_ENDED_PAYMENT.search(text))

        if numeric is None:
            if open_ended or conditional:
                return [
                    self._make(
                        ClauseCategory.PAYMENT,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        None,
                        "Payment timing is present but not expressed as a fixed, "
                        "deterministic deadline. The wording depends on receipt, approval, "
                        "acceptance, discretion, or another open-ended condition.",
                        "Ask for a fixed payment deadline measured from a defined event "
                        "(for example, invoice receipt) and confirm the day basis in writing.",
                        "payment.open_ended_deadline",
                        {
                            "rule": "payment.open_ended_deadline",
                            "policy_max_days": policy.max_payment_days,
                            "conditional": conditional,
                            "open_ended": open_ended,
                        },
                        uncertainty=(
                            "Deterministic rules cannot convert this wording into a bounded "
                            "number of payment days."
                        ),
                    )
                ]
            return []

        days, basis = numeric
        policy_basis = policy.payment_basis
        basis_mismatch = basis != policy_basis.value
        # Conservative calendar approximation: 7 calendar days / 5 business days.
        approx_calendar_days = math.ceil(days * 7 / 5) if basis == "business" else days
        approx_policy_basis_days = (
            math.ceil(days * 5 / 7) if basis == "calendar" and policy_basis == PaymentBasis.BUSINESS else days
        )
        compared_days = (
            approx_policy_basis_days
            if policy_basis == PaymentBasis.BUSINESS and basis == "calendar"
            else approx_calendar_days
            if basis == "business" and policy_basis == PaymentBasis.CALENDAR
            else days
        )
        limit = policy.max_payment_days

        if conditional or open_ended:
            return [
                self._make(
                    ClauseCategory.PAYMENT,
                    FindingStatus.REVIEW_REQUIRED,
                    passage,
                    f"Maximum payment delay is {limit} {policy_basis.value} days; "
                    f"the contract states {days} {basis or 'unspecified'} days, but the "
                    "deadline is conditional or open-ended.",
                    f"The clause appears to allow {days} {basis or 'unspecified'} day(s), "
                    f"but the clock starts only after an approval, acceptance, receipt, or "
                    f"other condition. It cannot be confirmed that the payment obligation is "
                    f"bounded by your {limit}-day policy without reviewing that condition.",
                    "Negotiate a fixed payment period measured from a clearly defined event. "
                    "Remove sole-discretion or open-ended acceptance conditions.",
                    "payment.conditional_deadline",
                    {
                        "rule": "payment.conditional_deadline",
                        "observed_days": days,
                        "observed_basis": basis or "unspecified",
                        "policy_max_days": limit,
                        "conditional": conditional,
                        "open_ended": open_ended,
                    },
                    uncertainty=(
                        "The number of days is visible, but the triggering event is "
                        "conditional. A precise calendar comparison cannot be made safely."
                    ),
                )
            ]

        if basis_mismatch:
            if compared_days > limit:
                return [
                    self._make(
                        ClauseCategory.PAYMENT,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        f"Maximum payment delay is {limit} {policy_basis.value} days; "
                        f"the contract uses {days} {basis} days (approximately "
                        f"{compared_days} calendar days).",
                        f"The contract states {days} {basis} days. This is approximately "
                        f"{compared_days} calendar days, which appears to exceed the "
                        f"{limit}-day policy, but business-day conversion depends on weekends "
                        "and holidays and cannot be confirmed deterministically.",
                        "Negotiate an explicit calendar-day deadline, or provide the applicable "
                        "holiday calendar and confirm the exact due date in writing.",
                        "payment.business_day_ambiguity",
                        {
                            "rule": "payment.business_day_ambiguity",
                            "observed_days": days,
                            "observed_basis": basis,
                            "approximate_calendar_days": compared_days,
                            "policy_max_days": limit,
                            "policy_basis": policy_basis.value,
                        },
                        uncertainty=(
                            "Business/working-day deadlines depend on a holiday calendar; the "
                            "approximate conversion is not used to mark this as confirmed."
                        ),
                    )
                ]
            return [
                self._make(
                    ClauseCategory.PAYMENT,
                    FindingStatus.NO_CONFLICT_FOUND,
                    passage,
                    None,
                    f"The contract uses {days} {basis} day(s), approximately {compared_days} "
                    f"calendar days, within the {limit}-day policy. Exact business-day "
                    "compliance depends on the holiday calendar but the gap is conservative.",
                    "No action required under the current policy. Retain the holiday calendar "
                    "if precise enforcement matters.",
                    "payment.business_days_within_limit",
                    {
                        "rule": "payment.business_days_within_limit",
                        "observed_days": days,
                        "observed_basis": basis,
                        "approximate_calendar_days": compared_days,
                        "policy_max_days": limit,
                        "comparison": f"{compared_days} <= {limit}",
                    },
                    uncertainty=(
                        "Business-day conversion is approximate; exact compliance depends on "
                        "weekends and holidays."
                    ),
                )
            ]

        if days > limit:
            return [
                self._make(
                    ClauseCategory.PAYMENT,
                    FindingStatus.HIGH,
                    passage,
                    f"Maximum payment delay: {limit} {policy_basis.value} days; "
                    f"contract requires {days} {basis} days.",
                    f"The payment clause requires payment within {days} {basis} days, "
                    f"which exceeds your configured maximum of {limit} "
                    f"{policy_basis.value} days by {days - limit} days.",
                    f"Negotiate payment within {limit} {policy_basis.value} days or less. "
                    "Define when the clock starts and avoid approval-dependent triggers.",
                    "payment.days_exceed_limit",
                    {
                        "rule": "payment.days_exceed_limit",
                        "observed_days": days,
                        "observed_basis": basis,
                        "policy_max_days": limit,
                        "policy_basis": policy_basis.value,
                        "comparison": f"{days} > {limit}",
                    },
                )
            ]

        if policy.preferences.preferred_payment_days < days <= limit:
            return [
                self._make(
                    ClauseCategory.PAYMENT,
                    FindingStatus.LOW,
                    passage,
                    f"Preference: target payment within {policy.preferences.preferred_payment_days} "
                    f"days; contract states {days} days.",
                    f"The {days}-day payment period complies with the hard limit of {limit} days "
                    f"but is longer than your preferred target of "
                    f"{policy.preferences.preferred_payment_days} days.",
                    "Optional negotiation: request shorter payment terms closer to your target.",
                    "payment.above_preference",
                    {
                        "rule": "payment.above_preference",
                        "observed_days": days,
                        "preferred_days": policy.preferences.preferred_payment_days,
                        "policy_max_days": limit,
                        "comparison": f"{days} <= {limit} and {days} > "
                        f"{policy.preferences.preferred_payment_days}",
                    },
                )
            ]

        return [
            self._make(
                ClauseCategory.PAYMENT,
                FindingStatus.NO_CONFLICT_FOUND,
                passage,
                None,
                f"The payment period of {days} {basis} days is within your configured "
                f"maximum of {limit} {policy_basis.value} days.",
                "No action required under the current policy.",
                "payment.within_limit",
                {
                    "rule": "payment.within_limit",
                    "observed_days": days,
                    "observed_basis": basis,
                    "policy_max_days": limit,
                    "policy_basis": policy_basis.value,
                    "comparison": f"{days} <= {limit}",
                },
            )
        ]

    def _extract_payment_days(self, text: str) -> Optional[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        for match in _DAYS_RE.finditer(text):
            local = text[max(0, match.start() - 90) : match.end() + 40]
            if not re.search(
                r"\b(?:pay|payment|payable|paid|invoice|remit|settlement|compensation|fee|fees|net)\b",
                local,
                re.IGNORECASE,
            ):
                continue
            parsed = parse_days_match(match)
            if parsed is not None:
                candidates.append(parsed)
        for match in self.PAYMENT_NET.finditer(text):
            try:
                candidates.append((int(match.group(1)), "calendar"))
            except (TypeError, ValueError):
                continue
        if not candidates:
            return None
        # Choose the longest stated period; it is the most conservative payment risk.
        return max(candidates, key=lambda item: item[0])

    # ------------------------------------------------------------------ renewal
    def _evaluate_renewal(self, passage: RetrievedPassage, policy: PolicyConfig) -> list[Finding]:
        text = passage.segment.text
        auto_match = self._auto_renewal_match(text)
        if auto_match is None:
            if not self.RENEWAL_CONTEXT.search(text):
                return []
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.NO_CONFLICT_FOUND,
                    passage,
                    None,
                    "A renewal/termination provision was reviewed, but no automatic renewal "
                    "trigger was detected in this provision.",
                    "No action required for automatic renewal in this provision. Confirm the "
                    "rest of the termination mechanics manually.",
                    "renewal.no_auto_trigger",
                    {"rule": "renewal.no_auto_trigger", "auto_renewal": False},
                )
            ]

        notice = self._extract_notice_days(text)
        notice_days = notice[0] if notice else None
        notice_basis = notice[1] if notice else None

        if notice_days is not None and notice_basis == "business":
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.REVIEW_REQUIRED,
                    passage,
                    f"Minimum renewal notice is {policy.min_renewal_notice_days} calendar "
                    f"days; the contract states {notice_days} business days.",
                    f"Automatic renewal is coupled with a {notice_days}-business-day notice "
                    "period. A business-day notice cannot be compared reliably with the "
                    "calendar-day policy without a holiday calendar.",
                    "Negotiate a clearly stated calendar-day notice period and confirm the "
                    "method and deadline for giving notice.",
                    "renewal.business_day_notice",
                    {
                        "rule": "renewal.business_day_notice",
                        "notice_days": notice_days,
                        "notice_basis": "business",
                        "policy_min_notice_days": policy.min_renewal_notice_days,
                    },
                    uncertainty="Business-day count cannot be converted exactly.",
                )
            ]

        if not policy.allow_auto_renewal:
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.HIGH,
                    passage,
                    f"Automatic renewal is not allowed by policy. Contract auto-renews "
                    f"{'with ' + str(notice_days) + ' days notice' if notice_days is not None else 'without a clearly stated notice period'}.",
                    "The contract contains an automatic renewal or evergreen provision. Your "
                    "policy does not allow automatic renewal."
                    + (
                        f" The stated non-renewal notice is {notice_days} days, but the "
                        "automatic renewal trigger itself conflicts with the policy."
                        if notice_days is not None
                        else " No reliable non-renewal notice period was found."
                    ),
                    "Remove automatic renewal or convert it to explicit opt-in renewal. If "
                    "retained, require a written non-renewal notice period of at least "
                    f"{policy.min_renewal_notice_days} days.",
                    "renewal.auto_renewal_prohibited",
                    {
                        "rule": "renewal.auto_renewal_prohibited",
                        "auto_renewal": True,
                        "notice_days": notice_days,
                        "policy_allow_auto_renewal": False,
                        "policy_min_notice_days": policy.min_renewal_notice_days,
                    },
                )
            ]

        if policy.min_renewal_notice_days > 0 and notice_days is None:
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.HIGH,
                    passage,
                    f"Automatic renewal is allowed only with at least "
                    f"{policy.min_renewal_notice_days} days notice; no reliable notice period "
                    "was found.",
                    "Automatic renewal is present, but the text does not provide a reliable "
                    "non-renewal notice period. This leaves no deterministic exit window "
                    "matching your policy.",
                    "Add a written non-renewal notice period of at least "
                    f"{policy.min_renewal_notice_days} days before the renewal date.",
                    "renewal.notice_missing",
                    {
                        "rule": "renewal.notice_missing",
                        "auto_renewal": True,
                        "notice_days": None,
                        "policy_min_notice_days": policy.min_renewal_notice_days,
                    },
                    uncertainty="No numeric notice period could be extracted from this provision.",
                )
            ]

        if notice_days is not None and notice_days < policy.min_renewal_notice_days:
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.HIGH,
                    passage,
                    f"Minimum renewal notice: {policy.min_renewal_notice_days} days; "
                    f"contract provides {notice_days} days.",
                    f"The contract auto-renews unless notice is given {notice_days} days "
                    f"before expiry, which is shorter than your required minimum of "
                    f"{policy.min_renewal_notice_days} days.",
                    f"Negotiate at least {policy.min_renewal_notice_days} days' written "
                    "non-renewal notice, or remove automatic renewal.",
                    "renewal.notice_below_minimum",
                    {
                        "rule": "renewal.notice_below_minimum",
                        "notice_days": notice_days,
                        "policy_min_notice_days": policy.min_renewal_notice_days,
                        "comparison": f"{notice_days} < {policy.min_renewal_notice_days}",
                    },
                )
            ]

        if policy.preferences.flag_auto_renewal_even_if_allowed:
            return [
                self._make(
                    ClauseCategory.RENEWAL_TERMINATION,
                    FindingStatus.LOW,
                    passage,
                    "Preference: flag automatic renewal even when permitted.",
                    f"Automatic renewal is permitted by your hard policy and the "
                    f"{notice_days}-day notice meets the minimum, but your preference requests "
                    "that automatic renewal still be flagged.",
                    "Consider requesting opt-in renewal for additional control.",
                    "renewal.preference_flag",
                    {
                        "rule": "renewal.preference_flag",
                        "notice_days": notice_days,
                        "preference": True,
                    },
                )
            ]

        return [
            self._make(
                ClauseCategory.RENEWAL_TERMINATION,
                FindingStatus.NO_CONFLICT_FOUND,
                passage,
                None,
                f"Automatic renewal is allowed by policy and the {notice_days}-day notice "
                f"period meets the {policy.min_renewal_notice_days}-day minimum.",
                "No action required under the current policy. Calendar the notice deadline.",
                "renewal.within_policy",
                {
                    "rule": "renewal.within_policy",
                    "auto_renewal": True,
                    "notice_days": notice_days,
                    "policy_min_notice_days": policy.min_renewal_notice_days,
                    "comparison": f"{notice_days} >= {policy.min_renewal_notice_days}",
                },
            )
        ]

    def _auto_renewal_match(self, text: str) -> Optional[re.Match[str]]:
        for match in self.AUTO_RENEW.finditer(text):
            prefix = text[max(0, match.start() - 45) : match.start()].casefold()
            if re.search(r"\b(?:not|no|never|shall\s+not|will\s+not|does\s+not)\b", prefix):
                continue
            return match
        return None

    def _extract_notice_days(self, text: str) -> Optional[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        for match in _DAYS_RE.finditer(text):
            before = text[max(0, match.start() - 180) : match.start()].casefold()
            after = text[match.end() : match.end() + 100].casefold()
            context = before + " " + after
            if not re.search(
                r"\b(?:notice|notify|notification|non-renewal|renewal|renew|terminat|expir)\w*",
                context,
            ):
                continue
            if not re.search(
                r"\b(?:prior to|before|ahead of|in advance of|expiration|expiry|renewal|terminate|termination)\b",
                context,
            ):
                continue
            parsed = parse_days_match(match)
            if parsed is not None:
                candidates.append(parsed)
        if not candidates:
            return None
        # The shortest stated non-renewal window is the most restrictive one and
        # therefore the safest comparison against a minimum-notice policy.
        return min(candidates, key=lambda item: item[0])

    # ---------------------------------------------------------------- liability
    def _evaluate_liability(self, passage: RetrievedPassage, policy: PolicyConfig) -> list[Finding]:
        text = passage.segment.text
        uncapped = self.UNCAPPED.search(text)
        capped = self.CAPPED.search(text)
        indemnity = self.INDEMNITY.search(text)
        findings: list[Finding] = []

        if uncapped:
            carveout = bool(self.CARVEOUT.search(text))
            if not policy.liability_must_be_capped:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.NO_CONFLICT_FOUND,
                        passage,
                        None,
                        "The contract contains uncapped liability wording, but your policy "
                        "does not require a liability cap.",
                        "No action required under the current policy; consider seeking legal "
                        "advice because uncapped exposure may still be commercially material.",
                        "liability.uncapped_allowed",
                        {"rule": "liability.uncapped_allowed", "uncapped": True},
                    )
                )
            elif carveout:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        "Liability must be capped; an uncapped phrase appears alongside "
                        "carve-outs or exceptions.",
                        "Uncapped or unlimited liability wording is present, but the same "
                        "provision includes carve-outs or exceptions. The interaction needs "
                        "contextual legal review before confirming a cap conflict.",
                        "Ask counsel to confirm whether the carve-outs make aggregate "
                        "liability uncapped, and negotiate a mutual cap if needed.",
                        "liability.uncapped_with_carveout",
                        {
                            "rule": "liability.uncapped_with_carveout",
                            "uncapped": True,
                            "carveout": True,
                            "policy_liability_must_be_capped": True,
                        },
                        uncertainty=(
                            "Carve-outs can limit the effect of the uncapped wording; the "
                            "deterministic rule cannot resolve that interaction."
                        ),
                    )
                )
            else:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.HIGH,
                        passage,
                        "Liability must be capped by policy; the contract expressly states "
                        "uncapped or unlimited liability.",
                        "The provision expressly makes liability uncapped or unlimited. "
                        "Your policy requires a liability cap, so this is a direct hard-"
                        "constraint conflict.",
                        "Negotiate a mutual aggregate liability cap (for example, fees paid in "
                        "the prior 12 months) with narrow, mutual carve-outs. Seek legal advice "
                        "before signing.",
                        "liability.uncapped_prohibited",
                        {
                            "rule": "liability.uncapped_prohibited",
                            "uncapped": True,
                            "policy_liability_must_be_capped": True,
                        },
                    )
                )
        elif capped:
            if policy.liability_must_be_capped:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.NO_CONFLICT_FOUND,
                        passage,
                        None,
                        "A limitation or cap on liability was identified, consistent with your "
                        "policy requiring a cap.",
                        "No action required under the current policy. Confirm the cap amount and "
                        "carve-outs with counsel.",
                        "liability.cap_present",
                        {"rule": "liability.cap_present", "capped": True},
                    )
                )
            else:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.NO_CONFLICT_FOUND,
                        passage,
                        None,
                        "A liability cap was identified; your policy does not require one.",
                        "No action required under the current policy.",
                        "liability.cap_not_required",
                        {"rule": "liability.cap_not_required", "capped": True},
                    )
                )

        if indemnity:
            mutual = bool(self.MUTUAL_INDEMNITY.search(text))
            if policy.preferences.prefer_mutual_indemnity and not mutual:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.LOW,
                        passage,
                        "Preference: mutual indemnification.",
                        "This provision contains one-way indemnity language and does not "
                        "identify a mutual or reciprocal obligation. Your preference is for "
                        "mutual indemnity.",
                        "Consider requesting a mutual indemnity or narrowing the one-way "
                        "indemnity to third-party claims caused by the indemnifying party.",
                        "liability.one_way_indemnity",
                        {
                            "rule": "liability.one_way_indemnity",
                            "indemnity": True,
                            "mutual": False,
                            "preference": True,
                        },
                    )
                )
            if policy.liability_must_be_capped and not uncapped and not capped:
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        "Liability must be capped; this indemnity provision does not state "
                        "whether aggregate liability is capped.",
                        "An indemnity obligation is present, but no aggregate liability cap or "
                        "express uncapped statement was identified in this provision. The cap "
                        "may appear elsewhere, so this cannot be treated as a confirmed conflict.",
                        "Locate the limitation of liability section and confirm whether it caps "
                        "the indemnity. If not, negotiate a cap.",
                        "liability.indemnity_cap_unknown",
                        {
                            "rule": "liability.indemnity_cap_unknown",
                            "indemnity": True,
                            "capped": False,
                            "uncapped": False,
                            "policy_liability_must_be_capped": True,
                        },
                        uncertainty="The cap may be in another section not retrieved with this "
                        "segment.",
                    )
                )
        elif not uncapped and not capped:
            # Liability keyword matched but no determinable cap status.
            if re.search(r"\b(?:liability|liable|damages)\b", text, re.IGNORECASE):
                findings.append(
                    self._make(
                        ClauseCategory.LIABILITY_INDEMNITY,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        "Liability must be capped; no cap or uncapped term was identified.",
                        "The provision discusses liability or damages, but the deterministic "
                        "rules could not identify a cap, an uncapped statement, or an indemnity "
                        "obligation. Interpretive review is required.",
                        "Ask counsel to confirm the liability position and whether a cap applies.",
                        "liability.cap_unknown",
                        {
                            "rule": "liability.cap_unknown",
                            "policy_liability_must_be_capped": policy.liability_must_be_capped,
                        },
                        uncertainty="The rule could not classify the liability provision.",
                    )
                )
        return findings

    # -------------------------------------------------------------- jurisdiction
    def _evaluate_jurisdiction(self, passage: RetrievedPassage, policy: PolicyConfig) -> list[Finding]:
        text = passage.segment.text
        found = self._extract_forums(text)
        exclusive = bool(re.search(r"\bexclusive\s+jurisdiction\b", text, re.IGNORECASE))

        if not found:
            if self.AMBIGUOUS_FORUM.search(text) or self.JURISDICTION_SIGNAL.search(text):
                return [
                    self._make(
                        ClauseCategory.JURISDICTION_DISPUTE,
                        FindingStatus.REVIEW_REQUIRED,
                        passage,
                        f"Approved jurisdictions: {', '.join(policy.approved_jurisdictions)}; "
                        "the contract does not identify a determinable forum.",
                        "The clause discusses governing law, jurisdiction, or dispute "
                        "resolution, but no determinable governing law or forum could be "
                        "extracted. The provision may defer the choice to a future agreement.",
                        "Ask the counterparty to name the governing law and exclusive forum in "
                        "writing, and confirm it is on your approved list.",
                        "jurisdiction.ambiguous_forum",
                        {
                            "rule": "jurisdiction.ambiguous_forum",
                            "approved_jurisdictions": policy.approved_jurisdictions,
                            "exclusive": exclusive,
                        },
                        uncertainty="No reliable jurisdiction value was extracted.",
                    )
                ]
            return []

        findings: list[Finding] = []
        for forum in found:
            approved = self._is_approved_jurisdiction(forum, policy.approved_jurisdictions)
            if approved and exclusive and not policy.allow_exclusive_jurisdiction:
                findings.append(
                    self._make(
                        ClauseCategory.JURISDICTION_DISPUTE,
                        FindingStatus.HIGH,
                        passage,
                        "Exclusive jurisdiction is not allowed by policy.",
                        f"The contract selects the exclusive jurisdiction of {forum}. Your "
                        "policy does not permit exclusive jurisdiction clauses.",
                        "Negotiate a non-exclusive forum or remove exclusivity.",
                        "jurisdiction.exclusive_prohibited",
                        {
                            "rule": "jurisdiction.exclusive_prohibited",
                            "found_forum": forum,
                            "exclusive": True,
                            "policy_allow_exclusive": False,
                        },
                    )
                )
            elif not approved:
                findings.append(
                    self._make(
                        ClauseCategory.JURISDICTION_DISPUTE,
                        FindingStatus.HIGH if exclusive else FindingStatus.MEDIUM,
                        passage,
                        f"Approved jurisdictions: {', '.join(policy.approved_jurisdictions)}; "
                        f"contract selects {forum}"
                        f"{' on an exclusive basis' if exclusive else ''}.",
                        f"The governing law or forum is {forum}, which is not on your approved "
                        f"list ({', '.join(policy.approved_jurisdictions)})."
                        + (
                            " The clause makes that forum exclusive."
                            if exclusive
                            else " The clause does not state that the forum is exclusive."
                        ),
                        "Negotiate governing law and venue in an approved jurisdiction, or "
                        "obtain legal advice about enforceability and local litigation risk.",
                        "jurisdiction.not_approved",
                        {
                            "rule": "jurisdiction.not_approved",
                            "found_forum": forum,
                            "approved_jurisdictions": policy.approved_jurisdictions,
                            "exclusive": exclusive,
                        },
                    )
                )
            else:
                findings.append(
                    self._make(
                        ClauseCategory.JURISDICTION_DISPUTE,
                        FindingStatus.NO_CONFLICT_FOUND,
                        passage,
                        None,
                        f"The selected forum ({forum}) is on your approved list and the "
                        "exclusivity setting is permitted by policy.",
                        "No action required under the current policy.",
                        "jurisdiction.approved",
                        {
                            "rule": "jurisdiction.approved",
                            "found_forum": forum,
                            "approved_jurisdictions": policy.approved_jurisdictions,
                            "exclusive": exclusive,
                        },
                    )
                )
        return findings

    def _extract_forums(self, text: str) -> list[str]:
        forums: list[str] = []
        for _, pattern in self.FORUM_PATTERNS:
            for match in pattern.finditer(text):
                value = self._clean_jurisdiction(match.group(1))
                if value and value.casefold() not in {item.casefold() for item in forums}:
                    forums.append(value)
        return forums

    @staticmethod
    def _clean_jurisdiction(value: str) -> str:
        value = normalize_whitespace(value)
        value = re.sub(r"^(?:the\s+)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^(?:state\s+of\s+)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+(?:and|without|for)$", "", value, flags=re.IGNORECASE)
        return value.strip(" .,;:'\"()")

    @staticmethod
    def _is_approved_jurisdiction(found: str, approved: list[str]) -> bool:
        found_norm = normalize_for_match(found)
        for item in approved:
            item_norm = normalize_for_match(item)
            if not item_norm:
                continue
            if found_norm == item_norm or found_norm in item_norm or item_norm in found_norm:
                return True
        return False

    # ------------------------------------------------------------------ helpers
    def _make(
        self,
        category: ClauseCategory,
        status: FindingStatus,
        passage: RetrievedPassage,
        policy_violated: Optional[str],
        explanation: str,
        recommended_action: str,
        rule_id: str,
        raw_evidence: dict[str, Any],
        uncertainty: Optional[str] = None,
    ) -> Finding:
        segment = passage.segment
        raw = {
            **raw_evidence,
            "rule_version": self.RULE_VERSION,
            "clause_id": segment.clause_id,
            "retrieval_score": passage.score,
            "matched_terms": passage.matched_terms,
        }
        return Finding(
            finding_id="PENDING",
            clause_category=category,
            risk_level=STATUS_TO_RISK[status],
            status=status,
            exact_quote=segment.text,
            source_id=segment.source_id,
            page_number=segment.page_number,
            paragraph_index=segment.paragraph_index,
            span_start=segment.span_start,
            span_end=segment.span_end,
            policy_violated=policy_violated,
            explanation=explanation,
            recommended_action=recommended_action,
            verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            confidence=None,
            evidence_quality="pending_verification",
            uncertainty_reason=uncertainty,
            rule_id=rule_id,
            is_ai_generated=False,
            policy_support_verified=False,
            raw_evidence=raw,
        )

    @staticmethod
    def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
        seen: set[tuple[str, str, str, str]] = set()
        result: list[Finding] = []
        for finding in findings:
            key = (
                finding.clause_category.value,
                finding.source_id,
                finding.status.value,
                normalize_for_match(finding.exact_quote)[:180],
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(finding)
        return result
