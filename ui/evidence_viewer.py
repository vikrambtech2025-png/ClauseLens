"""Quote-first evidence explorer with independent verification details."""

from __future__ import annotations

import json

import streamlit as st

from models.schemas import AnalysisResult, ClauseCategory, Finding, FindingStatus
from ui import common


def _empty_state() -> None:
    common.write_note(
        "No contract analyzed yet. Upload a file or run the demo."
    )
    if st.button("Go to Analyze contract", type="primary"):
        common.request_navigation("Analyze contract")


def _span_context(result: AnalysisResult, finding: Finding) -> tuple[str, str] | None:
    """Return (HTML with highlighted quote, location metadata) or None."""
    span = result.document.span_by_id(finding.source_id)
    if span is None:
        return None
    text = span.text
    start = finding.span_start
    end = finding.span_end
    quote = finding.exact_quote
    if start is None or end is None or start < 0 or end > len(text) or not quote:
        located = text.find(quote) if quote else -1
        if located >= 0:
            start, end = located, located + len(quote)
        else:
            start, end = 0, 0
    window = 260
    left = max(0, int(start) - window)
    right = min(len(text), int(end) + window)
    before = text[left : int(start)]
    highlighted = text[int(start) : int(end)]
    after = text[int(end) : right]
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    markup = (
        f"{common.esc(prefix)}{common.esc(before)}"
        f"<mark>{common.esc(highlighted)}</mark>"
        f"{common.esc(after)}{common.esc(suffix)}"
    )
    location = span.page_number if span.page_number is not None else span.paragraph_index
    detail = (
        f"Source {span.source_id}"
        + (f" · page {span.page_number}" if span.page_number is not None else "")
        + (
            f" · paragraph {span.paragraph_index}"
            if span.paragraph_index is not None
            else ""
        )
        + (f" · {span.source_type}" if span.source_type else "")
    )
    return markup, detail


def _verification_row(label: str, value: str, ok: bool | None) -> str:
    color = "#067647" if ok is True else "#b42318" if ok is False else "#475467"
    background = common._tint(color)
    return (
        f'<div style="display:flex;justify-content:space-between;gap:12px;'
        f'padding:8px 0;border-bottom:1px solid #e4e7ec">'
        f'<span style="color:#475467;font-size:.86rem">{common.esc(label)}</span>'
        f'<span style="color:{color};background:{background};border-radius:6px;'
        f'padding:1px 7px;font-size:.78rem;font-weight:650">{common.esc(value)}</span>'
        f"</div>"
    )


def _render_finding(result: AnalysisResult, finding: Finding) -> None:
    summary = result.summary
    location = common.location_text(finding)
    quality = finding.evidence_quality or "No evidence grade"
    st.markdown(
        f'<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:4px">'
        f'<span style="font-size:.8rem;color:#475467;font-weight:650">'
        f"{common.esc(finding.finding_id)}</span>"
        f"{common.badge(finding.status.value)}"
        f"{common.verification_badge(finding.verification_status)}"
        f'<span class="cl-badge" style="color:#475467;background:#f2f4f7;'
        f'border:1px solid #e4e7ec">{common.esc(quality)}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"## {common.esc(finding.clause_category.label)}")
    st.markdown(
        f'<div class="cl-source">{common.esc(location)} · Rule '
        f'{common.esc(finding.rule_id or "n/a")}'
        + (" · AI-generated" if finding.is_ai_generated else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if finding.status == FindingStatus.NOT_VERIFIED:
        common.write_warning("Verification failed")
    elif finding.status == FindingStatus.NOT_FOUND:
        common.write_note("No clause found")
    elif finding.status == FindingStatus.REVIEW_REQUIRED:
        common.write_note("Needs human review")
    elif finding.status == FindingStatus.NO_CONFLICT_FOUND:
        common.write_success("No conflict")

    st.markdown("### Quotation")
    if finding.exact_quote:
        st.markdown(
            f'<div class="cl-quote">{common.esc(finding.exact_quote)}</div>',
            unsafe_allow_html=True,
        )

    context = _span_context(result, finding)
    st.markdown("### Source context")
    if context:
        markup, detail = context
        st.markdown(
            f'<div class="cl-source" style="margin-bottom:6px">{common.esc(detail)}</div>'
            f'<div class="cl-quote">{markup}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("### Details")
    if finding.policy_violated:
        st.markdown(f"**Policy:** {common.esc(finding.policy_violated)}")
    st.markdown(f"**Explanation:** {common.esc(finding.explanation)}")
    if finding.recommended_action:
        st.markdown(f"**Action:** {common.esc(finding.recommended_action)}")

    st.markdown("### Verification")
    verification = finding.raw_evidence.get("verification", {}) if finding.raw_evidence else {}
    quote_match = verification.get("quote_match", {})
    quote_ok = verification.get("source_verified")
    location_ok = verification.get("location_matches")
    support = verification.get("policy_support_result", {})
    support_result = support.get("result", "not checked")
    support_ok = True if support_result == "pass" else False if support_result == "fail" else None
    match_type = quote_match.get("match_type", "unavailable")
    rows = [
        _verification_row(
            "Quote exists in original span",
            f"{str(match_type).replace('_', ' ')} · {quote_match.get('status', 'unavailable')}",
            bool(quote_ok),
        ),
        _verification_row(
            "Source location validated",
            "validated" if location_ok else "not validated",
            bool(location_ok),
        ),
        _verification_row(
            "Policy comparison independently supported",
            str(support_result).replace("_", " "),
            support_ok,
        ),
        _verification_row(
            "Verification status",
            finding.verification_status.value.replace("_", " ").title(),
            finding.verification_status.value == "VERIFIED",
        ),
    ]
    st.markdown("".join(rows), unsafe_allow_html=True)
    if verification.get("location_detail"):
        st.caption(verification["location_detail"])
    if support.get("reason"):
        st.caption(f"Policy-support check: {support['reason']}")

    review = st.session_state.get("review_store", {}).get(finding.finding_id)
    if review:
        st.markdown("### Human review")
        decision = str(review.get("decision", "not recorded")).replace("_", " ").title()
        st.markdown(
            f"**Decision:** {common.esc(decision)}  \n"
            f"**Reviewer:** {common.esc(review.get('reviewer') or 'not recorded')}  \n"
            f"**Notes:** {common.esc(review.get('notes') or '—')}  \n"
            f"**Recorded:** {common.esc(review.get('reviewed_at') or '—')}"
        )
    else:
        st.caption("No human review decision recorded for this finding yet.")

    with st.expander("Verification record (raw)"):
        st.json(finding.raw_evidence)
    if finding.source_id:
        with st.expander("Source span metadata"):
            span = result.document.span_by_id(finding.source_id)
            if span:
                st.json(span.model_dump(mode="json"))
            else:
                st.caption("Source span is not present in the extraction map.")


def render() -> None:
    result = common.get_result()
    st.markdown('<div class="cl-kicker">Evidence · Traceability</div>', unsafe_allow_html=True)
    st.title("Evidence explorer")

    # Show notifications from upload page
    security_alert = st.session_state.get("analysis_security_alert")
    if security_alert:
        common.write_error(security_alert)
        # Don't clear immediately - let it persist

    success = st.session_state.get("analysis_success")
    if success:
        common.write_success(success)
        # Don't clear immediately - let it persist

    error = st.session_state.get("analysis_error")
    if error:
        common.write_error(error)
        # Don't clear immediately - let it persist

    if result is None:
        _empty_state()
        return

    summary = result.summary
    verified = summary.verified_evidence
    bearing = summary.evidence_bearing_findings
    if bearing and verified == bearing:
        common.write_success(f"All {bearing} verified")
    elif bearing:
        common.write_warning(f"{bearing - verified} failed verification")
    else:
        common.write_note("No evidence findings")

    filters = st.columns([1.2, 1.2, 2])
    category = filters[0].selectbox(
        "Category",
        options=["all"] + [item.value for item in ClauseCategory],
        format_func=lambda value: "All categories"
        if value == "all"
        else common.category_label(value),
        key="ev_category",
    )
    status = filters[1].selectbox(
        "Status",
        options=["all"] + [item.value for item in FindingStatus],
        format_func=lambda value: "All statuses"
        if value == "all"
        else common.STATUS_LABELS.get(value, value),
        key="ev_status",
    )
    search = filters[2].text_input(
        "Search quotation or explanation",
        placeholder="e.g. liability, 90 days, Singapore",
        key="ev_search",
    )

    candidates = []
    query = search.strip().casefold()
    for finding in result.findings:
        if category != "all" and finding.clause_category.value != category:
            continue
        if status != "all" and finding.status.value != status:
            continue
        haystack = " ".join(
            [finding.exact_quote, finding.explanation, finding.policy_violated or "", finding.finding_id]
        ).casefold()
        if query and query not in haystack:
            continue
        candidates.append(finding)

    if not candidates:
        st.info("No findings match the current filters.")
        return
    candidates.sort(key=lambda item: (common.status_order(item.status), item.finding_id))
    selected_id = st.selectbox(
        "Finding",
        options=[finding.finding_id for finding in candidates],
        format_func=lambda value: next(
            (common.finding_label(finding) for finding in candidates if finding.finding_id == value),
            value,
        ),
        key="ev_selected",
    )
    finding = next(item for item in candidates if item.finding_id == selected_id)
    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    _render_finding(result, finding)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    nav1, nav2, nav3 = st.columns([1, 1, 2])
    if nav1.button("Open risk dashboard", use_container_width=True):
        common.request_navigation("Risk dashboard")
    if nav2.button("Record review decision", use_container_width=True):
        common.request_navigation("Review & export")

    if st.button("Clear filters", use_container_width=False):
        for key in ("ev_category", "ev_status", "ev_search", "ev_selected"):
            st.session_state.pop(key, None)
        st.rerun()
