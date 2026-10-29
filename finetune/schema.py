"""Output contract for the fine-tuned decision model.

The model reads a *RAG context* (policy + retrieved passages) and returns one
decision per category. It never returns a rule id: rule ids belong to the
deterministic engine. The model's job is to extract the fact and apply the
policy, so that the existing evidence verifier can gate its quotation.

Keeping this schema small is deliberate. A 3B model on 6 GB VRAM has to emit
valid JSON on every call; every optional field is a chance to fail.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "payment",
    "renewal_termination",
    "liability_indemnity",
    "jurisdiction_dispute",
]

# Mirrors models.schemas.FindingStatus, minus the values the model must not
# invent (NOT_VERIFIED is assigned by the verifier, never by the model).
DecisionStatus = Literal[
    "HIGH",
    "MEDIUM",
    "LOW",
    "REVIEW_REQUIRED",
    "NO_CONFLICT_FOUND",
    "NOT_FOUND",
]

PaymentCondition = Literal["none", "acceptance", "dispute", "other"]
PaymentBasis = Literal["calendar", "business"]
LiabilityCap = Literal["uncapped", "capped_fees", "capped_fixed", "carveout", "none"]
IndemnityShape = Literal["mutual", "one_way", "none"]


class PaymentFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: Optional[int] = Field(default=None, ge=0, le=3650)
    basis: Optional[PaymentBasis] = None
    condition: PaymentCondition = "none"


class RenewalFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_renewal: Optional[bool] = None
    notice_days: Optional[int] = Field(default=None, ge=0, le=3650)


class LiabilityFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap: LiabilityCap = "none"
    indemnity: IndemnityShape = "none"


class JurisdictionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    governing_law: Optional[str] = None
    exclusive: Optional[bool] = None


class Decision(BaseModel):
    """One category decision. `exact_quote` must be copied verbatim from a
    passage in the context; the verifier rejects anything else."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    status: DecisionStatus
    payment: Optional[PaymentFact] = None
    renewal: Optional[RenewalFact] = None
    liability: Optional[LiabilityFact] = None
    jurisdiction: Optional[JurisdictionFact] = None
    exact_quote: str = ""
    source_id: str = ""
    reason: str = Field(default="", max_length=320)

    @field_validator("exact_quote")
    @classmethod
    def quote_not_whitespace_only(cls, value: str) -> str:
        return value.strip()


class DecisionBatch(BaseModel):
    """The model's full response for one contract."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[Decision] = Field(min_length=1, max_length=4)

    @field_validator("decisions")
    @classmethod
    def one_per_category(cls, values: list[Decision]) -> list[Decision]:
        seen = [d.category for d in values]
        if len(seen) != len(set(seen)):
            raise ValueError("duplicate category in decisions")
        return values


class PassageContext(BaseModel):
    """What the model sees for one retrieved passage."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    clause_id: str
    page_number: Optional[int] = None
    text: str


class CategoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    passages: list[PassageContext] = Field(default_factory=list)


class PolicySnapshot(BaseModel):
    """Hard constraints only. Soft preferences are deliberately excluded: the
    model decides against enforced policy, and preference-level nudges stay in
    the deterministic layer where they are cheap to explain."""

    model_config = ConfigDict(extra="forbid")

    max_payment_days: int
    payment_basis: PaymentBasis = "calendar"
    allow_auto_renewal: bool = False
    min_renewal_notice_days: int = 30
    liability_must_be_capped: bool = True
    approved_jurisdictions: list[str] = Field(default_factory=list)
    allow_exclusive_jurisdiction: bool = True


class RAGContext(BaseModel):
    """The complete model input for one contract."""

    model_config = ConfigDict(extra="forbid")

    policy: PolicySnapshot
    categories: list[CategoryContext] = Field(default_factory=list)


class GoldDecision(BaseModel):
    """Ground truth, written by the generator from what it planted."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    status: DecisionStatus
    payment: Optional[PaymentFact] = None
    renewal: Optional[RenewalFact] = None
    liability: Optional[LiabilityFact] = None
    jurisdiction: Optional[JurisdictionFact] = None
    reason_key: str = ""
    # The exact sentence the generator wrote for this category. Copied verbatim
    # so the training target and the verifier agree on what a valid quote is.
    exact_quote: str = ""
    source_id: str = ""


class TrainingExample(BaseModel):
    """One JSONL row."""

    model_config = ConfigDict(extra="forbid")

    example_id: str
    contract_id: str
    source: Literal["synthetic", "edgar"]
    context: RAGContext
    messages: list[dict[str, str]]
    gold: list[GoldDecision]
