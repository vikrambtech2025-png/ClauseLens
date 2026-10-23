"""JSON/PDF reports and separation of human review from AI findings."""

from __future__ import annotations

import json

import fitz

from core.report_generator import ReportGenerator
from models.schemas import ReviewDecision, utc_now_iso
from tests.helpers import run_sample
from ui.common import apply_reviews


def test_json_report_contains_evidence_policy_and_limitations(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    generator = ReportGenerator()
    payload = generator.to_payload(result)

    assert payload["report_type"] == "ClauseLens contract risk assessment"
    assert payload["document"]["filename"] == "demo_contract.docx"
    assert "extracted_text" not in payload["document"]
    assert payload["document"]["text_included"] is False
    assert payload["policy"]["hard_constraints"]["max_payment_days"] == 30
    assert payload["summary"]["confirmed_conflicts"] == 4
    assert len(payload["findings"]) == 7
    assert payload["findings"][0]["evidence"]["exact_quote"]
    assert payload["findings"][0]["evidence"]["verification_detail"]
    assert payload["warnings"] == result.warnings
    assert payload["limitations"]
    assert payload["verification_method"]["confidence"].startswith("No numeric confidence")
    assert "not legal advice" in payload["disclaimer"].lower()
    assert payload["review"]["decisions"]["accept"] == 0


def test_contract_text_is_opt_in(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    payload = ReportGenerator().to_payload(result, include_contract_text=True)
    assert payload["document"]["text_included"] is True
    assert payload["document"]["extracted_text"] == result.document.raw_text
    assert len(payload["document"]["extracted_text"]) > 500


def test_review_decisions_are_applied_to_a_copy(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    target = result.findings[0]
    assert target.review_decision is None
    store = {
        target.finding_id: {
            "decision": ReviewDecision.ACCEPT.value,
            "notes": "Request a 30-day payment window.",
            "reviewer": "Judge Demo",
            "reviewed_at": utc_now_iso(),
        }
    }

    reviewed = apply_reviews(result, store)
    assert result.findings[0].review_decision is None
    assert result.findings[0].review_notes == ""
    assert reviewed.findings[0].review_decision == ReviewDecision.ACCEPT
    assert reviewed.findings[0].review_notes == "Request a 30-day payment window."
    assert reviewed.findings[0].reviewer == "Judge Demo"

    payload = ReportGenerator().to_payload(reviewed)
    assert payload["review"]["reviewed_findings"] == 1
    assert payload["review"]["decisions"]["accept"] == 1
    assert payload["findings"][0]["review_decision"] == "accept"


def test_json_round_trip_and_unicode(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    encoded = ReportGenerator().to_json(result)
    payload = json.loads(encoded)
    assert payload["document"]["filename"] == "demo_contract.docx"
    assert payload["summary"]["high"] == 4
    # ensure_ascii=False keeps symbols readable; the generated document should not contain mojibake.
    assert "\ufffd" not in encoded


def test_pdf_report_is_generated_and_readable(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    pdf_bytes = ReportGenerator().to_pdf_bytes(result)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2000

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert document.page_count >= 2
        text = "\n".join(page.get_text() for page in document)
        assert "ClauseLens Contract Risk Report" in text
        assert "not legal advice" in text.lower()
        assert "Payment terms" in text
        assert "F0001" in text
    finally:
        document.close()


def test_report_filename_is_safe(pipeline):
    result = run_sample(pipeline, "demo_contract.docx")
    name = ReportGenerator.report_filename(result, "json")
    assert name.startswith("clauselens_demo_contract")
    assert name.endswith(".json")
    assert " " not in name
    assert ReportGenerator.report_filename(result, "pdf").endswith(".pdf")


def test_injection_warnings_survive_export(pipeline):
    result = run_sample(pipeline, "prompt_injection_contract.docx")
    payload = ReportGenerator().to_payload(result)
    assert any("instruction" in warning.lower() for warning in payload["warnings"])
    assert payload["summary"]["not_verified"] == 1
    assert "extracted_text" not in payload["document"]
