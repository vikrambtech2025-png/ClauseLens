"""Deterministic policy rules and independent evidence verification."""

from __future__ import annotations

from core.document_loader import DocumentLoader
from core.evidence_verifier import EvidenceVerifier
from models.schemas import (
    ClauseCategory,
    Finding,
    FindingStatus,
    PaymentBasis,
    PolicyConfig,
    RiskLevel,
    VerificationStatus,
)
from tests.helpers import rule, run_sample, run_text, sample_path


def payment_text(days: str = "ninety (90) calendar") -> str:
    return f"1. Fees and Payment. The Customer shall pay each invoice within {days} days of the invoice date."


def test_payment_limit_uses_parenthetical_duration(pipeline):
    result = run_text(pipeline, payment_text())
    finding = rule(result, "payment.days_exceed_limit")
    assert finding.status == FindingStatus.HIGH
    assert finding.verification_status == VerificationStatus.VERIFIED
    assert finding.raw_evidence["observed_days"] == 90
    assert "ninety (90)" in finding.exact_quote
    assert finding.policy_support_verified is True


def test_payment_within_limit_and_soft_preference(pipeline):
    within = run_text(pipeline, payment_text("15 calendar"))
    assert rule(within, "payment.within_limit").status == FindingStatus.NO_CONFLICT_FOUND

    above_preference = run_text(pipeline, payment_text("20 calendar"))
    finding = rule(above_preference, "payment.above_preference")
    assert finding.status == FindingStatus.LOW
    assert finding.is_confirmed
    assert finding.verification_status == VerificationStatus.VERIFIED


def test_business_days_are_not_silently_converted(pipeline):
    review = run_text(pipeline, payment_text("45 business"))
    assert rule(review, "payment.business_day_ambiguity").status == FindingStatus.REVIEW_REQUIRED

    within = run_text(pipeline, payment_text("20 business"))
    assert rule(within, "payment.business_days_within_limit").status == FindingStatus.NO_CONFLICT_FOUND


def test_conditional_payment_is_review_required(pipeline):
    text = (
        "1. Payment. The Customer shall pay each invoice within 90 calendar days after "
        "written acceptance of the deliverables, which acceptance is in the Customer's "
        "sole discretion."
    )
    result = run_text(pipeline, text)
    finding = rule(result, "payment.conditional_deadline")
    assert finding.status == FindingStatus.REVIEW_REQUIRED
    assert finding.verification_status == VerificationStatus.VERIFIED
    assert result.summary.confirmed_conflicts == 0


def test_auto_renewal_and_negation_are_distinguished(pipeline):
    auto = run_text(
        pipeline,
        "3. Term. This Agreement will automatically renew for successive twelve-month terms.",
    )
    assert rule(auto, "renewal.auto_renewal_prohibited").status == FindingStatus.HIGH

    negated = run_text(
        pipeline,
        "3. Term. This Agreement does not automatically renew and expires at the end of the initial term.",
    )
    finding = rule(negated, "renewal.no_auto_trigger")
    assert finding.status == FindingStatus.NO_CONFLICT_FOUND
    assert finding.verification_status == VerificationStatus.VERIFIED


def test_renewal_notice_period_is_checked_when_renewal_is_allowed(pipeline):
    text = (
        "3. Term. This Agreement will automatically renew for successive twelve-month "
        "terms unless either party gives written notice of non-renewal at least sixty (60) days before expiration."
    )
    policy = PolicyConfig(allow_auto_renewal=True, min_renewal_notice_days=30)
    within = run_text(pipeline, text, policy)
    assert rule(within, "renewal.within_policy").status == FindingStatus.NO_CONFLICT_FOUND

    strict = PolicyConfig(allow_auto_renewal=True, min_renewal_notice_days=90)
    below = run_text(pipeline, text, strict)
    finding = rule(below, "renewal.notice_below_minimum")
    assert finding.status == FindingStatus.HIGH
    assert finding.raw_evidence["notice_days"] == 60


def test_liability_cap_uncapped_and_carveout(pipeline):
    capped = run_text(
        pipeline,
        "4. Limitation of Liability. The Supplier's total liability shall not exceed the fees paid in the preceding twelve months.",
    )
    assert rule(capped, "liability.cap_present").status == FindingStatus.NO_CONFLICT_FOUND

    uncapped = run_text(
        pipeline,
        "4. Limitation of Liability. The Supplier's liability shall be uncapped for all claims.",
    )
    assert rule(uncapped, "liability.uncapped_prohibited").status == FindingStatus.HIGH

    carveout = run_text(
        pipeline,
        "4. Limitation of Liability. The Supplier's liability shall be uncapped, except for fraud and gross negligence.",
    )
    assert rule(carveout, "liability.uncapped_with_carveout").status == FindingStatus.REVIEW_REQUIRED


def test_jurisdiction_approval_exclusivity_and_ambiguity(pipeline):
    approved = run_text(
        pipeline,
        "5. Governing Law. This Agreement is governed by the laws of the State of Delaware, "
        "and the parties submit to the exclusive jurisdiction of the courts of Delaware.",
    )
    assert rule(approved, "jurisdiction.approved").status == FindingStatus.NO_CONFLICT_FOUND

    unapproved = run_text(
        pipeline,
        "5. Governing Law. This Agreement is governed by the laws of Singapore, and the "
        "parties submit to the exclusive jurisdiction of the courts of Singapore.",
    )
    finding = rule(unapproved, "jurisdiction.not_approved")
    assert finding.status == FindingStatus.HIGH
    assert "Singapore" in finding.raw_evidence.get("found_forum", "")

    ambiguous = run_text(
        pipeline,
        "5. Dispute Resolution. Any dispute arising out of this Agreement shall be resolved "
        "through a dispute resolution procedure to be agreed between the parties.",
    )
    assert rule(ambiguous, "jurisdiction.ambiguous_forum").status == FindingStatus.REVIEW_REQUIRED


def test_exclusive_jurisdiction_policy_is_configurable(pipeline):
    text = (
        "5. Governing Law. This Agreement is governed by the laws of the State of Delaware, "
        "and the parties submit to the exclusive jurisdiction of the courts of Delaware."
    )
    policy = PolicyConfig(allow_exclusive_jurisdiction=False)
    result = run_text(pipeline, text, policy)
    finding = rule(result, "jurisdiction.exclusive_prohibited")
    assert finding.status == FindingStatus.HIGH
    assert finding.verification_status == VerificationStatus.VERIFIED


def test_undisputed_is_not_a_jurisdiction_keyword(pipeline):
    result = run_text(
        pipeline,
        "1. Payment. The Customer shall pay undisputed invoices within twenty (20) calendar days.",
    )
    jurisdiction = result.retrieval[ClauseCategory.JURISDICTION_DISPUTE]
    assert jurisdiction.passages == []
    coverage = next(
        finding
        for finding in result.findings
        if finding.rule_id == "coverage.not_found"
        and finding.clause_category == ClauseCategory.JURISDICTION_DISPUTE
    )
    assert coverage.status == FindingStatus.NOT_FOUND


def test_prompt_injection_is_quarantined_and_surfaced(pipeline):
    result = run_sample(pipeline, "prompt_injection_contract.docx")
    assert any("instruction" in warning.lower() for warning in result.warnings)
    assert rule(result, "liability.uncapped_prohibited").status == FindingStatus.HIGH
    assert all(
        "ignore all previous instructions" not in finding.exact_quote.lower()
        for finding in result.findings
    )


def _document_with(text: str):
    return DocumentLoader().load_text(text)


def _finding(document, quote: str, **overrides) -> Finding:
    span = document.spans[0]
    values = dict(
        finding_id="T0001",
        clause_category=ClauseCategory.PAYMENT,
        risk_level=RiskLevel.HIGH,
        status=FindingStatus.HIGH,
        exact_quote=quote,
        source_id=span.source_id,
        paragraph_index=span.paragraph_index,
        explanation="test finding",
        recommended_action="test action",
    )
    values.update(overrides)
    return Finding(**values)


def test_verifier_canonicalizes_normalized_whitespace():
    text = "Payment   is due within   90 days of invoice."
    document = _document_with(text)
    finding = _finding(
        document,
        "Payment is due within 90 days of invoice.",
        rule_id="payment.days_exceed_limit",
        raw_evidence={"observed_days": 90},
    )
    verified = EvidenceVerifier().verify([finding], document, PolicyConfig())[0]
    assert verified.verification_status == VerificationStatus.VERIFIED
    assert verified.exact_quote == document.spans[0].text
    assert verified.raw_evidence["verification"]["quote_match_type"] == "whitespace_normalized"


def test_verifier_rejects_wrong_quote_offsets_and_location(pipeline):
    text = "Payment shall be made within 30 days of invoice."
    document = _document_with(text)

    wrong_offsets = _finding(document, text, span_start=2, span_end=len(text) - 2)
    failed = EvidenceVerifier().verify([wrong_offsets], document, PolicyConfig())[0]
    assert failed.status == FindingStatus.NOT_VERIFIED
    assert failed.verification_status == VerificationStatus.FAILED_QUOTE_MATCH

    pdf_document = DocumentLoader().load(sample_path("demo_contract.pdf"))
    span = pdf_document.spans[0]
    wrong_page = _finding(
        pdf_document,
        span.text,
        source_id=span.source_id,
        page_number=(span.page_number or 1) + 99,
        paragraph_index=None,
    )
    failed = EvidenceVerifier().verify([wrong_page], pdf_document, PolicyConfig())[0]
    assert failed.verification_status == VerificationStatus.FAILED_LOCATION
    assert failed.status == FindingStatus.NOT_VERIFIED


def test_verifier_rejects_unsupported_policy_comparison():
    text = "Payment shall be made within 10 days of invoice."
    document = _document_with(text)
    finding = _finding(
        document,
        text,
        rule_id="payment.days_exceed_limit",
        raw_evidence={"observed_days": 10},
    )
    failed = EvidenceVerifier().verify([finding], document, PolicyConfig(max_payment_days=30))[0]
    assert failed.status == FindingStatus.NOT_VERIFIED
    assert failed.verification_status == VerificationStatus.FAILED_POLICY_SUPPORT


def test_ai_finding_can_never_be_confirmed():
    text = "The Supplier may suspend the services at any time for any reason."
    document = _document_with(text)
    finding = _finding(
        document,
        text,
        rule_id="ai.suspension",
        status=FindingStatus.HIGH,
        risk_level=RiskLevel.HIGH,
        is_ai_generated=True,
    )
    verified = EvidenceVerifier().verify([finding], document, PolicyConfig())[0]
    assert verified.status == FindingStatus.REVIEW_REQUIRED
    assert verified.verification_status == VerificationStatus.VERIFIED
    assert verified.is_confirmed is False


def test_coverage_finding_without_evidence_stays_not_found():
    document = _document_with("This contract contains no relevant clauses.")
    finding = Finding(
        finding_id="C0001",
        clause_category=ClauseCategory.PAYMENT,
        risk_level=RiskLevel.LOW,
        status=FindingStatus.NOT_FOUND,
        explanation="No payment clause found.",
        recommended_action="Request a payment clause.",
        rule_id="coverage.not_found",
    )
    verified = EvidenceVerifier().verify([finding], document, PolicyConfig())[0]
    assert verified.status == FindingStatus.NOT_FOUND
    assert verified.verification_status == VerificationStatus.INSUFFICIENT_EVIDENCE
    assert verified.has_evidence is False


def test_payment_basis_policy_is_respected(pipeline):
    # A 45-day calendar deadline is a confirmed violation of a 30-day calendar policy.
    # The same policy expressed in business days must become a review item.
    calendar = run_text(pipeline, payment_text("45 calendar"))
    assert rule(calendar, "payment.days_exceed_limit").status == FindingStatus.HIGH

    business = run_text(pipeline, payment_text("45 business"))
    assert rule(business, "payment.business_day_ambiguity").status == FindingStatus.REVIEW_REQUIRED
    assert PaymentBasis.CALENDAR.value == "calendar"
