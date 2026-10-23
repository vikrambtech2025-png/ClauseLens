"""ClauseLens — evidence-grounded contract risk flagger.

A Streamlit decision-support app. It is not legal advice.
"""

from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from ui import common, dashboard, evidence_viewer, policies, review_export, upload


def main() -> None:
    st.set_page_config(
        page_title="ClauseLens · Contract Risk Flagger",
        page_icon="⚖",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    common.inject_css()
    common.init_state()
    settings = get_settings()

    with st.sidebar:
        st.markdown(
            '<div class="cl-brand">Clause<span>Lens</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        if "_requested_page" in st.session_state:
            st.session_state["nav"] = st.session_state.pop("_requested_page")
        st.session_state.setdefault("nav", "Analyze contract")
        page = st.radio(
            "Workspace",
            [
                "Analyze contract",
                "Policy settings",
                "Risk dashboard",
                "Evidence explorer",
                "Review & export",
            ],
            key="nav",
            label_visibility="collapsed",
        )
        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

        result = common.get_result()
        if result:
            st.markdown(
                f'<div class="cl-kicker">Current</div>'
                f'<div style="font-weight:650;margin:.25rem 0 .1rem 0;overflow-wrap:anywhere">'
                f'{common.esc(result.document.filename)}</div>',
                unsafe_allow_html=True,
            )
            summary = result.summary
            st.markdown(
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">'
                f'{common.badge("HIGH", f"{summary.high}")}'
                f'{common.badge("REVIEW_REQUIRED", f"{summary.review_required}")}'
                f'{common.badge("NO_CONFLICT_FOUND", f"{summary.no_conflict_found}")}'
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button("Clear", use_container_width=True):
                common.clear_analysis()
                st.rerun()

        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        policy = st.session_state["policy"]
        st.markdown('<div class="cl-kicker">Policy</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="cl-sub">Payment ≤ {policy.max_payment_days} days<br>'
            f'Auto-renewal: {"allowed" if policy.allow_auto_renewal else "blocked"}<br>'
            f'Liability cap: {"required" if policy.liability_must_be_capped else "optional"}</div>',
            unsafe_allow_html=True,
        )
        if st.button("Edit", use_container_width=True):
            common.request_navigation("Policy settings")

    if page == "Analyze contract":
        upload.render()
    elif page == "Policy settings":
        policies.render()
    elif page == "Risk dashboard":
        dashboard.render()
    elif page == "Evidence explorer":
        evidence_viewer.render()
    else:
        review_export.render()

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
