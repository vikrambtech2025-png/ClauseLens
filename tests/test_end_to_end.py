"""End-to-end synthetic contract benchmark and explicit status behavior."""

from __future__ import annotations

import pytest

from models.schemas import ClauseCategory, FindingStatus, PolicyConfig, VerificationStatus
from tests.helpers import evidence_statuses, run_sample, rule, status_map

DEMO_EXPECTED = {
    "payment.days_exceed_limit": "HIGH",
    "payment.open_ended_deadline": "REVIEW_REQUIRED",
    "payment.within_limit": "NO_CONFLICT_FOUND",
    "renewal.auto_renewal_prohibited": "HIGH",
    "liability.uncapped_prohibited": "HIGH",
    "jurisdiction.not_approved": "HIGH",
    "jurisdiction.ambiguous_forum": "REVIEW_REQUIRED",
}


def test_demo_docx_full_expected_result(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    assert status_map(result) == DEMO_EXPECTED
    assert result.summary.total_findings == 7
    assert result.summary.high == 4
    assert result.summary.review_required == 2
    assert result.summary.no_conflict_found == 1
    assert result.summary.confirmed_conflicts == 4
    assert result.summary.categories_detected == 4
    assert result.summary.categories_retrieved == 4
    assert result.summary.categories_total == 4
    assert result.summary.evidence_bearing_findings == 7
    assert result.summary.verified_evidence == 7
    assert result.summary.verification_rate == 1.0
    assert result.document.stats.extraction_ok is True
    assert result.llm_status.used is False
    assert result.analysis_mode == "deterministic"


def test_demo_pdf_matches_docx_behavior_with_pdf_locations(pipeline):
    result = run_sample(pipeline, "demo_contract.pdf")
    assert status_map(result) == DEMO_EXPECTED
    assert result.summary.confirmed_conflicts == 4
    assert result.summary.verified_evidence == 7
    assert result.document.file_type == "pdf"
    for finding in result.findings:
        if finding.has_evidence:
            assert finding.page_number is not None
            assert finding.paragraph_index is None
            assert result.document.span_by_id(finding.source_id) is not None


def test_missing_category_is_not_found_not_invented(pipeline):
    result = run_sample(pipeline, "demo_missing_category.docx")
    jurisdiction = next(
        finding
        for finding in result.findings
        if finding.clause_category == ClauseCategory.JURISDICTION_DISPUTE
        and finding.rule_id == "coverage.not_found"
    )
    assert jurisdiction.status == FindingStatus.NOT_FOUND
    assert jurisdiction.has_evidence is False
    assert jurisdiction.verification_status == VerificationStatus.INSUFFICIENT_EVIDENCE
    assert result.summary.categories_detected == 3
    assert result.summary.confirmed_conflicts == 0
    assert any(finding.status == FindingStatus.LOW for finding in result.findings)


def test_compliant_contract_produces_no_confirmed_conflicts(pipeline):
    result = run_sample(pipeline, "compliant_contract.docx")
    assert result.summary.confirmed_conflicts == 0
    assert result.summary.high == 0
    assert result.summary.review_required == 0
    assert result.summary.no_conflict_found == 4
    assert result.summary.categories_detected == 4
    assert all(
        finding.verification_status == VerificationStatus.VERIFIED
        for finding in result.findings
    )


def test_prompt_injection_cannot_suppress_a_real_conflict(pipeline):
    result = run_sample(pipeline, "prompt_injection_contract.docx")
    assert any("instruction" in warning.lower() for warning in result.warnings)
    assert rule(result, "liability.uncapped_prohibited").status == FindingStatus.HIGH
    assert result.summary.confirmed_conflicts == 3
    assert result.summary.not_verified == 1
    assert any("quarantined" in limitation.lower() for limitation in result.limitations)
    assert all(
        "ignore all previous instructions" not in finding.exact_quote.lower()
        for finding in result.findings
    )


def test_scanned_pdf_reports_unverified_not_clean(pipeline):
    result = run_sample(pipeline, "scanned_no_text.pdf")
    assert result.document.stats.needs_ocr is True
    assert result.summary.categories_detected == 0
    assert result.summary.not_verified == 4
    assert result.summary.confirmed_conflicts == 0
    assert result.summary.verified_evidence == 0
    assert any("OCR" in limitation for limitation in result.limitations)
    assert all(not finding.has_evidence for finding in result.findings)


def test_policy_changes_change_the_outcome(pipeline):
    wide_open = PolicyConfig(
        max_payment_days=120,
        allow_auto_renewal=True,
        min_renewal_notice_days=30,
        liability_must_be_capped=False,
        approved_jurisdictions=["Singapore"],
        allow_exclusive_jurisdiction=True,
    )
    result = run_sample(pipeline, "demo_contract.docx", wide_open)
    assert result.summary.high == 0
    assert result.summary.confirmed_conflicts == 0
    assert result.summary.review_required == 2
    assert rule(result, "payment.above_preference").status == FindingStatus.LOW
    assert rule(result, "renewal.within_policy").status == FindingStatus.NO_CONFLICT_FOUND
    assert rule(result, "liability.uncapped_allowed").status == FindingStatus.NO_CONFLICT_FOUND
    assert rule(result, "jurisdiction.approved").status == FindingStatus.NO_CONFLICT_FOUND


def test_every_confirmed_finding_has_verified_exact_quote_and_location(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    for finding in result.findings:
        if finding.status in {
            FindingStatus.HIGH,
            FindingStatus.MEDIUM,
            FindingStatus.LOW,
        }:
            assert finding.exact_quote
            assert finding.source_id
            assert finding.verification_status == VerificationStatus.VERIFIED
            span = result.document.span_by_id(finding.source_id)
            assert span is not None
            assert span.text[finding.span_start : finding.span_end].strip() == finding.exact_quote
            location = result.document.location_by_id(finding.source_id)
            assert location is not None
            if span.page_number is not None:
                assert finding.page_number == span.page_number
            else:
                assert finding.paragraph_index == span.paragraph_index
            assert finding.confidence is None
            assert finding.evidence_quality.startswith(("A", "B"))


def test_all_explicit_statuses_are_used_by_the_demo_set(pipeline):
    statuses: set[str] = set()
    for sample in (
        "demo_contract.docx",
        "demo_missing_category.docx",
        "prompt_injection_contract.docx",
        "scanned_no_text.pdf",
    ):
        result = run_sample(pipeline, sample)
        statuses.update(finding.status.value for finding in result.findings)
    assert {
        "HIGH",
        "LOW",
        "REVIEW_REQUIRED",
        "NO_CONFLICT_FOUND",
        "NOT_FOUND",
        "NOT_VERIFIED",
    }.issubset(statuses)


def test_evidence_verification_map_is_complete(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    statuses = evidence_statuses(result)
    assert len(statuses) == 7
    assert set(statuses.values()) == {"VERIFIED"}
