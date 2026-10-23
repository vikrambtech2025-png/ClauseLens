"""Readable JSON and PDF risk reports with source evidence and review state."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from core.text_utils import safe_filename, safe_pdf_text
from models.schemas import AnalysisResult, FindingStatus, ReviewDecision, utc_now_iso


class ReportGenerationError(RuntimeError):
    """Raised when a report cannot be generated."""


class ReportGenerator:
    def to_payload(
        self,
        result: AnalysisResult,
        include_contract_text: bool = False,
        generated_by: str = "ClauseLens",
    ) -> dict[str, Any]:
        decision_counts = {decision.value: 0 for decision in ReviewDecision}
        reviewed = 0
        for finding in result.findings:
            if finding.reviewed_at:
                reviewed += 1
            if finding.review_decision:
                decision_counts[finding.review_decision.value] += 1

        findings_payload: list[dict[str, Any]] = []
        for finding in result.findings:
            payload = finding.model_dump(mode="json")
            payload["source_location"] = {
                "source_id": finding.source_id or None,
                "page_number": finding.page_number,
                "paragraph_index": finding.paragraph_index,
                "span_start": finding.span_start,
                "span_end": finding.span_end,
            }
            payload["evidence"] = {
                "exact_quote": finding.exact_quote or None,
                "verification_status": finding.verification_status.value,
                "evidence_quality": finding.evidence_quality,
                "policy_support_verified": finding.policy_support_verified,
                "uncertainty_reason": finding.uncertainty_reason,
                "verification_detail": finding.raw_evidence.get("verification"),
            }
            payload.pop("raw_evidence", None)
            findings_payload.append(payload)

        unresolved = [
            {
                "finding_id": finding.finding_id,
                "category": finding.clause_category.value,
                "status": finding.status.value,
                "reason": finding.uncertainty_reason or finding.explanation,
            }
            for finding in result.findings
            if finding.status
            in {
                FindingStatus.REVIEW_REQUIRED,
                FindingStatus.NOT_FOUND,
                FindingStatus.NOT_VERIFIED,
            }
        ]

        payload: dict[str, Any] = {
            "schema_version": result.schema_version,
            "report_type": "ClauseLens contract risk assessment",
            "generated_at": utc_now_iso(),
            "generated_by": generated_by,
            "disclaimer": (
                "Decision support only. This report is not legal advice and does not "
                "replace review by a qualified lawyer."
            ),
            "analysis_mode": result.analysis_mode,
            "processing_ms": result.processing_ms,
            "document": {
                "filename": result.document.filename,
                "file_type": result.document.file_type,
                "sha256": result.document.sha256,
                "stats": result.document.stats.model_dump(mode="json"),
                "text_included": include_contract_text,
            },
            "policy": {
                "hard_constraints": result.policy.hard_constraint_snapshot(),
                "preferences": result.policy.preferences_snapshot(),
            },
            "summary": result.summary.model_dump(mode="json"),
            "findings": findings_payload,
            "review": {
                "reviewed_findings": reviewed,
                "total_findings": len(result.findings),
                "decisions": decision_counts,
                "note": "Review decisions are recorded by the user and are separate from "
                "AI-generated or deterministic findings.",
            },
            "unresolved": unresolved,
            "warnings": result.warnings,
            "limitations": result.limitations,
            "verification_method": {
                "quote": "Exact or whitespace-normalized match against the original extracted "
                "source span; unsupported quotations are rejected.",
                "location": "Source ID must exist in the extraction map and page/paragraph "
                "metadata must agree with that map.",
                "policy": "Confirmed rules independently recompute their numeric or categorical "
                "comparison. AI-assisted findings are never confirmed conflicts.",
                "confidence": "No numeric confidence score is fabricated. Evidence quality is a "
                "categorical grade derived from quote, location, and policy verification.",
            },
            "llm_status": result.llm_status.model_dump(mode="json"),
        }
        if include_contract_text:
            payload["document"]["extracted_text"] = result.document.raw_text
        return payload

    def to_json(self, result: AnalysisResult, include_contract_text: bool = False) -> str:
        try:
            return json.dumps(
                self.to_payload(result, include_contract_text=include_contract_text),
                indent=2,
                ensure_ascii=False,
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise ReportGenerationError(f"JSON report generation failed: {exc}") from exc

    def to_pdf_bytes(self, result: AnalysisResult) -> bytes:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.platypus import (
                KeepTogether,
                PageBreak,
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except Exception as exc:  # pragma: no cover
            raise ReportGenerationError("ReportLab is not available.") from exc

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title="ClauseLens Risk Report",
            author="ClauseLens",
        )
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "Body",
            parent=styles["BodyText"],
            fontSize=9,
            leading=12,
            alignment=TA_LEFT,
            spaceAfter=4,
        )
        small = ParagraphStyle(
            "Small",
            parent=body,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#444444"),
        )
        quote_style = ParagraphStyle(
            "Quote",
            parent=body,
            fontName="Courier",
            fontSize=8,
            leading=10,
            leftIndent=10,
            rightIndent=10,
            borderColor=colors.HexColor("#cbd5e1"),
            borderWidth=0.6,
            borderPadding=5,
            backColor=colors.HexColor("#f8fafc"),
            spaceBefore=4,
            spaceAfter=6,
        )
        heading = ParagraphStyle(
            "Heading",
            parent=styles["Heading2"],
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#0f172a"),
        )

        def text(value: Any) -> str:
            return escape(safe_pdf_text(str(value if value is not None else "")))

        def para(value: Any, style=body) -> Paragraph:
            return Paragraph(text(value), style)

        story: list[Any] = []
        story.append(Paragraph("ClauseLens Contract Risk Report", styles["Title"]))
        story.append(
            para(
                "Decision support only — not legal advice. Severity is a product heuristic.",
                small,
            )
        )
        story.append(Spacer(1, 6))
        metadata = [
            ["Contract", result.document.filename],
            ["File type", result.document.file_type.upper()],
            ["Generated", result.generated_at],
            ["Analysis mode", result.analysis_mode],
            ["Processing time", f"{result.processing_ms:.0f} ms"],
            ["SHA-256", result.document.sha256 or "not available"],
        ]
        table = Table(metadata, colWidths=[35 * mm, 135 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 10))

        summary = result.summary
        story.append(Paragraph("Summary", heading))
        summary_rows = [
            ["Total findings", str(summary.total_findings)],
            ["High", str(summary.high)],
            ["Medium", str(summary.medium)],
            ["Low", str(summary.low)],
            ["Review required", str(summary.review_required)],
            ["No conflict found", str(summary.no_conflict_found)],
            ["Not found", str(summary.not_found)],
            ["Not verified", str(summary.not_verified)],
            ["Clauses reviewed", str(summary.clauses_reviewed)],
            ["Categories detected", f"{summary.categories_detected}/{summary.categories_total}"],
            ["Evidence verification", (
                f"{summary.verified_evidence}/{summary.evidence_bearing_findings} "
                f"({(summary.verification_rate or 0) * 100:.0f}%)"
            )],
            ["Extraction coverage", f"{summary.extraction_coverage * 100:.0f}%"],
        ]
        summary_table = Table(summary_rows, colWidths=[67 * mm, 103 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(summary_table)

        if result.warnings:
            story.append(Paragraph("Extraction and processing warnings", heading))
            for warning in result.warnings:
                story.append(para(f"• {warning}", body))

        story.append(PageBreak())
        story.append(Paragraph("Findings and evidence", heading))
        for finding in result.findings:
            block: list[Any] = []
            block.append(
                para(
                    f"{finding.finding_id} · {finding.clause_category.label} · "
                    f"{finding.status.value} · {finding.risk_level.value}",
                    styles["Heading3"],
                )
            )
            block.append(
                para(
                    f"Verification: {finding.verification_status.value} | "
                    f"Evidence: {finding.evidence_quality}",
                    small,
                )
            )
            block.append(
                para(
                    f"Source: {finding.source_id or '—'} | Page: "
                    f"{finding.page_number if finding.page_number is not None else '—'} | "
                    f"Paragraph: "
                    f"{finding.paragraph_index if finding.paragraph_index is not None else '—'}",
                    small,
                )
            )
            if finding.exact_quote:
                block.append(para(f"“{finding.exact_quote}”", quote_style))
            else:
                block.append(para("No verified quotation is available for this item.", body))
            block.append(para(f"Policy: {finding.policy_violated or 'No policy conflict recorded'}", body))
            block.append(para(f"Explanation: {finding.explanation}", body))
            block.append(para(f"Recommended action: {finding.recommended_action}", body))
            if finding.uncertainty_reason:
                block.append(para(f"Uncertainty: {finding.uncertainty_reason}", small))
            if finding.review_decision:
                block.append(
                    para(
                        f"User review: {finding.review_decision.value}"
                        f"{' · ' + finding.reviewer if finding.reviewer else ''}"
                        f"{' · ' + finding.reviewed_at if finding.reviewed_at else ''}",
                        small,
                    )
                )
                if finding.review_notes:
                    block.append(para(f"Review notes: {finding.review_notes}", small))
            block.append(Spacer(1, 8))
            story.append(KeepTogether(block))

        if result.summary.unresolved_finding_ids:
            story.append(Paragraph("Unresolved items", heading))
            for finding_id in result.summary.unresolved_finding_ids:
                finding = result.finding_by_id(finding_id)
                if not finding:
                    continue
                story.append(
                    para(
                        f"{finding.finding_id} · {finding.clause_category.value} · "
                        f"{finding.status.value} · {finding.uncertainty_reason or finding.explanation}",
                        body,
                    )
                )

        story.append(Paragraph("Limitations", heading))
        for limitation in result.limitations:
            story.append(para(f"• {limitation}", body))
        story.append(
            para(
                "Review decisions in this report are user input, separate from AI-generated "
                "or deterministic findings. Verify all critical terms with qualified counsel.",
                small,
            )
        )

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawString(
                16 * mm,
                9 * mm,
                "ClauseLens · Decision support only · Not legal advice",
            )
            canvas.drawRightString(
                A4[0] - 16 * mm,
                9 * mm,
                f"Page {doc.page}",
            )
            canvas.restoreState()

        try:
            document.build(story, onFirstPage=footer, onLaterPages=footer)
        except Exception as exc:  # pragma: no cover - layout/runtime dependent
            raise ReportGenerationError(f"PDF report generation failed: {exc}") from exc
        return buffer.getvalue()

    @staticmethod
    def report_filename(result: AnalysisResult, extension: str) -> str:
        stem = safe_filename(result.document.filename.rsplit(".", 1)[0], "contract")
        return f"clauselens_{stem}_risk_report.{extension.lstrip('.')}"
