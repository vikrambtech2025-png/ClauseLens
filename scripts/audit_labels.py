"""Measure agreement between the label specification and the shipped engine.

The point of this script is to be able to say, with a number, that the training
labels are not just a copy of the rule engine's output. The label spec
(``finetune/labels.py``) is written independently from ``core/policy_engine.py``;
where they disagree, one of them is wrong, and it matters which.

Comparison unit is (contract, category). The engine can emit several findings
per category because it evaluates each retrieved passage separately, so the
most severe verdict per category is used, and NOT_VERIFIED is ignored because it
reports verification rather than risk.

Run:
    .\\.venv\\Scripts\\python.exe scripts\\audit_labels.py --limit 300
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from core.analysis import AnalysisPipeline
from finetune.labels import SEVERITY_ORDER
from models.schemas import PaymentBasis, PolicyConfig, PolicyPreferences

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "generated" / "manifest.jsonl"

IGNORED = {"NOT_VERIFIED"}


def policy_config_from(raw: dict) -> PolicyConfig:
    return PolicyConfig(
        max_payment_days=raw["max_payment_days"],
        payment_basis=PaymentBasis(raw["payment_basis"]),
        allow_auto_renewal=raw["allow_auto_renewal"],
        min_renewal_notice_days=raw["min_renewal_notice_days"],
        liability_must_be_capped=raw["liability_must_be_capped"],
        approved_jurisdictions=list(raw["approved_jurisdictions"]),
        allow_exclusive_jurisdiction=raw["allow_exclusive_jurisdiction"],
        preferences=PolicyPreferences(
            preferred_payment_days=raw["preferred_payment_days"]
        ),
    )


def worst_by_category(findings) -> dict[str, str]:
    out: dict[str, str] = {}
    for finding in findings:
        status = finding.status.value
        if status in IGNORED:
            continue
        category = finding.clause_category.value
        current = out.get(category)
        if current is None or SEVERITY_ORDER.get(status, -1) > SEVERITY_ORDER.get(current, -1):
            out[category] = status
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show", type=int, default=12)
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="retrieved passages per category; must match the model's context",
    )
    args = parser.parse_args()

    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    rows = [json.loads(l) for l in manifest.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[: args.limit]

    pipeline = AnalysisPipeline(
        settings=replace(get_settings(), retrieved_passages_per_category=args.top_k)
    )

    agree = Counter()
    total = Counter()
    confusion: dict[str, Counter] = defaultdict(Counter)
    examples: list[dict] = []

    for row in rows:
        policy = policy_config_from(row["policy"])
        result = pipeline.analyze_text(row["text"], filename="audit.md", policy=policy)
        engine = worst_by_category(result.findings)

        for gold in row["gold"]:
            category = gold["category"]
            expected = gold["status"]
            actual: Optional[str] = engine.get(category)
            total[category] += 1
            confusion[expected][actual or "MISSING"] += 1
            if actual == expected:
                agree[category] += 1
            elif len(examples) < args.show:
                examples.append(
                    {
                        "contract": row["contract_id"],
                        "category": category,
                        "label": expected,
                        "engine": actual,
                        "reason_key": gold["reason_key"],
                        "quote": gold["exact_quote"][:140],
                    }
                )

    print(f"contracts: {len(rows)}   decisions: {sum(total.values())}")
    print()
    grand_total = sum(total.values())
    print(f"{'category':<24}{'agree':>7}{'total':>7}{'rate':>8}")
    print("-" * 46)
    for category in total:
        rate = agree[category] / total[category]
        print(f"{category:<24}{agree[category]:>7}{total[category]:>7}{rate:>8.1%}")
    print("-" * 46)
    print(f"{'OVERALL':<24}{sum(agree.values()):>7}{grand_total:>7}{sum(agree.values())/grand_total:>8.1%}")

    print("\nlabel -> engine confusion (label rows, engine columns)")
    for label in ["HIGH", "MEDIUM", "LOW", "REVIEW_REQUIRED", "NO_CONFLICT_FOUND", "NOT_FOUND"]:
        row = confusion.get(label)
        if not row:
            continue
        cells = ", ".join(f"{k}={v}" for k, v in row.most_common())
        print(f"  {label:<18} {cells}")

    if examples:
        print("\ndisagreements:")
        for example in examples:
            print(
                f"  {example['category']:<22} label={example['label']:<17} "
                f"engine={example['engine']} :: {example['reason_key']}"
            )


if __name__ == "__main__":
    main()
