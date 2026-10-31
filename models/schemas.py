"""Typed domain models for ClauseLens.

All analysis output is validated through these models. Findings separate the
product risk status from quote/location verification so the UI can never
present an unverified quotation as confirmed evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClauseCategory(str, Enum):
    PAYMENT = "payment"
    RENEWAL_TERMINATION = "renewal_termination"
    LIABILITY_INDEMNITY = "liability_indemnity"
    JURISDICTION_DISPUTE = "jurisdiction_dispute"

    @property
    def label(self) -> str:
        return {
            "payment": "Payment terms",
            "renewal_termination": "Automatic renewal & termination",
            "liability_indemnity": "Liability & indemnification",
            "jurisdiction_dispute": "Governing law & disputes",
        }[self.value]


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FindingStatus(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NO_CONFLICT_FOUND = "NO_CONFLICT_FOUND"
    NOT_FOUND = "NOT_FOUND"
    NOT_VERIFIED = "NOT_VERIFIED"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED_QUOTE_MATCH = "FAILED_QUOTE_MATCH"
    FAILED_LOCATION = "FAILED_LOCATION"
    FAILED_POLICY_SUPPORT = "FAILED_POLICY_SUPPORT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    AMBIGUOUS = "AMBIGUOUS"


class PaymentBasis(str, Enum):
    CALENDAR = "calendar"
    BUSINESS = "business"


class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    NEGOTIATE = "negotiate"
    SEEK_LEGAL_ADVICE = "seek_legal_advice"


class PolicyPreferences(BaseModel):
    """Soft preferences. They can create low-priority or review findings."""

    preferred_payment_days: int = Field(default=15, ge=0, le=365)
    prefer_mutual_indemnity: bool = True
    flag_auto_renewal_even_if_allowed: bool = False
    require_written_notice: bool = True
    notes: str = Field(default="", max_length=2000)


class PolicyConfig(BaseModel):
    """User policy. Hard constraints drive confirmed conflicts; preferences
    drive lower-severity or review findings."""

    max_payment_days: int = Field(default=30, ge=0, le=365)
    payment_basis: PaymentBasis = PaymentBasis.CALENDAR
    allow_auto_renewal: bool = False
    min_renewal_notice_days: int = Field(default=30, ge=0, le=365)
    liability_must_be_capped: bool = True
    approved_jurisdictions: list[str] = Field(
        default_factory=lambda: ["Delaware", "New York", "California", "United States"]
    )
    allow_exclusive_jurisdiction: bool = True
    preferences: PolicyPreferences = Field(default_factory=PolicyPreferences)

    @field_validator("approved_jurisdictions")
    @classmethod
    def clean_jurisdictions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(str(value).split()).strip(" ,;")
            key = item.casefold()
            if item and key not in seen:
                cleaned.append(item)
                seen.add(key)
        if not cleaned:
            raise ValueError("At least one approved jurisdiction is required.")
        return cleaned

    @model_validator(mode="after")
    def validate_preferences(self) -> "PolicyConfig":
        if (
            self.preferences.preferred_payment_days > self.max_payment_days
            and self.max_payment_days > 0
        ):
            # Not invalid, but confusing. Keep the policy; the engine explains it.
            pass
        return self

    def hard_constraint_snapshot(self) -> dict[str, Any]:
        return {
            "max_payment_days": self.max_payment_days,
            "payment_basis": self.payment_basis.value,
            "allow_auto_renewal": self.allow_auto_renewal,
            "min_renewal_notice_days": self.min_renewal_notice_days,
            "liability_must_be_capped": self.liability_must_be_capped,
            "approved_jurisdictions": self.approved_jurisdictions,
            "allow_exclusive_jurisdiction": self.allow_exclusive_jurisdiction,
        }

    def preferences_snapshot(self) -> dict[str, Any]:
        return self.preferences.model_dump()


class SourceLocation(BaseModel):
    source_id: str
    page_number: Optional[int] = None
    paragraph_index: Optional[int] = None
    block_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    source_type: str = "document"

    @property
    def human_location(self) -> str:
        parts: list[str] = []
        if self.page_number is not None:
            parts.append(f"Page {self.page_number}")
        if self.paragraph_index is not None:
            parts.append(f"Paragraph {self.paragraph_index}")
        if self.table_index is not None:
            parts.append(f"Table {self.table_index}")
        if self.row_index is not None:
            parts.append(f"Row {self.row_index}")
        if not parts:
            parts.append("Location unavailable")
        return " · ".join(parts)


class DocumentSpan(BaseModel):
    source_id: str
    text: str
    page_number: Optional[int] = None
    paragraph_index: Optional[int] = None
    block_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    section_id: Optional[str] = None
    source_type: str = "document"
    # Offsets are relative to this span's exact text, not to a normalized copy.
    char_start: int = 0
    char_end: int = 0

    @model_validator(mode="after")
    def validate_offsets(self) -> "DocumentSpan":
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError("Invalid span offsets")
        if self.char_end > len(self.text):
            raise ValueError("Span offset exceeds source text length")
        return self


class DocumentStats(BaseModel):
    filename: str
    file_type: str
    num_pages: Optional[int] = None
    num_paragraphs: int = 0
    num_tables: int = 0
    num_blocks: int = 0
    num_chars: int = 0
    num_words: int = 0
    pages_with_text: Optional[int] = None
    extraction_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_ok: bool = True
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    needs_ocr: bool = False


class Document(BaseModel):
    filename: str
    file_type: Literal["pdf", "docx", "text"]
    raw_text: str
    spans: list[DocumentSpan] = Field(default_factory=list)
    stats: DocumentStats
    source_map: dict[str, SourceLocation] = Field(default_factory=dict)
    sha256: Optional[str] = None

    def span_by_id(self, source_id: str) -> Optional[DocumentSpan]:
        for span in self.spans:
            if span.source_id == source_id:
                return span
        return None

    def location_by_id(self, source_id: str) -> Optional[SourceLocation]:
        return self.source_map.get(source_id)


class ClauseSegment(BaseModel):
    clause_id: str
    text: str
    source_id: str
    page_number: Optional[int] = None
    paragraph_index: Optional[int] = None
    section_id: Optional[str] = None
    span_start: int = 0
    span_end: int = 0
    source_type: str = "document"


class RetrievedPassage(BaseModel):
    segment: ClauseSegment
    score: float = 0.0
    bm25_score: float = 0.0
    keyword_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    context_before: str = ""
    context_after: str = ""


class CategoryRetrieval(BaseModel):
    category: ClauseCategory
    passages: list[RetrievedPassage] = Field(default_factory=list)
    keyword_hits: int = 0
    has_signal: bool = False
    method: str = "lexical_hybrid"
    notes: str = ""


class Finding(BaseModel):
    finding_id: str
    clause_category: ClauseCategory
    risk_level: RiskLevel
    status: FindingStatus
    exact_quote: str = ""
    source_id: str = ""
    page_number: Optional[int] = None
    paragraph_index: Optional[int] = None
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    policy_violated: Optional[str] = None
    explanation: str
    recommended_action: str
    verification_status: VerificationStatus = VerificationStatus.INSUFFICIENT_EVIDENCE
    # Deliberately no fabricated numeric confidence by default.
    confidence: Optional[float] = None
    confidence_basis: str = (
        "No numeric confidence is assigned. Evidence quality is reported as a "
        "categorical grade derived from quote, location, and policy verification."
    )
    evidence_quality: str = ""
    uncertainty_reason: Optional[str] = None
    rule_id: Optional[str] = None
    is_ai_generated: bool = False
    policy_support_verified: bool = False
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    review_decision: Optional[ReviewDecision] = None
    review_notes: str = ""
    reviewer: str = ""
    reviewed_at: Optional[str] = None

    @property
    def is_confirmed(self) -> bool:
        return self.status in {
            FindingStatus.HIGH,
            FindingStatus.MEDIUM,
            FindingStatus.LOW,
        }

    @property
    def has_evidence(self) -> bool:
        return bool(self.exact_quote and self.source_id)


class AnalysisSummary(BaseModel):
    total_findings: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    review_required: int = 0
    no_conflict_found: int = 0
    not_found: int = 0
    not_verified: int = 0
    confirmed_conflicts: int = 0
    evidence_bearing_findings: int = 0
    verified_evidence: int = 0
    verification_rate: Optional[float] = None
    clauses_reviewed: int = 0
    retrieval_candidates: int = 0
    categories_detected: int = 0
    categories_retrieved: int = 0
    categories_total: int = 4
    extraction_coverage: float = 0.0
    unresolved_finding_ids: list[str] = Field(default_factory=list)
    by_category: dict[str, dict[str, int]] = Field(default_factory=dict)


class LLMStatus(BaseModel):
    requested: bool = False
    available: bool = False
    used: bool = False
    provider: str = "openai"
    model: str = ""
    error: Optional[str] = None
    privacy_note: str = (
        "Deterministic analysis runs locally. Contract passages are sent to the "
        "configured external provider only when the user enables AI interpretation."
    )


class AnalysisResult(BaseModel):
    schema_version: str = "1.0"
    document: Document
    policy: PolicyConfig
    retrieval: dict[str, CategoryRetrieval] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    summary: AnalysisSummary = Field(default_factory=AnalysisSummary)
    generated_at: str = Field(default_factory=utc_now_iso)
    analysis_mode: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)
    fake_data_detected: bool = False
    fake_data_reasons: list[str] = Field(default_factory=list)
    llm_status: LLMStatus = Field(default_factory=LLMStatus)
    processing_ms: float = 0.0
    pipeline_steps: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Decision support only; this is not legal advice.",
            "Lexical retrieval can miss unusual drafting; NOT_FOUND is not proof of absence.",
            "Business-day deadlines cannot be converted to exact calendar days without a holiday calendar.",
            "Severity is a product heuristic, not a legal determination.",
        ]
    )

    def finding_by_id(self, finding_id: str) -> Optional[Finding]:
        return next((f for f in self.findings if f.finding_id == finding_id), None)
