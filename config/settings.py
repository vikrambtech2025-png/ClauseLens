"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "ClauseLens"
    app_version: str = "1.0.0"
    max_upload_mb: int = 10
    max_pdf_pages: int = 300
    retrieved_passages_per_category: int = 8

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    openai_base_url: str = ""
    llm_timeout_seconds: float = 30.0
    allow_external_llm: bool = True

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key) and self.allow_external_llm

    @property
    def llm_status_label(self) -> str:
        if not self.allow_external_llm:
            return "AI interpretation disabled by configuration"
        if not self.openai_api_key:
            return "AI interpretation unavailable (no API key)"
        return f"AI interpretation available ({self.llm_provider}/{self.llm_model})"


def get_settings() -> Settings:
    return Settings(
        max_upload_mb=_int_env("CLAUSELENS_MAX_UPLOAD_MB", 10),
        max_pdf_pages=_int_env("CLAUSELENS_MAX_PDF_PAGES", 300),
        retrieved_passages_per_category=_int_env("CLAUSELENS_TOP_K", 8),
        llm_provider=os.getenv("CLAUSELENS_LLM_PROVIDER", "openai").strip() or "openai",
        llm_model=os.getenv("CLAUSELENS_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
        llm_timeout_seconds=_float_env("CLAUSELENS_LLM_TIMEOUT_SECONDS", 30.0),
        allow_external_llm=_bool_env("CLAUSELENS_ALLOW_EXTERNAL_LLM", True),
    )


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
