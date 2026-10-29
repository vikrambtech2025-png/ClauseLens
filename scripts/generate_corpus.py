"""Parametric contract generator with planted ground truth.

The order of operations matters:

    1. choose a policy            (what the reviewer is checking against)
    2. choose a planting          (what the contract actually says)
    3. derive the gold label      (from 1 + 2, never from the text)
    4. realise the text           (describe the planting in prose)

Because step 3 never reads the generated prose, the labels cannot inherit the
rule engine's mistakes. This is what makes the corpus usable for training when
the original six samples were not.

Run:
    .\\.venv\\Scripts\\python.exe scripts\\generate_corpus.py --count 1500 --seed 7
    .\\.venv\\Scripts\\python.exe scripts\\generate_corpus.py --count 20 --out data/preview
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from finetune.labels import Planting, Policy, label_all
from finetune.schema import LiabilityCap, PaymentBasis, PaymentCondition

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "generated"

# --------------------------------------------------------------------------
# number words
# --------------------------------------------------------------------------

_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def num_words(n: int) -> str:
    if n < 20:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] + (f"-{_UNITS[unit]}" if unit else "")
    hundreds, rest = divmod(n, 100)
    head = f"{_UNITS[hundreds]} hundred"
    return head if not rest else f"{head} and {num_words(rest)}"


def day_token(rng: random.Random, days: int, basis: str, style: Optional[str] = None) -> str:
    """Render a day count in one of several realistic surface forms."""
    style = style or rng.choice(["words_digits", "digits", "words", "words_digits"])
    unit = "business" if basis == "business" else rng.choice(["calendar", "calendar", ""])
    suffix = f" {unit} days".replace("  ", " ") if unit else " days"
    if style == "digits":
        return f"{days}{suffix}"
    if style == "words":
        return f"{num_words(days)}{suffix}"
    return f"{num_words(days)} ({days}){suffix}"


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

PAYMENT_DAYS = [0, 7, 10, 15, 20, 30, 45, 60, 90, 120]
NOTICE_DAYS = [0, 10, 15, 30, 45, 60, 90]
MAX_PAYMENT_OPTIONS = [15, 30, 45, 60]
MIN_NOTICE_OPTIONS = [15, 30, 60]
PREFERRED_OPTIONS = [7, 10, 15, 20]

APPROVED_POOL = {
    "Delaware": "the State of Delaware",
    "New York": "the State of New York",
    "California": "the State of California",
    "England and Wales": "England and Wales",
    "Singapore": "Singapore",
    "Ontario": "the Province of Ontario",
}

PARTIES = [
    ("Northstar Studio", "Meridian Retail Ltd"),
    ("Cobalt Systems Inc", "Harbour Point Logistics LLC"),
    ("Lumen Analytics Ltd", "Vantage Health Group"),
    ("Orchard Software BV", "Kestrel Manufacturing GmbH"),
    ("Pinewood Consulting LLP", "Aurora Financial Services"),
    ("Ridgeway Data Corp", "Blue Harbour Media Inc"),
    ("Silverline Robotics", "Northgate Utilities plc"),
    ("Atlas Cloud Services", "Cedar Valley Foods"),
]

AGREEMENT_TITLES = [
    "MUTUAL SERVICES AGREEMENT",
    "MASTER SERVICES AGREEMENT",
    "PROFESSIONAL SERVICES AGREEMENT",
    "SOFTWARE LICENCE AGREEMENT",
    "SUPPLY AGREEMENT",
    "CONSULTING AGREEMENT",
    "MANAGED SERVICES AGREEMENT",
]


def sample_policy(rng: random.Random) -> Policy:
    approved = rng.sample(list(APPROVED_POOL), rng.choice([2, 3, 3, 4]))
    return Policy(
        max_payment_days=rng.choice(MAX_PAYMENT_OPTIONS),
        payment_basis=rng.choice(["calendar", "calendar", "calendar", "business"]),
        preferred_payment_days=rng.choice(PREFERRED_OPTIONS),
        allow_auto_renewal=rng.random() < 0.35,
        min_renewal_notice_days=rng.choice(MIN_NOTICE_OPTIONS),
        liability_must_be_capped=rng.random() < 0.8,
        approved_jurisdictions=tuple(approved),
        allow_exclusive_jurisdiction=rng.random() < 0.7,
    )


def sample_planting(rng: random.Random, policy: Policy) -> Planting:
    payment_present = rng.random() < 0.94
    payment_days: Optional[int]
    roll = rng.random()
    if roll < 0.05:
        payment_days = None                     # open-ended deadline
    else:
        payment_days = rng.choice(PAYMENT_DAYS)

    payment_basis: Optional[PaymentBasis] = (
        "business" if rng.random() < 0.18 else "calendar"
    )
    condition: PaymentCondition = rng.choices(
        ["none", "acceptance", "dispute", "other"],
        weights=[62, 20, 14, 4],
    )[0]

    auto_renewal = rng.choices([False, True], weights=[42, 58])[0]
    notice_days: Optional[int]
    if not auto_renewal:
        notice_days = rng.choice([None, 30, 60])
    elif rng.random() < 0.12:
        notice_days = None                      # silent auto-renewal
    else:
        notice_days = rng.choice(NOTICE_DAYS)

    cap: LiabilityCap = rng.choices(
        ["uncapped", "capped_fees", "capped_fixed", "carveout"],
        weights=[32, 42, 14, 12],
    )[0]
    indemnity = rng.choices(["mutual", "one_way", "none"], weights=[48, 34, 18])[0]

    approved = {j.casefold() for j in policy.approved_jurisdictions}
    pool = list(APPROVED_POOL)
    if rng.random() < 0.45:
        law = rng.choice(pool)                  # unconstrained: may be approved
    else:
        outside = [k for k in pool if k.casefold() not in approved]
        law = rng.choice(outside) if outside else rng.choice(pool)

    return Planting(
        payment_present=payment_present,
        payment_days=payment_days if payment_present else None,
        payment_basis=payment_basis if payment_present else None,
        payment_condition=condition,
        renewal_present=rng.random() < 0.96,
        auto_renewal=auto_renewal,
        notice_days=notice_days,
        liability_present=rng.random() < 0.95,
        liability_cap=cap,
        indemnity=indemnity,
        jurisdiction_present=rng.random() < 0.95,
        governing_law=law,
        ambiguous_forum=rng.random() < 0.08,
        exclusive=rng.random() < 0.45,
    )


# --------------------------------------------------------------------------
# realisation
# --------------------------------------------------------------------------

def payment_clause(rng: random.Random, p: Planting) -> Optional[str]:
    if not p.payment_present:
        return None
    days = p.payment_days
    basis = p.payment_basis or "calendar"

    if days is None:
        return rng.choice(
            [
                "All invoices shall be paid within a reasonable time after receipt.",
                "The Customer shall pay each invoice promptly following delivery of the Services.",
                "Payment shall be made at such time as the Customer considers appropriate.",
                "Invoices are payable in accordance with the Customer's standard payment run.",
            ]
        )

    if p.payment_condition == "acceptance":
        return rng.choice(
            [
                f"The Customer shall pay each invoice within {day_token(rng, days, basis)} of its written acceptance of the deliverables.",
                f"Payment falls due {day_token(rng, days, basis)} after the Customer confirms acceptance in writing.",
            ]
        )
    if p.payment_condition == "dispute":
        return rng.choice(
            [
                f"Undisputed invoices are payable within {day_token(rng, days, basis)} of receipt.",
                f"The Customer shall settle each invoice within {day_token(rng, days, basis)} of receipt, provided it has not been disputed.",
            ]
        )
    if p.payment_condition == "other":
        return rng.choice(
            [
                f"Invoices are payable within {day_token(rng, days, basis)} of receipt, subject to the Customer's satisfaction with the Services.",
                f"Payment shall be made within {day_token(rng, days, basis)} of the Supplier completing all deliverables to the Customer's reasonable satisfaction.",
            ]
        )

    style = "net" if (basis == "calendar" and rng.random() < 0.18) else None
    if style == "net":
        return rng.choice(
            [
                f"Invoices are payable net {days} from the invoice date.",
                f"Payment terms are net {days} days.",
            ]
        )
    return rng.choice(
        [
            f"The Customer shall pay each invoice within {day_token(rng, days, basis, style)} of the invoice date.",
            f"All undisputed amounts are due within {day_token(rng, days, basis, style)} of receipt of a valid invoice.",
            f"Payment is due {day_token(rng, days, basis, style)} after the invoice is issued.",
            f"The Customer shall remit payment within {day_token(rng, days, basis, style)} of the date of the invoice.",
        ]
    )


def renewal_clause(rng: random.Random, p: Planting) -> Optional[str]:
    if not p.renewal_present:
        return None
    if not p.auto_renewal:
        return rng.choice(
            [
                "This Agreement runs for an initial term of twelve (12) months and expires without automatic renewal.",
                "The Term is fixed at twenty-four (24) months from the Effective Date and shall not renew automatically.",
                "This Agreement terminates at the end of the Initial Term unless the parties sign a new written agreement.",
            ]
        )

    if p.notice_days is None:
        return rng.choice(
            [
                "This Agreement shall automatically renew for successive terms of twelve (12) months. Either party may terminate for convenience on written notice.",
                "The Term renews automatically for additional one-year periods, and either party may elect not to renew by giving written notice.",
            ]
        )

    notice = day_token(rng, p.notice_days, "calendar")
    return rng.choice(
        [
            f"This Agreement commences on the Effective Date and will automatically renew for successive twelve (12) month terms unless either party gives written notice of non-renewal at least {notice} before the expiration of the then-current term.",
            f"Upon expiry of the Initial Term the Agreement shall renew automatically for further periods of twelve (12) months. Notice of non-renewal must be delivered no later than {notice} prior to expiry.",
            f"The Term shall renew automatically unless a party gives not less than {notice} written notice of termination before the end of the current term.",
        ]
    )


def liability_clause(rng: random.Random, p: Planting) -> Optional[str]:
    if not p.liability_present:
        return None
    if p.liability_cap == "uncapped":
        return rng.choice(
            [
                "The Supplier's liability under this Agreement shall be uncapped, and the Supplier shall have no limitation of liability whatsoever.",
                "Nothing in this Agreement limits the Supplier's liability, which shall be unlimited in amount.",
                "The Supplier accepts unlimited liability for all losses arising under this Agreement.",
            ]
        )
    if p.liability_cap == "capped_fees":
        return rng.choice(
            [
                "The Supplier's aggregate liability shall not exceed the fees paid in the twelve (12) months preceding the claim.",
                "Each party's total liability is limited to the amounts invoiced under this Agreement during the preceding twelve (12) months.",
            ]
        )
    if p.liability_cap == "capped_fixed":
        amount = rng.choice([50_000, 100_000, 250_000, 500_000])
        return (
            f"The Supplier's aggregate liability under this Agreement shall not exceed "
            f"{rng.choice(['USD', 'GBP', 'EUR'])} {amount:,}."
        )
    # carveout: capped, but with unlimited exceptions
    return rng.choice(
        [
            "Liability is limited to the fees paid in the preceding twelve (12) months, save that nothing limits liability for fraud, death or personal injury, or breach of confidentiality.",
            "Each party's liability is capped at the charges paid, except for indemnity obligations, which remain uncapped.",
        ]
    )


def indemnity_clause(rng: random.Random, p: Planting) -> Optional[str]:
    if not p.liability_present or p.indemnity == "none":
        return None

    # When the contract caps liability, an indemnity that does not say so reads
    # as unlimited exposure. Real drafting ties the two together, so do that.
    capped = p.liability_cap in {"capped_fees", "capped_fixed"}
    qualifier = (
        rng.choice(
            [
                ", subject to the liability cap above",
                ", subject to the aggregate liability cap in this Agreement",
            ]
        )
        if capped
        else ""
    )

    if p.indemnity == "mutual":
        body = rng.choice(
            [
                "Each party shall indemnify the other against third-party claims arising from its breach of this Agreement",
                "The parties give reciprocal indemnities in respect of claims arising from their respective negligence",
            ]
        )
    else:
        body = rng.choice(
            [
                "The Supplier shall indemnify the Customer against all third-party claims, and the Customer gives no reciprocal indemnity",
                "The Customer shall be indemnified by the Supplier in full against any claims, losses or liabilities, but the Customer assumes no corresponding obligation",
            ]
        )
    return f"{body}{qualifier}."


def jurisdiction_clause(rng: random.Random, p: Planting) -> Optional[str]:
    if not p.jurisdiction_present or p.governing_law is None:
        return None
    law = APPROVED_POOL.get(p.governing_law, p.governing_law)

    if p.ambiguous_forum:
        other = rng.choice([v for v in APPROVED_POOL.values() if v != law])
        return (
            f"This Agreement is governed by the laws of {law}, save that the claimant may "
            f"instead bring proceedings in the courts of {other}."
        )

    if p.exclusive:
        return (
            f"This Agreement is governed by the laws of {law}, and the parties submit to the "
            f"exclusive jurisdiction of the courts of {law}."
        )
    return rng.choice(
        [
            f"This Agreement is governed by the laws of {law}.",
            f"The laws of {law} govern this Agreement and its interpretation.",
            f"This Agreement shall be construed under the laws of {law}, and the parties submit to the non-exclusive jurisdiction of its courts.",
        ]
    )


DECOYS: list[tuple[str, str]] = [
    ("Confidentiality", "Each party shall protect the other's confidential information for three (3) years after termination."),
    ("Assignment", "Neither party may assign this Agreement without the prior written consent of the other."),
    ("Force Majeure", "Neither party is liable for delay caused by events beyond its reasonable control, provided it notifies the other within five (5) business days."),
    ("Project Governance", "The parties shall meet within ten (10) business days of the Effective Date to agree a project plan."),
    ("Insurance", "The Supplier shall maintain insurance cover and shall give thirty (30) days notice of cancellation of that cover."),
    ("Variation", "Any amendment to this Agreement must be in writing and signed by both parties."),
    ("Severability", "If any provision is held invalid, the remaining provisions continue in full force."),
    ("Entire Agreement", "This Agreement constitutes the entire agreement between the parties and supersedes prior discussions."),
    ("Counterparts", "The parties may execute this Agreement in counterparts, including by electronic signature."),
    ("Records and Audit", "The Supplier shall keep complete records of the Services for two (2) years and make them available on request."),
    ("Definitions", "Capitalised terms have the meanings given to them in Schedule 1."),
    ("Non-solicitation", "Neither party shall solicit the other's employees during the Term and for six (6) months afterwards."),
    ("Subcontracting", "The Supplier may subcontract any part of the Services with the Customer's prior written consent."),
    ("Warranties", "The Supplier warrants that the Services will be performed with reasonable skill and care."),
]


def decoy_clauses(rng: random.Random) -> list[tuple[str, str]]:
    return rng.sample(DECOYS, rng.randint(6, 10))


def build_contract(
    rng: random.Random, policy: Policy, planting: Planting
) -> tuple[str, dict[str, str]]:
    """Return the contract prose and the verbatim sentence for each family."""
    supplier, customer = rng.choice(PARTIES)
    title = rng.choice(AGREEMENT_TITLES)
    effective = date(2025, 1, 1) + timedelta(days=rng.randint(0, 400))

    blocks: list[tuple[str, str]] = []
    evidence: dict[str, str] = {}

    payment = payment_clause(rng, planting)
    if payment:
        blocks.append(("Fees and Payment", payment))
        evidence["payment"] = payment

    renewal = renewal_clause(rng, planting)
    if renewal:
        blocks.append(("Term and Renewal", renewal))
        evidence["renewal"] = renewal

    liability = liability_clause(rng, planting)
    indemnity = indemnity_clause(rng, planting)
    if liability:
        # Cap and indemnity live in one clause, as they usually do in practice.
        # Splitting them made the engine see an indemnity with no visible cap,
        # which it correctly flagged as ambiguous -- an artefact of generation.
        body = f"{liability} {indemnity}" if indemnity else liability
        blocks.append(("Limitation of Liability", body))
        evidence["liability"] = body
        if indemnity:
            evidence["indemnity"] = indemnity
    elif indemnity:
        blocks.append(("Indemnity", indemnity))
        evidence["indemnity"] = indemnity

    jurisdiction = jurisdiction_clause(rng, planting)
    if jurisdiction:
        blocks.append(("Governing Law and Disputes", jurisdiction))
        evidence["jurisdiction"] = jurisdiction

    blocks.append(
        (
            "Services",
            "The Supplier shall provide the services described in Schedule A in a professional and workmanlike manner.",
        )
    )
    for heading, text in decoy_clauses(rng):
        blocks.append((heading, text))

    rng.shuffle(blocks)

    lines = [
        title,
        "",
        f"This Agreement is made on {effective.strftime('%d %B %Y')} between {supplier} "
        f"(the Supplier) and {customer} (the Customer).",
        "",
    ]
    for number, (heading, body) in enumerate(blocks, start=1):
        lines.append(f"{number}. {heading}. {body}")
        # Blank line between clauses: real contracts break paragraphs, and the
        # splitter treats consecutive lines as one block without it.
        lines.append("")
    lines.append(
        "IN WITNESS WHEREOF the parties have executed this Agreement as of the date first written above."
    )
    return "\n".join(lines) + "\n", evidence


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def contract_id_for(index: int, seed: int, text: str) -> str:
    digest = hashlib.sha256(f"{seed}:{index}:{text}".encode("utf-8")).hexdigest()[:10]
    return f"syn-{seed}-{index:05d}-{digest}"


def generate(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    for index in range(count):
        policy = sample_policy(rng)
        planting = sample_planting(rng, policy)
        text, evidence = build_contract(rng, policy, planting)
        contract_id = contract_id_for(index, seed, text)

        rows.append(
            {
                "contract_id": contract_id,
                "seed": seed,
                "index": index,
                "policy": asdict(policy),
                "planting": asdict(planting),
                "evidence": evidence,
                "gold": [
                    g.model_dump(mode="json")
                    for g in label_all(planting, policy, evidence)
                ],
                "text": text,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--keep-text",
        action="store_true",
        help="write each contract as a .md file (needed before running the pipeline)",
    )
    args = parser.parse_args()

    rows = generate(args.count, args.seed)

    out: Path = args.out if args.out.is_absolute() else (ROOT / args.out)
    out.mkdir(parents=True, exist_ok=True)
    contracts_dir = out / "contracts"
    if args.keep_text:
        contracts_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if args.keep_text:
                path = contracts_dir / f"{row['contract_id']}.md"
                path.write_text(row["text"], encoding="utf-8")
                row["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"contracts : {len(rows)}")
    print(f"manifest  : {manifest_path}")
    if args.keep_text:
        print(f"text dir  : {contracts_dir}")

    from collections import Counter

    statuses = Counter(g["status"] for row in rows for g in row["gold"])
    print("status mix:")
    for status, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {n:>5}  {n / (len(rows) * 4):.1%}")


if __name__ == "__main__":
    main()
