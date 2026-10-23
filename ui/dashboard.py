"""Risk dashboard page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from models.schemas import ClauseCategory, FindingStatus
from ui import common


def _empty_state() -> None:
    common.write_note(
        "No contract analyzed yet. Upload a file or run the demo."
    )
    if st.button("Go to Analyze contract", type="primary"):
        common.request_navigation("Analyze contract")


def _findings_frame(result, statuses: list[str], categories: list[str], search: str) -> pd.DataFrame:
    rows = []
    query = search.strip().casefold()
    for finding in result.findings:
        if statuses and finding.status.value not in statuses:
            continue
        if categories and finding.clause_category.value not in categories:
            continue
        haystack = " ".join(
            [
                finding.finding_id,
                finding.clause_category.value,
                finding.exact_quote,
                finding.explanation,
                finding.policy_violated or "",
                finding.rule_id or "",
            ]
        ).casefold()
        if query and query not in haystack:
            continue
        rows.append(
            {
                "ID": finding.finding_id,
                "Category": finding.clause_category.label,
                "Status": finding.status.value,
                "Verification": finding.verification_status.value,
                "Location": common.location_text(finding),
                "Evidence": (
                    "Yes" if finding.has_evidence else "No"
                ),
                "Quote / explanation": (
                    " ".join(finding.exact_quote.split())[:150]
                    or " ".join(finding.explanation.split())[:150]
                ),
                "Rule": finding.rule_id or "—",
            }
        )
    return pd.DataFrame(rows)


def render() -> None:
    result = common.get_result()
    st.markdown('<div class="cl-kicker">Step 3 · Triage</div>', unsafe_allow_html=True)
    st.title("Risk dashboard")
    if result is None:
        _empty_state()
        return

    summary = result.summary

    metric_cols = st.columns(4)
    metric_cols[0].metric(
        "Confirmed conflicts",
        summary.confirmed_conflicts,
        help="HIGH + MEDIUM findings",
    )
    metric_cols[1].metric(
        "Review required",
        summary.review_required,
    )
    metric_cols[2].metric(
        "Not found / not verified",
        summary.not_found + summary.not_verified,
    )
    verification_value = (
        f"{summary.verified_evidence}/{summary.evidence_bearing_findings}"
        if summary.evidence_bearing_findings
        else "n/a"
    )
    metric_cols[3].metric(
        "Evidence verified",
        verification_value,
    )

    if summary.not_verified:
        common.write_warning(f"{summary.not_verified} items NOT_VERIFIED")
    if result.warnings:
        with st.expander("Warnings", expanded=bool(summary.not_verified)):
            for warning in result.warnings:
                st.markdown(f"- {warning}")
    if result.llm_status.requested and not result.llm_status.used:
        common.write_note("AI unavailable. Deterministic analysis completed.")

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("### Categories")
    category_cols = st.columns(2)
    for index, category in enumerate(ClauseCategory):
        counts = summary.by_category.get(category.value, {})
        total = counts.get("total", 0)
        detected = total > 0 and any(
            counts.get(key, 0)
            for key in ("high", "medium", "low", "review_required", "no_conflict_found")
        )
        chips = []
        for key, status in (
            ("high", "HIGH"),
            ("medium", "MEDIUM"),
            ("low", "LOW"),
            ("review_required", "REVIEW_REQUIRED"),
            ("no_conflict_found", "NO_CONFLICT_FOUND"),
            ("not_found", "NOT_FOUND"),
            ("not_verified", "NOT_VERIFIED"),
        ):
            value = counts.get(key, 0)
            if value:
                chips.append(common.badge(status, f"{value} {common.STATUS_LABELS[status].lower()}"))
        chip_html = " ".join(chips) if chips else common.badge("NOT_FOUND", "No evidence")
        with category_cols[index % 2]:
            st.markdown(
                f'<div class="cl-card" style="margin-bottom:12px">'
                f'<h4>{common.category_icon(category)} {common.esc(category.label)}</h4>'
                f"<div style=\"display:flex;gap:5px;flex-wrap:wrap\">{chip_html}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("### Findings")
    filter_cols = st.columns([1.5, 1.5, 2])
    statuses = filter_cols[0].multiselect(
        "Status",
        options=[status.value for status in FindingStatus],
        default=[],
        format_func=lambda value: common.STATUS_LABELS.get(value, value),
        key="dash_status_filter",
    )
    categories = filter_cols[1].multiselect(
        "Category",
        options=[category.value for category in ClauseCategory],
        default=[],
        format_func=lambda value: common.category_label(value),
        key="dash_category_filter",
    )
    search = filter_cols[2].text_input(
        "Search quote, rule, or explanation",
        key="dash_search",
        placeholder="e.g. uncapped, renewal, Singapore",
    )
    frame = _findings_frame(result, statuses, categories, search)
    if frame.empty:
        st.info("No findings match the current filters.")
    else:
        st.dataframe(
            frame,
            use_container_width=True,
            hide_index=True,
            height=min(560, 42 + len(frame) * 35),
            column_config={
                "ID": st.column_config.TextColumn("ID", width="small"),
                "Status": st.column_config.TextColumn("Status", width="medium"),
                "Verification": st.column_config.TextColumn("Verification", width="medium"),
                "Location": st.column_config.TextColumn("Location", width="medium"),
                "Evidence": st.column_config.TextColumn("Evidence", width="small"),
                "Rule": st.column_config.TextColumn("Rule", width="medium"),
            },
        )

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    nav1, nav2, nav3 = st.columns([1, 1, 2])
    if nav1.button("Open evidence explorer", use_container_width=True):
        common.request_navigation("Evidence explorer")
    if nav2.button("Record review decisions", use_container_width=True):
        common.request_navigation("Review & export")
