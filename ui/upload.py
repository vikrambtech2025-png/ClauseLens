"""Upload, demo selection, and analysis launch page."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import streamlit as st

from config.settings import get_settings
from core.document_loader import DocumentError
from ui import common


def _save_upload(filename: str, data: bytes) -> Path:
    upload_dir = Path(tempfile.gettempdir()) / "clauselens_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = common.safe_upload_name(filename)
    suffix = Path(name).suffix.lower()
    path = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return path


def _execute(path: Path, name: str, use_llm: bool, is_demo: bool = False, use_llm_decider: bool = False) -> None:
    try:
        with st.status("Analyzing…", expanded=True) as status:
            status.write(f"📄 Loading file: {name}")
            status.write(f"📄 File path: {path}")
            status.write("📄 Extracting text from document…")
            result = common.run_analysis(str(path), name, use_llm, use_finetuned=False, use_llm_decider=use_llm_decider)
            status.write("✓ Text extracted")
            status.write(f"📄 Document: {result.document.filename}")
            status.write(f"📄 Characters extracted: {result.document.stats.num_chars}")
            status.write("🔍 Segmenting clauses…")
            status.write("✓ Clauses segmented")
            status.write("🛡️ Checking for prompt injection…")
            status.write("✓ Security check complete")
            status.write("📚 Retrieving relevant passages…")
            status.write("✓ Passages retrieved")
            if use_llm_decider:
                status.write("🤖 LLM deciding from RAG output…")
            else:
                status.write("⚖️ Applying policy rules…")
            status.write("✓ Policy evaluation complete")
            status.write("🔎 Verifying evidence…")
            status.write("✓ Evidence verified")
            status.write("📊 Classifying risks…")
            status.write("✓ Risk classification complete")

            # Create a short, understandable summary
            summary = result.summary
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
        # Check for prompt injection warnings
        if any("prompt-injection" in warning.lower() or "injection" in warning.lower() for warning in result.warnings):
            st.session_state["analysis_security_alert"] = "SECURITY ALERT: This document contains passages that appear to be prompt-injection attempts. These passages have been quarantined from analysis, but the rest of the document was analyzed normally. Review the warnings on the Risk dashboard for details."

        # Check for fake data detection
        if result.fake_data_detected:
            reasons_text = "; ".join(result.fake_data_reasons) if result.fake_data_reasons else "Unknown reasons"
            st.session_state["analysis_fake_data_alert"] = f"⚠️ FAKE DATA DETECTED: This document appears to be fake, mocked, or falsified. {reasons_text} The analysis results may not be reliable."

        # Store file hash for verification
        st.session_state["document_sha256"] = result.document.sha256

        # Different success message for demo vs uploaded contract
        if is_demo:
            st.session_state["analysis_success"] = f"Analysis complete: {summary_text}"
        else:
            st.session_state["analysis_success"] = "The uploaded contract has been done !!!"
            st.session_state["analysis_summary"] = summary_text

        st.rerun()
    except DocumentError as exc:
        st.session_state["analysis_error"] = str(exc)
        st.rerun()
    except Exception as exc:
        st.session_state["analysis_error"] = f"Error: {exc}"
        st.rerun()


def render() -> None:
    settings = get_settings()
    st.markdown('<div class="cl-kicker">Step 1 · Intake</div>', unsafe_allow_html=True)
    st.title("Analyze a contract")

    error = st.session_state.get("analysis_error")
    if error:
        common.write_error(error)

    security_alert = st.session_state.get("analysis_security_alert")
    if security_alert:
        # Display security alert in white text with black background
        st.markdown(f'<div style="color: #ffffff; background-color: #000000; padding: 10px; border-radius: 5px; font-weight: 600;">{common.esc(security_alert)}</div>', unsafe_allow_html=True)

    fake_data_alert = st.session_state.get("analysis_fake_data_alert")
    if fake_data_alert:
        # Display fake data alert with red background for high visibility
        st.markdown(f'<div style="color: #ffffff; background-color: #dc2626; padding: 15px; border-radius: 5px; font-weight: 600; border: 2px solid #991b1b;">{common.esc(fake_data_alert)}</div>', unsafe_allow_html=True)

    success = st.session_state.get("analysis_success")
    if success:
        if success == "The uploaded contract has been done !!!":
            # Display this specific message in white text with black background
            st.markdown(f'<div style="color: #ffffff; background-color: #000000; padding: 10px; border-radius: 5px; font-weight: 600;">{common.esc(success)}</div>', unsafe_allow_html=True)
        else:
            common.write_success(success)
        # Clear success message after showing it once (only for upload page)
        if success == "The uploaded contract has been done !!!":
            st.session_state["analysis_success"] = None

    # Add clear notifications button if any notifications exist
    if error or security_alert or fake_data_alert or success:
        if st.button("Clear notifications", use_container_width=False):
            st.session_state["analysis_error"] = None
            st.session_state["analysis_security_alert"] = None
            st.session_state["analysis_fake_data_alert"] = None
            st.session_state["analysis_success"] = None

    with st.container():
        st.markdown("### Upload")
        # Use a unique key that includes the analysis count to force widget recreation
        uploader_key = f"contract_uploader_{int(st.session_state.get('uploader_nonce', 0))}_{int(st.session_state.get('analysis_count', 0))}"
        uploaded = st.file_uploader(
            "Contract file",
            type=common.UPLOAD_TYPES,
            accept_multiple_files=False,
            help=f"PDF or DOCX, up to {settings.max_upload_mb} MB",
            key=uploader_key,
        )

        from core.llm_decider import llm_backend_available as _llm_avail

        _backend_ok = _llm_avail()
        if not _backend_ok:
            st.warning(
                "LLM mode requires either `llama-cpp-python` + a GGUF model file, "
                "or an OpenAI-compatible API key. "
                "Set `OPENAI_API_KEY` in `.env` or install llama-cpp-python. "
                "Using Rules engine instead."
            )
        mode = st.radio(
            "Analysis mode",
            ["Rules engine", "LLM from RAG (local)"],
            horizontal=True,
            help="LLM from RAG: RAG finds clauses, local GGUF decides from those passages only. Verifier still applies.",
            disabled=not _backend_ok,
        )
        use_llm_decider = _backend_ok and mode == "LLM from RAG (local)"
        use_llm = False

        analyze_col, clear_col = st.columns([1.2, 1])
        analyze_clicked = analyze_col.button(
            "Analyze contract",
            type="primary",
            use_container_width=True,
            disabled=uploaded is None,
        )
        if clear_col.button("Clear uploaded file", use_container_width=True):
            common.clear_analysis()
            st.session_state["uploader_nonce"] = (
                int(st.session_state.get("uploader_nonce", 0)) + 1
            )
            st.rerun()

        if analyze_clicked and uploaded is not None:
            data = uploaded.getvalue()
            if not data:
                st.session_state["analysis_error"] = "The uploaded file is empty."
                st.rerun()
            elif len(data) > settings.max_upload_mb * 1024 * 1024:
                st.session_state["analysis_error"] = (
                    f"File is {len(data) / (1024 * 1024):.1f} MB; the limit is "
                    f"{settings.max_upload_mb} MB."
                )
                st.rerun()
            else:
                # Clear previous analysis before running new one
                common.clear_analysis()
                path = _save_upload(uploaded.name, data)
                # Store the actual file size for verification
                st.session_state["uploaded_file_size"] = len(data)
                _execute(path, uploaded.name, use_llm, is_demo=False, use_llm_decider=use_llm_decider)

        st.caption("What this does: Extracts text → Checks security → Evaluates clauses → Verifies evidence → Classifies risks")

        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        st.markdown("### Demo")
        options = common.sample_options()
        selected = st.selectbox(
            "Demo sample",
            list(options.keys()),
            index=0,
            key="demo_sample",
        )
        demo_mode = st.radio(
            "Demo mode",
            ["Rules engine", "LLM from RAG (local)"],
            horizontal=True,
            key="demo_mode",
            disabled=not _backend_ok,
        )
        demo_use_llm_decider = _backend_ok and demo_mode == "LLM from RAG (local)"
        demo_use_llm = False
        if st.button("Run demo analysis", type="primary", use_container_width=True):
            path = options[selected]
            if not path.exists():
                st.session_state["analysis_error"] = "Demo sample missing"
                st.rerun()
            else:
                # Clear previous analysis before running new one
                common.clear_analysis()
                _execute(path, path.name, demo_use_llm, is_demo=True, use_llm_decider=demo_use_llm_decider)

        st.caption("What this does: Extracts text → Checks security → Evaluates clauses → Verifies evidence → Classifies risks")

        # Display analysis stats for both demo and uploaded files
        result = common.get_result()
        if result:
            st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
            st.markdown("### Analysis Results")

            # Debug: show current source and document hash
            source_name = st.session_state.get("source_name", "Unknown")
            source_path = st.session_state.get("source_path", "Unknown")
            doc_hash = st.session_state.get("document_sha256", "Unknown")
            st.caption(f"File: {source_name}")
            st.caption(f"Path: {source_path}")
            if doc_hash and doc_hash != "Unknown":
                st.caption(f"Hash: {doc_hash[:16]}...")
            else:
                st.caption(f"Hash: {doc_hash or 'Unknown'}")
            st.caption(f"Document chars: {result.document.stats.num_chars}")
            st.caption(f"Document words: {result.document.stats.num_words}")

            summary = result.summary
            
            # Create stats display
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Confirmed Conflicts", summary.confirmed_conflicts)
            col2.metric("Review Required", summary.review_required)
            col3.metric("Verified Evidence", f"{summary.verified_evidence}/{summary.evidence_bearing_findings}")
            col4.metric("Not Verified", summary.not_verified)
            
            # Show risk breakdown
            st.markdown("**Risk Breakdown:**")
            for category in ["HIGH", "MEDIUM", "LOW", "REVIEW_REQUIRED", "NO_CONFLICT_FOUND", "NOT_FOUND", "NOT_VERIFIED"]:
                count = 0
                for finding in result.findings:
                    if finding.status.value == category:
                        count += 1
                if count > 0:
                    st.markdown(f"- {category.replace('_', ' ').title()}: {count}")
            
            # Show action buttons
            nav_col1, nav_col2, nav_col3 = st.columns(3)
            if nav_col1.button("View Risk Dashboard", use_container_width=True, key="view_dashboard"):
                common.request_navigation("Risk dashboard")
            if nav_col2.button("View Evidence", use_container_width=True, key="view_evidence"):
                common.request_navigation("Evidence explorer")
            if nav_col3.button("Export Report", use_container_width=True, key="export"):
                common.request_navigation("Review & export")
