"""Human review workflow and JSON/PDF export."""

from __future__ import annotations

import streamlit as st

from core.report_generator import ReportGenerationError, ReportGenerator
from models.schemas import (
    AnalysisResult,
    Finding,
    FindingStatus,
    ReviewDecision,
    utc_now_iso,
)
from ui import common

DECISION_LABELS = {
    "not_reviewed": "Not reviewed",
    ReviewDecision.ACCEPT.value: "Accept",
    ReviewDecision.NEGOTIATE.value: "Negotiate",
    ReviewDecision.SEEK_LEGAL_ADVICE.value: "Seek legal advice",
}

ATTENTION_STATUSES = {
    FindingStatus.HIGH,
    FindingStatus.MEDIUM,
    FindingStatus.REVIEW_REQUIRED,
    FindingStatus.NOT_FOUND,
    FindingStatus.NOT_VERIFIED,
}


def _empty_state() -> None:
    common.write_note(
        "No contract analyzed yet. Upload a file or run the demo."
    )
    if st.button("Go to Analyze contract", type="primary"):
        common.request_navigation("Analyze contract")


def _save_review(finding_id: str, decision: str, notes: str, reviewer: str) -> None:
    store = st.session_state.setdefault("review_store", {})
    if decision == "not_reviewed" and not notes.strip() and not reviewer.strip():
        store.pop(finding_id, None)
        return
    store[finding_id] = {
        "decision": None if decision == "not_reviewed" else decision,
        "notes": notes.strip(),
        "reviewer": reviewer.strip(),
        "reviewed_at": utc_now_iso(),
    }


def _render_review_card(result: AnalysisResult, finding: Finding, key_prefix: str) -> None:
    store = st.session_state.setdefault("review_store", {})
    existing = store.get(finding.finding_id, {})
    existing_decision = existing.get("decision") or "not_reviewed"
    source = common.location_text(finding)
    label = (
        f"{finding.finding_id} · {finding.clause_category.label} · "
        f"{common.STATUS_LABELS.get(finding.status.value, finding.status.value)}"
    )
    with st.expander(label, expanded=False):
        st.markdown(
            f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            f"{common.badge(finding.status.value)}"
            f"{common.verification_badge(finding.verification_status)}"
            f'<span class="cl-badge" style="color:#475467;background:#f2f4f7;'
            f'border:1px solid #e4e7ec">{common.esc(source)}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
        if finding.exact_quote:
            st.markdown(
                f'<div class="cl-quote">{common.esc(finding.exact_quote)}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(f"**Why:** {common.esc(finding.explanation)}")
        if finding.recommended_action:
            st.markdown(
                f"**Action:** {common.esc(finding.recommended_action)}"
            )
        decision = st.radio(
            "Decision",
            options=list(DECISION_LABELS.keys()),
            index=list(DECISION_LABELS.keys()).index(existing_decision)
            if existing_decision in DECISION_LABELS
            else 0,
            format_func=lambda value: DECISION_LABELS[value],
            horizontal=True,
            key=f"{key_prefix}_decision_{finding.finding_id}",
        )
        notes = st.text_area(
            "Review notes",
            value=str(existing.get("notes", "")),
            height=80,
            max_chars=2000,
            key=f"{key_prefix}_notes_{finding.finding_id}",
        )
        action1, action2, _ = st.columns([1, 1, 2])
        if action1.button(
            "Save review",
            key=f"{key_prefix}_save_{finding.finding_id}",
            type="primary",
            use_container_width=True,
        ):
            reviewer = st.session_state.get("reviewer_name", "")
            if decision == "not_reviewed" and not notes.strip():
                common.write_warning("Choose a decision or add a note before saving.")
            else:
                _save_review(finding.finding_id, decision, notes, reviewer)
                st.rerun()
        if action2.button(
            "Clear review",
            key=f"{key_prefix}_clear_{finding.finding_id}",
            use_container_width=True,
            disabled=not existing,
        ):
            st.session_state["review_store"].pop(finding.finding_id, None)
            # Recreate the decision/notes widgets so the cleared record does not
            # reappear from the previous widget state.
            st.session_state["review_widget_nonce"] = (
                int(st.session_state.get("review_widget_nonce", 0)) + 1
            )
            st.rerun()
        if existing:
            recorded = str(existing.get("reviewed_at", "")).replace("T", " ")
            st.caption(
                f"Recorded {recorded or '—'} by {existing.get('reviewer') or 'anonymous reviewer'}."
            )


def _review_queue(result: AnalysisResult, mode: str) -> list[Finding]:
    if mode == "All findings":
        return list(result.findings)
    if mode == "No-conflict findings":
        return [
            finding
            for finding in result.findings
            if finding.status == FindingStatus.NO_CONFLICT_FOUND
        ]
    return [finding for finding in result.findings if finding.status in ATTENTION_STATUSES]


def _render_export(result: AnalysisResult) -> None:
    store = st.session_state.setdefault("review_store", {})
    reviewed = common.apply_reviews(result, store)
    include_text = st.toggle(
        "Include full contract text in JSON",
        value=False,
        key="export_include_text",
    )
    generator = ReportGenerator()
    try:
        json_payload = generator.to_json(
            reviewed, include_contract_text=include_text
        )
        pdf_payload = generator.to_pdf_bytes(reviewed)
    except ReportGenerationError as exc:
        common.write_error(f"Export failed: {exc}")
        return
    except Exception as exc:  # keep the export page usable
        common.write_error(f"Export failed unexpectedly: {exc}")
        return

    counts = common.review_counts(store)
    decision_total = (
        counts.get(ReviewDecision.ACCEPT.value, 0)
        + counts.get(ReviewDecision.NEGOTIATE.value, 0)
        + counts.get(ReviewDecision.SEEK_LEGAL_ADVICE.value, 0)
    )
    unresolved = [
        finding
        for finding in result.findings
        if finding.finding_id not in store and finding.status in ATTENTION_STATUSES
    ]
    if unresolved:
        common.write_warning(
            f"{len(unresolved)} finding(s) needing attention have no recorded decision. "
            "You can still export; the unresolved list will be included in the report."
        )
    else:
        common.write_success(
            "Every finding that needs attention has a recorded human decision."
        )

    cols = st.columns(3)
    cols[0].metric("Decisions recorded", decision_total)
    cols[1].metric("Unresolved", len(unresolved))
    cols[2].metric("Confirmed conflicts", result.summary.confirmed_conflicts)

    st.markdown("### Downloads")
    file_col1, file_col2 = st.columns(2)
    file_col1.download_button(
        "Download JSON report",
        data=json_payload.encode("utf-8"),
        file_name=generator.report_filename(reviewed, "json"),
        mime="application/json",
        use_container_width=True,
        key="download_json",
    )
    file_col2.download_button(
        "Download PDF report",
        data=pdf_payload,
        file_name=generator.report_filename(reviewed, "pdf"),
        mime="application/pdf",
        use_container_width=True,
        key="download_pdf",
    )
    st.caption(
        f"JSON {len(json_payload.encode('utf-8')) / 1024:.1f} KB · "
        f"PDF {len(pdf_payload) / 1024:.1f} KB"
    )


def render() -> None:
    result = common.get_result()
    st.markdown('<div class="cl-kicker">Step 4 · Decide & export</div>', unsafe_allow_html=True)
    st.title("Review & export")
    if result is None:
        _empty_state()
        return

    summary = result.summary
    store = st.session_state.setdefault("review_store", {})
    counts = common.review_counts(store)
    recorded = (
        counts.get(ReviewDecision.ACCEPT.value, 0)
        + counts.get(ReviewDecision.NEGOTIATE.value, 0)
        + counts.get(ReviewDecision.SEEK_LEGAL_ADVICE.value, 0)
    )
    attention = [finding for finding in result.findings if finding.status in ATTENTION_STATUSES]
    top1, top2, top3 = st.columns([1, 1, 1.2])
    top1.metric("Decisions recorded", recorded)
    top2.metric("Need attention", len(attention))
    top3.text_input(
        "Reviewer name",
        key="reviewer_name",
    )

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("### Review queue")
    mode = st.radio(
        "Show",
        ["Needs attention", "All findings", "No-conflict findings"],
        horizontal=True,
        key="review_mode",
        label_visibility="collapsed",
    )
    queue = _review_queue(result, mode)
    if not queue:
        common.write_success("No findings")
    else:
        st.caption(f"{len(queue)} findings shown")
        key_prefix = (
            f"r{st.session_state.get('analysis_count', 0)}"
            f"_{st.session_state.get('review_widget_nonce', 0)}"
        )
        for finding in queue:
            _render_review_card(result, finding, key_prefix)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("### Export")
    _render_export(result)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    nav1, nav2, nav3 = st.columns([1, 1, 1.4])
    if nav1.button("Open evidence explorer", use_container_width=True):
        common.request_navigation("Evidence explorer")
    if nav2.button("Back to dashboard", use_container_width=True):
        common.request_navigation("Risk dashboard")
    if nav3.button("Clear all review decisions", use_container_width=True):
        st.session_state["review_store"] = {}
        st.session_state["review_widget_nonce"] = (
            int(st.session_state.get("review_widget_nonce", 0)) + 1
        )
        st.rerun()
