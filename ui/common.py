"""Shared Streamlit helpers and presentation primitives."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Iterable

import streamlit as st

from core.analysis import AnalysisPipeline
from models.schemas import (
    AnalysisResult,
    ClauseCategory,
    Finding,
    FindingStatus,
    PolicyConfig,
    ReviewDecision,
    VerificationStatus,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

STATUS_COLORS = {
    "HIGH": "#b42318",
    "MEDIUM": "#b54708",
    "LOW": "#475467",
    "REVIEW_REQUIRED": "#6941c6",
    "NO_CONFLICT_FOUND": "#067647",
    "NOT_FOUND": "#475467",
    "NOT_VERIFIED": "#b42318",
}

STATUS_LABELS = {
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
    "REVIEW_REQUIRED": "Review required",
    "NO_CONFLICT_FOUND": "No conflict found",
    "NOT_FOUND": "Not found",
    "NOT_VERIFIED": "Not verified",
}

VERIFICATION_LABELS = {
    "VERIFIED": "Evidence verified",
    "FAILED_QUOTE_MATCH": "Quote failed verification",
    "FAILED_LOCATION": "Location failed verification",
    "FAILED_POLICY_SUPPORT": "Policy support failed",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence",
    "AMBIGUOUS": "Ambiguous evidence",
}

CATEGORY_ICONS = {
    ClauseCategory.PAYMENT: "◷",
    ClauseCategory.RENEWAL_TERMINATION: "↻",
    ClauseCategory.LIABILITY_INDEMNITY: "⚖",
    ClauseCategory.JURISDICTION_DISPUTE: "⌖",
}

UPLOAD_TYPES = ["pdf", "docx"]
UPLOAD_LIMIT_MB = 10


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 2.2rem;
            padding-bottom: 3rem;
            max-width: 1240px;
        }
        h1, h2, h3, h4, h5, h6 {letter-spacing: -0.02em;}
        .cl-brand {font-weight: 800; font-size: 1.55rem; letter-spacing: -0.04em;}
        .cl-kicker {font-size: .72rem; letter-spacing: .14em; text-transform: uppercase; font-weight: 700;}
        .cl-sub {font-size: .95rem; line-height: 1.45;}
        .cl-card {border: 1px solid #000000; border-radius: 14px; padding: 16px 18px; background: #000000; color: #ffffff; height: 100%;}
        .cl-card h4 {margin: 0 0 6px 0; font-size: .85rem; font-weight: 600; letter-spacing: .02em; color: #ffffff !important;}
        .cl-big {font-size: 1.85rem; font-weight: 750; line-height: 1.1;}
        .cl-mini {font-size: .78rem;}
        .cl-badge {display: inline-block; border-radius: 999px; padding: 2px 9px; font-size: .72rem; font-weight: 700; line-height: 1.5; white-space: nowrap; color: #ffffff !important; background-color: #333333 !important; border: 1px solid #ffffff !important;}
        .cl-flag {border-left: 4px solid #e0e0e0; padding: 10px 14px; margin: 8px 0; background: #f9f9f9; border-radius: 0 10px 10px 0;}
        .cl-quote {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .86rem; line-height: 1.55; background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 10px; padding: 12px 14px; white-space: pre-wrap;}
        .cl-quote mark {background: #ffff00; padding: 1px 2px;}
        .cl-source {font-size: .8rem; color: #666666;}
        .cl-divider {height: 1px; background: #e0e0e0; margin: 14px 0;}
        .cl-note {background: #f0f7ff; border: 1px solid #b3d7ff; border-radius: 10px; padding: 12px 14px; font-size: .88rem;}
        .cl-warn {background: #fff8e6; border: 1px solid #ffd54f; border-radius: 10px; padding: 12px 14px; font-size: .88rem;}
        .cl-error {background: #fff5f5; border: 1px solid #feb2b2; border-radius: 10px; padding: 12px 14px; font-size: .88rem;}
        .cl-success {background: #f0fff4; border: 1px solid #9ae6b4; border-radius: 10px; padding: 12px 14px; font-size: .88rem;}
        div[data-testid="stMetricValue"] {font-size: 1.55rem;}
        .stButton > button, .stDownloadButton > button {border-radius: 9px; font-weight: 600;}
        .stTextArea textarea, .stTextInput input {border-radius: 8px;}
        [data-testid="stFileUploaderDropzone"] {border-radius: 12px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_state() -> None:
    st.session_state.setdefault("policy", PolicyConfig())
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("source_path", None)
    st.session_state.setdefault("source_name", None)
    st.session_state.setdefault("analysis_error", None)
    st.session_state.setdefault("analysis_security_alert", None)
    st.session_state.setdefault("analysis_success", None)
    st.session_state.setdefault("review_store", {})
    st.session_state.setdefault("analysis_count", 0)
    st.session_state.setdefault("analysis_policy_fingerprint", None)
    st.session_state.setdefault("last_use_llm", False)
    # Incremented when a widget must be recreated from a clean slate. This is
    # the supported Streamlit pattern for clearing a file uploader or stale
    # review fields without mutating their widget keys after instantiation.
    st.session_state.setdefault("uploader_nonce", 0)
    st.session_state.setdefault("review_widget_nonce", 0)


def get_result() -> AnalysisResult | None:
    result = st.session_state.get("result")
    return result if isinstance(result, AnalysisResult) else None


def request_navigation(page: str) -> None:
    """Navigate without writing to the sidebar widget key after instantiation."""
    st.session_state["_requested_page"] = page
    st.rerun()


def clear_analysis() -> None:
    st.session_state["result"] = None
    st.session_state["analysis_error"] = None
    st.session_state["analysis_security_alert"] = None
    st.session_state["analysis_success"] = None
    st.session_state["analysis_summary"] = None
    st.session_state["review_store"] = {}
    st.session_state["source_path"] = None
    st.session_state["source_name"] = None
    st.session_state["analysis_policy_fingerprint"] = None
    st.session_state["document_sha256"] = None
    st.session_state["uploaded_file_size"] = None
    # Clear evidence viewer filters
    for key in ("ev_category", "ev_status", "ev_search", "ev_selected"):
        st.session_state.pop(key, None)
    # Clear dashboard filters
    for key in ("dash_status_filter", "dash_category_filter", "dash_search"):
        st.session_state.pop(key, None)


def set_analysis(result: AnalysisResult, source_path: str, source_name: str) -> None:
    st.session_state["result"] = result
    st.session_state["analysis_error"] = None
    st.session_state["review_store"] = {}
    st.session_state["source_path"] = source_path
    st.session_state["source_name"] = source_name
    st.session_state["analysis_policy_fingerprint"] = policy_fingerprint(result.policy)
    st.session_state["analysis_count"] = int(st.session_state.get("analysis_count", 0)) + 1


def run_analysis(file_path: str, source_name: str, use_llm: bool, progress_callback=None) -> AnalysisResult:
    policy: PolicyConfig = st.session_state["policy"]
    pipeline = AnalysisPipeline()
    result = pipeline.analyze_file(file_path, source_name, policy, use_llm=use_llm)
    st.session_state["last_use_llm"] = use_llm
    set_analysis(result, file_path, source_name)
    return result


def rerun_current(use_llm: bool | None = None) -> AnalysisResult | None:
    path = st.session_state.get("source_path")
    name = st.session_state.get("source_name")
    if not path or not name or not Path(path).exists():
        return None
    if use_llm is None:
        use_llm = bool(st.session_state.get("last_use_llm", False))
    return run_analysis(str(path), str(name), use_llm)


def sample_options() -> dict[str, Path]:
    return {
        "Risky demo contract (DOCX)": SAMPLES / "demo_contract.docx",
        "Risky demo contract (PDF)": SAMPLES / "demo_contract.pdf",
        "Missing jurisdiction example": SAMPLES / "demo_missing_category.docx",
        "Compliant example": SAMPLES / "compliant_contract.docx",
        "Prompt-injection example": SAMPLES / "prompt_injection_contract.docx",
        "Scanned / no text example": SAMPLES / "scanned_no_text.pdf",
    }


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def badge(status: str, label: str | None = None) -> str:
    color = STATUS_COLORS.get(status, "#475467")
    background = _tint(color)
    text = label or STATUS_LABELS.get(status, status.replace("_", " ").title())
    # Use white text on dark background for visibility on black cards
    return (
        f'<span class="cl-badge" style="color:#ffffff;background:#333333;'
        f'border:1px solid #ffffff">{esc(text)}</span>'
    )


def verification_badge(status: VerificationStatus | str) -> str:
    value = status.value if isinstance(status, VerificationStatus) else str(status)
    good = value == "VERIFIED"
    color = "#067647" if good else "#b42318" if value.startswith("FAILED") else "#b54708"
    label = VERIFICATION_LABELS.get(value, value.replace("_", " ").title())
    # Use white text on dark background for visibility on black cards
    return (
        f'<span class="cl-badge" style="color:#ffffff;background:#333333;'
        f'border:1px solid #ffffff">{esc(label)}</span>'
    )


def _tint(color: str) -> str:
    # Soft, accessible backgrounds for badge chips.
    return {
        "#b42318": "#fef3f2",
        "#b54708": "#fffaeb",
        "#475467": "#f2f4f7",
        "#6941c6": "#f4f3ff",
        "#067647": "#ecfdf3",
    }.get(color, "#f2f4f7")


def category_label(category: ClauseCategory | str) -> str:
    if isinstance(category, ClauseCategory):
        return category.label
    try:
        return ClauseCategory(category).label
    except ValueError:
        return str(category)


def category_icon(category: ClauseCategory | str) -> str:
    try:
        value = category if isinstance(category, ClauseCategory) else ClauseCategory(category)
    except ValueError:
        return "•"
    return CATEGORY_ICONS[value]


def location_text(finding: Finding) -> str:
    parts: list[str] = []
    if finding.page_number is not None:
        parts.append(f"Page {finding.page_number}")
    if finding.paragraph_index is not None:
        parts.append(f"Paragraph {finding.paragraph_index}")
    if not parts and finding.source_id:
        parts.append(finding.source_id)
    if finding.span_start is not None and finding.span_end is not None:
        parts.append(f"chars {finding.span_start}–{finding.span_end}")
    return " · ".join(parts) if parts else "Location unavailable"


def finding_label(finding: Finding) -> str:
    quote = " ".join(finding.exact_quote.split())
    quote = quote[:88] + ("…" if len(quote) > 88 else "")
    if not quote:
        quote = finding.explanation[:88] + "…"
    return f"{finding.finding_id} · {finding.clause_category.label} · {finding.status.value} · {quote}"


def status_order(status: FindingStatus) -> int:
    return {
        FindingStatus.HIGH: 0,
        FindingStatus.MEDIUM: 1,
        FindingStatus.LOW: 2,
        FindingStatus.REVIEW_REQUIRED: 3,
        FindingStatus.NO_CONFLICT_FOUND: 4,
        FindingStatus.NOT_FOUND: 5,
        FindingStatus.NOT_VERIFIED: 6,
    }.get(status, 9)


def unresolved_findings(result: AnalysisResult) -> list[Finding]:
    return [
        finding
        for finding in result.findings
        if finding.status
        in {
            FindingStatus.REVIEW_REQUIRED,
            FindingStatus.NOT_FOUND,
            FindingStatus.NOT_VERIFIED,
        }
    ]


def apply_reviews(result: AnalysisResult, store: dict[str, dict[str, Any]]) -> AnalysisResult:
    """Return a copy with user decisions applied, never mutating AI findings."""
    reviewed = result.model_copy(deep=True)
    for finding in reviewed.findings:
        entry = store.get(finding.finding_id)
        if not entry:
            continue
        decision = entry.get("decision")
        if decision:
            try:
                finding.review_decision = ReviewDecision(decision)
            except ValueError:
                finding.review_decision = None
        finding.review_notes = str(entry.get("notes", ""))
        finding.reviewer = str(entry.get("reviewer", ""))
        finding.reviewed_at = entry.get("reviewed_at")
    return reviewed


def review_counts(store: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {decision.value: 0 for decision in ReviewDecision}
    for entry in store.values():
        decision = entry.get("decision")
        if decision in counts:
            counts[decision] += 1
    counts["unreviewed"] = max(0, len(store) - sum(counts.values()))
    return counts


def policy_summary_rows(policy: PolicyConfig) -> list[tuple[str, str]]:
    return [
        ("Maximum payment period", f"{policy.max_payment_days} {policy.payment_basis.value} days"),
        ("Automatic renewal", "Allowed" if policy.allow_auto_renewal else "Not allowed"),
        ("Minimum renewal notice", f"{policy.min_renewal_notice_days} days"),
        ("Liability cap", "Required" if policy.liability_must_be_capped else "Not required"),
        ("Exclusive jurisdiction", "Allowed" if policy.allow_exclusive_jurisdiction else "Not allowed"),
        ("Approved jurisdictions", ", ".join(policy.approved_jurisdictions)),
    ]


def policy_fingerprint(policy: PolicyConfig) -> str:
    return "|".join(
        [
            str(policy.max_payment_days),
            policy.payment_basis.value,
            str(policy.allow_auto_renewal),
            str(policy.min_renewal_notice_days),
            str(policy.liability_must_be_capped),
            str(policy.allow_exclusive_jurisdiction),
            ",".join(policy.approved_jurisdictions),
            str(policy.preferences.model_dump()),
        ]
    )


def write_error(message: str) -> None:
    st.markdown(f'<div class="cl-error">{esc(message)}</div>', unsafe_allow_html=True)


def write_warning(message: str) -> None:
    st.markdown(f'<div class="cl-warn">{esc(message)}</div>', unsafe_allow_html=True)


def write_note(message: str) -> None:
    st.markdown(f'<div class="cl-note">{esc(message)}</div>', unsafe_allow_html=True)


def write_success(message: str) -> None:
    st.markdown(f'<div class="cl-success">{esc(message)}</div>', unsafe_allow_html=True)


def report_timestamp(result: AnalysisResult) -> str:
    return result.generated_at.replace("T", " ").replace("+00:00", " UTC")


def safe_upload_name(filename: str) -> str:
    name = os.path.basename(filename.replace("\\", "/"))
    return name or "contract"
