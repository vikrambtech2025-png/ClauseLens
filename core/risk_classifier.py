"""Summary and severity aggregation.

Severity is a product heuristic. It is always derived from explicit statuses and
never presented as a legal conclusion or a fabricated confidence score.
"""

from __future__ import annotations

from models.schemas import (
    AnalysisSummary,
    CategoryRetrieval,
    ClauseCategory,
    Document,
    Finding,
    FindingStatus,
    VerificationStatus,
)

_STATUS_KEYS = {
    FindingStatus.HIGH: "high",
    FindingStatus.MEDIUM: "medium",
    FindingStatus.LOW: "low",
    FindingStatus.REVIEW_REQUIRED: "review_required",
    FindingStatus.NO_CONFLICT_FOUND: "no_conflict_found",
    FindingStatus.NOT_FOUND: "not_found",
    FindingStatus.NOT_VERIFIED: "not_verified",
}


class RiskClassifier:
    def classify(
        self,
        findings: list[Finding],
        retrieval: dict[str, CategoryRetrieval] | None = None,
        document: Document | None = None,
    ) -> AnalysisSummary:
        retrieval = retrieval or {}
        summary = AnalysisSummary(total_findings=len(findings))
        for finding in findings:
            key = _STATUS_KEYS[finding.status]
            setattr(summary, key, getattr(summary, key) + 1)
            if finding.status in {
                FindingStatus.REVIEW_REQUIRED,
                FindingStatus.NOT_FOUND,
                FindingStatus.NOT_VERIFIED,
            }:
                summary.unresolved_finding_ids.append(finding.finding_id)
            if finding.has_evidence:
                summary.evidence_bearing_findings += 1
                if finding.verification_status == VerificationStatus.VERIFIED:
                    summary.verified_evidence += 1

        # A LOW preference flag is advisory, not a confirmed conflict. Only hard
        # and medium statuses are counted here so the headline number is honest.
        summary.confirmed_conflicts = summary.high + summary.medium
        if summary.evidence_bearing_findings:
            summary.verification_rate = round(
                summary.verified_evidence / summary.evidence_bearing_findings, 4
            )
        else:
            summary.verification_rate = None

        clause_ids: set[str] = set()
        for finding in findings:
            clause_id = finding.raw_evidence.get("clause_id")
            if clause_id:
                clause_ids.add(str(clause_id))
            elif finding.source_id:
                clause_ids.add(finding.source_id)
        summary.clauses_reviewed = len(clause_ids)

        summary.retrieval_candidates = sum(
            len(result.passages) for result in retrieval.values()
        )
        summary.categories_retrieved = sum(
            1 for result in retrieval.values() if result.has_signal or result.passages
        )
        if document is not None:
            summary.extraction_coverage = document.stats.extraction_coverage

        for category in ClauseCategory:
            category_findings = [f for f in findings if f.clause_category == category]
            counts = {
                "total": len(category_findings),
                "high": 0,
                "medium": 0,
                "low": 0,
                "review_required": 0,
                "no_conflict_found": 0,
                "not_found": 0,
                "not_verified": 0,
                "verified": 0,
            }
            detected = False
            for finding in category_findings:
                counts[_STATUS_KEYS[finding.status]] += 1
                if finding.verification_status == VerificationStatus.VERIFIED and finding.has_evidence:
                    counts["verified"] += 1
                if (
                    finding.has_evidence
                    and finding.status
                    not in {FindingStatus.NOT_FOUND, FindingStatus.NOT_VERIFIED}
                ):
                    detected = True
            if detected:
                summary.categories_detected += 1
            summary.by_category[category.value] = counts

        return summary
