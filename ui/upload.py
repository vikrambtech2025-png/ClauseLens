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


def _execute(path: Path, name: str, use_llm: bool) -> None:
    try:
        with st.status("Analyzing…", expanded=True) as status:
            result = common.run_analysis(str(path), name, use_llm)
            status.update(
                label=f"Done · {len(result.findings)} findings",
                state="complete",
                expanded=False,
            )
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

    with st.container():
        st.markdown("### Upload")
        uploaded = st.file_uploader(
            "Contract file",
            type=common.UPLOAD_TYPES,
            accept_multiple_files=False,
            help=f"PDF or DOCX, up to {settings.max_upload_mb} MB",
            key=f"contract_uploader_{int(st.session_state.get('uploader_nonce', 0))}",
        )
        use_llm = st.checkbox(
            "Use AI interpretation",
            value=False,
            disabled=not settings.llm_configured,
            help=settings.llm_status_label,
            key="use_llm_toggle",
        )

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
                path = _save_upload(uploaded.name, data)
                _execute(path, uploaded.name, use_llm)

        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        st.markdown("### Demo")
        options = common.sample_options()
        selected = st.selectbox(
            "Demo sample",
            list(options.keys()),
            index=0,
            key="demo_sample",
        )
        demo_use_llm = st.checkbox(
            "Use AI interpretation",
            value=False,
            disabled=not settings.llm_configured,
            key="demo_llm",
        )
        if st.button("Run demo analysis", type="primary", use_container_width=True):
            path = options[selected]
            if not path.exists():
                st.session_state["analysis_error"] = "Demo sample missing"
                st.rerun()
            else:
                _execute(path, path.name, demo_use_llm)
