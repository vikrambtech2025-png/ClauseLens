"""Ground-truth label specification.

This module states, in one place, what a correct decision *is*. It is
deliberately an independent implementation of the policy semantics rather than
an import from ``core/policy_engine.py``.

Why duplicate something I criticised elsewhere? Because a label must not be
produced by the system being measured. Keeping this separate means the two can
disagree, and the disagreement is measurable (see ``scripts/audit_labels.py``).
If this module imported the engine, the benchmark would be circular again --
the exact flaw in the original six-sample suite.

The spec, in full:

payment
    no payment clause                 -> NOT_FOUND
    days is None (open-ended)         -> HIGH
    days present, basis differs from policy basis -> REVIEW_REQUIRED
    days present, condition is acceptance/dispute -> REVIEW_REQUIRED
    days > max_payment_days           -> HIGH
    preferred < days <= max_days      -> LOW
    days <= preferred                 -> NO_CONFLICT_FOUND

renewal_termination
    no renewal/term clause            -> NOT_FOUND
    renews automatically, not allowed -> HIGH
    renews automatically, allowed, no notice period given -> HIGH
    renews automatically, allowed, notice < minimum -> MEDIUM
    renews automatically, allowed, notice >= minimum -> NO_CONFLICT_FOUND
    does not renew automatically      -> NO_CONFLICT_FOUND

liability_indemnity
    no liability clause               -> NOT_FOUND
    uncapped, cap required            -> HIGH
    uncapped, cap not required        -> NO_CONFLICT_FOUND
    carveout present                  -> REVIEW_REQUIRED
    capped for any other reason       -> NO_CONFLICT_FOUND
    one-way indemnity                 -> LOW    (mutual indemnity is a preference)

jurisdiction_dispute
    no governing law clause           -> NOT_FOUND
    more than one forum named         -> REVIEW_REQUIRED
    law approved, exclusive and exclusivity not allowed -> MEDIUM
    law approved                      -> NO_CONFLICT_FOUND
    law not approved                  -> HIGH

Severity conflict rule: when a category produces more than one candidate
status, the most severe wins in the order HIGH > MEDIUM > LOW >
REVIEW_REQUIRED > NO_CONFLICT_FOUND. REVIEW_REQUIRED is treated as *less*
severe than LOW because it is an explicit request for a human, not an
assertion of conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from finetune.schema import (
    GoldDecision,
    IndemnityShape,
    JurisdictionFact,
    LiabilityCap,
    LiabilityFact,
    PaymentBasis,
    PaymentCondition,
    PaymentFact,
    RenewalFact,
)

SEVERITY_ORDER = {
    "HIGH": 5,
    "MEDIUM": 4,
    "LOW": 3,
    "REVIEW_REQUIRED": 2,
    "NO_CONFLICT_FOUND": 1,
    "NOT_FOUND": 0,
}


@dataclass(frozen=True)
class Policy:
    max_payment_days: int = 30
    payment_basis: PaymentBasis = "calendar"
    preferred_payment_days: int = 15
    allow_auto_renewal: bool = False
    min_renewal_notice_days: int = 30
    liability_must_be_capped: bool = True
    approved_jurisdictions: tuple[str, ...] = ("Delaware", "New York", "California")
    allow_exclusive_jurisdiction: bool = True


@dataclass(frozen=True)
class Planting:
    """What the generator decided to put in the contract.

    A ``None`` value with ``present=True`` means the clause exists but does not
    state the fact (an open-ended deadline, an unstated cap). ``present=False``
    means the clause family is absent, which must yield NOT_FOUND.
    """

    payment_present: bool = True
    payment_days: Optional[int] = 30
    payment_basis: Optional[PaymentBasis] = "calendar"
    payment_condition: PaymentCondition = "none"

    renewal_present: bool = True
    auto_renewal: Optional[bool] = False
    notice_days: Optional[int] = 30

    liability_present: bool = True
    liability_cap: LiabilityCap = "capped_fees"
    indemnity: IndemnityShape = "mutual"

    jurisdiction_present: bool = True
    governing_law: Optional[str] = "Delaware"
    ambiguous_forum: bool = False
    exclusive: bool = False


def _most_severe(candidates: list[tuple[str, str]]) -> tuple[str, str]:
    return max(candidates, key=lambda c: SEVERITY_ORDER[c[0]])


def label_payment(planting: Planting, policy: Policy) -> GoldDecision:
    fact = PaymentFact(
        days=planting.payment_days,
        basis=planting.payment_basis,
        condition=planting.payment_condition,
    )

    def decision(status: str, key: str) -> GoldDecision:
        return GoldDecision(
            category="payment", status=status, payment=fact, reason_key=key
        )

    if not planting.payment_present:
        return decision("NOT_FOUND", "payment.absent")
    if planting.payment_days is None:
        return decision("HIGH", "payment.open_ended")

    # A basis mismatch is an ambiguity, not a violation, so it competes with the
    # other candidates instead of short-circuiting them: a 90-day deadline
    # measured in business days is still HIGH, not merely REVIEW_REQUIRED.
    candidates: list[tuple[str, str]] = []
    if planting.payment_basis is not None and planting.payment_basis != policy.payment_basis:
        candidates.append(("REVIEW_REQUIRED", "payment.basis_mismatch"))
    if planting.payment_condition != "none":
        candidates.append(("REVIEW_REQUIRED", "payment.conditional"))
    if planting.payment_days > policy.max_payment_days:
        candidates.append(("HIGH", "payment.exceeds_limit"))
    elif planting.payment_days > policy.preferred_payment_days:
        candidates.append(("LOW", "payment.above_preference"))
    else:
        candidates.append(("NO_CONFLICT_FOUND", "payment.within_limit"))
    return decision(*_most_severe(candidates))


def label_renewal(planting: Planting, policy: Policy) -> GoldDecision:
    fact = (
        RenewalFact(
            auto_renewal=planting.auto_renewal, notice_days=planting.notice_days
        )
        if planting.renewal_present
        else RenewalFact()
    )

    def decision(status: str, key: str) -> GoldDecision:
        return GoldDecision(
            category="renewal_termination",
            status=status,
            renewal=fact,
            reason_key=key,
        )

    if not planting.renewal_present:
        return decision("NOT_FOUND", "renewal.absent")
    if not planting.auto_renewal:
        return decision("NO_CONFLICT_FOUND", "renewal.no_auto_trigger")

    candidates: list[tuple[str, str]] = []
    if not policy.allow_auto_renewal:
        candidates.append(("HIGH", "renewal.auto_prohibited"))
    if planting.notice_days is None:
        candidates.append(("HIGH", "renewal.notice_missing"))
    elif planting.notice_days < policy.min_renewal_notice_days:
        candidates.append(("MEDIUM", "renewal.notice_below_minimum"))
    else:
        candidates.append(("NO_CONFLICT_FOUND", "renewal.within_policy"))
    return decision(*_most_severe(candidates))


def label_liability(planting: Planting, policy: Policy) -> GoldDecision:
    fact = (
        LiabilityFact(cap=planting.liability_cap, indemnity=planting.indemnity)
        if planting.liability_present
        else LiabilityFact(cap="none", indemnity="none")
    )

    def decision(status: str, key: str) -> GoldDecision:
        return GoldDecision(
            category="liability_indemnity",
            status=status,
            liability=fact,
            reason_key=key,
        )

    if not planting.liability_present:
        return decision("NOT_FOUND", "liability.absent")

    candidates: list[tuple[str, str]] = []
    if planting.liability_cap == "uncapped":
        if policy.liability_must_be_capped:
            candidates.append(("HIGH", "liability.uncapped_prohibited"))
        else:
            candidates.append(("NO_CONFLICT_FOUND", "liability.uncapped_allowed"))
    elif planting.liability_cap == "carveout":
        candidates.append(("REVIEW_REQUIRED", "liability.carveout"))
    else:
        candidates.append(("NO_CONFLICT_FOUND", "liability.cap_present"))

    if planting.indemnity == "one_way":
        # Mutual indemnity is a soft preference in the policy model
        # (PolicyPreferences.prefer_mutual_indemnity), so a one-way indemnity is
        # LOW, not a hard-constraint breach.
        candidates.append(("LOW", "liability.one_way_indemnity"))

    return decision(*_most_severe(candidates))


def label_jurisdiction(planting: Planting, policy: Policy) -> GoldDecision:
    fact = (
        JurisdictionFact(
            governing_law=planting.governing_law, exclusive=planting.exclusive
        )
        if planting.jurisdiction_present
        else JurisdictionFact()
    )

    def decision(status: str, key: str) -> GoldDecision:
        return GoldDecision(
            category="jurisdiction_dispute",
            status=status,
            jurisdiction=fact,
            reason_key=key,
        )

    if not planting.jurisdiction_present or planting.governing_law is None:
        return decision("NOT_FOUND", "jurisdiction.absent")
    if planting.ambiguous_forum:
        return decision("REVIEW_REQUIRED", "jurisdiction.ambiguous_forum")

    approved = {j.casefold() for j in policy.approved_jurisdictions}
    if planting.governing_law.casefold() in approved:
        if planting.exclusive and not policy.allow_exclusive_jurisdiction:
            return decision("MEDIUM", "jurisdiction.exclusive_prohibited")
        return decision("NO_CONFLICT_FOUND", "jurisdiction.approved")
    return decision("HIGH", "jurisdiction.not_approved")


EVIDENCE_KEY = {
    "payment": "payment",
    "renewal_termination": "renewal",
    "liability_indemnity": "liability",
    "jurisdiction_dispute": "jurisdiction",
}


def label_all(
    planting: Planting, policy: Policy, evidence: Optional[dict[str, str]] = None
) -> list[GoldDecision]:
    """Build all four gold decisions and attach the verbatim source sentence.

    ``evidence`` maps a clause family to the exact prose the generator wrote for
    it. It is optional so the truth table can be tested without a contract.
    """
    evidence = evidence or {}
    decisions = [
        label_payment(planting, policy),
        label_renewal(planting, policy),
        label_liability(planting, policy),
        label_jurisdiction(planting, policy),
    ]

    quoted: list[GoldDecision] = []
    for decision in decisions:
        if decision.status == "NOT_FOUND":
            quoted.append(decision)
            continue
        key = EVIDENCE_KEY[decision.category]
        if decision.reason_key == "liability.one_way_indemnity":
            key = "indemnity"
        quote = evidence.get(key, "")
        quoted.append(decision.model_copy(update={"exact_quote": quote}))
    return quoted
