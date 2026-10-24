"""Policy configuration page."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from models.schemas import PaymentBasis, PolicyConfig, PolicyPreferences
from ui import common


def _parse_jurisdictions(raw: str) -> list[str]:
    pieces = []
    for chunk in raw.replace(";", "\n").replace(",", "\n").splitlines():
        value = " ".join(chunk.split()).strip()
        if value:
            pieces.append(value)
    return pieces


def render() -> None:
    st.markdown('<div class="cl-kicker">Step 2 · Policy</div>', unsafe_allow_html=True)
    st.title("Policy settings")

    # Show notifications from upload page
    security_alert = st.session_state.get("analysis_security_alert")
    if security_alert:
        # Display security alert in white text with black background
        st.markdown(f'<div style="color: #ffffff; background-color: #000000; padding: 10px; border-radius: 5px; font-weight: 600;">{common.esc(security_alert)}</div>', unsafe_allow_html=True)
        # Don't clear immediately - let it persist

    success = st.session_state.get("analysis_success")
    if success:
        if success == "The uploaded contract has been done !!!":
            # Display this specific message in white text with black background
            st.markdown(f'<div style="color: #ffffff; background-color: #000000; padding: 10px; border-radius: 5px; font-weight: 600;">{common.esc(success)}</div>', unsafe_allow_html=True)
        else:
            common.write_success(success)
        # Don't clear immediately - let it persist

    error = st.session_state.get("analysis_error")
    if error:
        common.write_error(error)
        # Don't clear immediately - let it persist

    current: PolicyConfig = st.session_state["policy"]
    result = common.get_result()
    saved_notice = st.session_state.pop("_policy_saved_notice", None)
    if saved_notice:
        common.write_success(saved_notice["message"])
        if saved_notice.get("note"):
            common.write_note(saved_notice["note"])
    if result and st.session_state.get("analysis_policy_fingerprint") != common.policy_fingerprint(current):
        common.write_warning(
            "The active policy has changed since the current analysis. Re-run the analysis "
            "to apply the new thresholds."
        )

    with st.form("policy_form", clear_on_submit=False):
        st.markdown("### Hard constraints")
        col1, col2 = st.columns(2, gap="large")
        with col1:
            max_payment_days = st.number_input(
                "Maximum payment period (days)",
                min_value=0,
                max_value=365,
                value=int(current.max_payment_days),
                step=5,
            )
            payment_basis = st.selectbox(
                "Payment day basis",
                options=[basis.value for basis in PaymentBasis],
                index=0 if current.payment_basis == PaymentBasis.CALENDAR else 1,
                format_func=lambda value: value.title() + " days",
            )
            allow_auto_renewal = st.toggle(
                "Permit automatic renewal",
                value=current.allow_auto_renewal,
            )
            min_renewal_notice_days = st.number_input(
                "Minimum renewal notice (days)",
                min_value=0,
                max_value=365,
                value=int(current.min_renewal_notice_days),
                step=5,
            )
        with col2:
            liability_must_be_capped = st.toggle(
                "Require a liability cap",
                value=current.liability_must_be_capped,
            )
            allow_exclusive_jurisdiction = st.toggle(
                "Permit exclusive jurisdiction",
                value=current.allow_exclusive_jurisdiction,
            )
            jurisdictions_raw = st.text_area(
                "Approved jurisdictions",
                value="\n".join(current.approved_jurisdictions),
                height=126,
            )

        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        st.markdown("### Preferences (advisory)")
        pref1, pref2 = st.columns(2, gap="large")
        with pref1:
            preferred_payment_days = st.number_input(
                "Preferred payment period (days)",
                min_value=0,
                max_value=365,
                value=int(current.preferences.preferred_payment_days),
                step=5,
            )
            prefer_mutual_indemnity = st.toggle(
                "Prefer mutual indemnification",
                value=current.preferences.prefer_mutual_indemnity,
            )
        with pref2:
            flag_auto_renewal_even_if_allowed = st.toggle(
                "Flag auto-renewal even when permitted",
                value=current.preferences.flag_auto_renewal_even_if_allowed,
            )
            require_written_notice = st.toggle(
                "Prefer written notice for renewal",
                value=current.preferences.require_written_notice,
            )
        notes = st.text_area(
            "Internal policy note",
            value=current.preferences.notes,
            height=80,
            max_chars=2000,
        )

        submitted = st.form_submit_button("Save policy", type="primary", use_container_width=True)

    if submitted:
        try:
            new_policy = PolicyConfig(
                max_payment_days=int(max_payment_days),
                payment_basis=PaymentBasis(payment_basis),
                allow_auto_renewal=allow_auto_renewal,
                min_renewal_notice_days=int(min_renewal_notice_days),
                liability_must_be_capped=liability_must_be_capped,
                approved_jurisdictions=_parse_jurisdictions(jurisdictions_raw),
                allow_exclusive_jurisdiction=allow_exclusive_jurisdiction,
                preferences=PolicyPreferences(
                    preferred_payment_days=int(preferred_payment_days),
                    prefer_mutual_indemnity=prefer_mutual_indemnity,
                    flag_auto_renewal_even_if_allowed=flag_auto_renewal_even_if_allowed,
                    require_written_notice=require_written_notice,
                    notes=notes.strip(),
                ),
            )
        except ValidationError as exc:
            messages = "; ".join(error["msg"] for error in exc.errors())
            common.write_error(f"Policy was not saved: {messages}")
        else:
            st.session_state["policy"] = new_policy
            notice = {
                "message": "Policy saved. Re-run the current contract to apply it.",
                "note": "",
            }
            if new_policy.preferences.preferred_payment_days > new_policy.max_payment_days:
                notice["note"] = (
                    "Your preferred payment period is longer than the hard maximum. "
                    "Preference flags only apply inside the hard limit."
                )
            st.session_state["_policy_saved_notice"] = notice
            st.rerun()

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    action1, action2, action3 = st.columns([1.2, 1, 2])
    if action1.button(
        "Re-run analysis",
        type="primary",
        use_container_width=True,
        disabled=result is None,
    ):
        with st.status("Re-analyzing…", expanded=True) as status:
            status.write("📄 Loading document…")
            analysis_result = common.rerun_current()
            if analysis_result is None:
                common.write_error("No file available")
            else:
                status.write("✓ Document loaded")
                status.write("🔍 Segmenting clauses…")
                status.write("✓ Clauses segmented")
                status.write("🛡️ Checking for prompt injection…")
                status.write("✓ Security check complete")
                status.write("📚 Retrieving relevant passages…")
                status.write("✓ Passages retrieved")
                status.write("⚖️ Applying new policy rules…")
                status.write("✓ Policy evaluation complete")
                status.write("🔎 Verifying evidence…")
                status.write("✓ Evidence verified")
                status.write("📊 Classifying risks…")
                status.write("✓ Risk classification complete")

                # Create a short, understandable summary
                summary = analysis_result.summary
                risk_summary = []
                if summary.confirmed_conflicts > 0:
                    risk_summary.append(f"{summary.confirmed_conflicts} HIGH/MEDIUM risks")
                if summary.review_required > 0:
                    risk_summary.append(f"{summary.review_required} need review")
                if summary.not_verified > 0:
                    risk_summary.append(f"{summary.not_verified} unverified")
                if summary.no_conflict_found > 0:
                    risk_summary.append(f"{summary.no_conflict_found} compliant")

                summary_text = ", ".join(risk_summary) if risk_summary else "No issues found"
                status.update(
                    label=f"Done · {summary_text}",
                    state="complete",
                    expanded=False,
                )
                common.request_navigation("Risk dashboard")
    if action2.button("Reset to defaults", use_container_width=True):
        st.session_state["policy"] = PolicyConfig()
        st.rerun()
