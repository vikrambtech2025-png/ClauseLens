"""Build the fine-tuning dataset from generated contracts.

Runs each contract through the real retrieval pipeline, so the context the model
trains on is produced by exactly the code that will serve it. Gold labels come
from the generator's manifest, never from the LLM.

Two things are measured and reported rather than silently fixed:
  * retrieval recall -- how often the retrieved passages actually contain the
    clause the label refers to. Examples where they do not are dropped: asking
    the model to decide from evidence it cannot see teaches it to guess.
  * label/engine agreement is measured separately by scripts/audit_labels.py.

Run:
    .\\.venv\\Scripts\\python.exe scripts\\build_dataset.py --limit 20
    .\\.venv\\Scripts\\python.exe scripts\\build_dataset.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from core.analysis import AnalysisPipeline
from finetune.prompt import (
    MAX_PASSAGES_PER_CATEGORY,
    build_context,
    build_messages,
    gold_to_target,
)
from finetune.schema import GoldDecision, PassageContext
from models.schemas import PaymentBasis, PolicyConfig, PolicyPreferences

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "generated" / "manifest.jsonl"
OUT_DIR = ROOT / "data" / "dataset"

NEEDS_QUOTE = {"HIGH", "MEDIUM", "LOW", "REVIEW_REQUIRED", "NO_CONFLICT_FOUND"}


def policy_config_from(row: dict) -> PolicyConfig:
    raw = row["policy"]
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


def find_source(passages: list[PassageContext], quote: str) -> Optional[str]:
    if not quote:
        return None
    for passage in passages:
        if quote in passage.text:
            return passage.source_id
    return None


def split_for(contract_id: str) -> str:
    bucket = int(hashlib.sha256(contract_id.encode("utf-8")).hexdigest(), 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "dev"
    return "test"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="0 means all")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out

    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        rows = rows[: args.limit]

    settings = replace(get_settings(), retrieved_passages_per_category=MAX_PASSAGES_PER_CATEGORY)
    pipeline = AnalysisPipeline(settings=settings)

    out_dir.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict]] = defaultdict(list)

    recall_hits = Counter()
    recall_total = Counter()
    dropped = 0
    context_chars: list[int] = []
    warnings: Counter[str] = Counter()

    for row in rows:
        policy = policy_config_from(row)
        result = pipeline.analyze_text(
            row["text"], filename=f"{row['contract_id']}.md", policy=policy
        )
        context = build_context(result.retrieval, policy)

        decisions = [GoldDecision.model_validate(g) for g in row["gold"]]
        resolved: list[GoldDecision] = []
        unanswerable = False

        for decision in decisions:
            if decision.status in NEEDS_QUOTE:
                recall_total[decision.category] += 1
                source_id = find_source(
                    [p for c in context.categories for p in c.passages],
                    decision.exact_quote,
                )
                if source_id is None:
                    warnings[
                        f"retrieval_miss:{decision.category}"
                    ] += 1
                    unanswerable = True
                    continue
                recall_hits[decision.category] += 1
                decision = decision.model_copy(update={"source_id": source_id})
            resolved.append(decision)

        if unanswerable or len(resolved) < len(decisions):
            dropped += 1
            continue

        target = gold_to_target(resolved, context.policy)
        messages = build_messages(context, target)
        context_chars.append(len(messages[1]["content"]))

        record = {
            "example_id": f"{row['contract_id']}-rag",
            "contract_id": row["contract_id"],
            "source": "synthetic",
            "context": context.model_dump(mode="json"),
            "messages": messages,
            "gold": [d.model_dump(mode="json") for d in resolved],
            "reason_keys": [d.reason_key for d in resolved],
        }
        buckets[split_for(row["contract_id"])].append(record)

    for split, records in buckets.items():
        path = out_dir / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    total_hits = sum(recall_hits.values())
    total_needed = sum(recall_total.values())
    stats = {
        "contracts_in_manifest": len(rows),
        "examples_written": sum(len(v) for v in buckets.values()),
        "dropped_unanswerable": dropped,
        "retrieval_recall_overall": round(total_hits / total_needed, 4) if total_needed else None,
        "retrieval_recall_by_category": {
            cat: round(recall_hits[cat] / recall_total[cat], 4)
            for cat in recall_total
        },
        "split_sizes": {k: len(v) for k, v in sorted(buckets.items())},
        "status_mix": dict(
            Counter(
                d["status"] for records in buckets.values() for r in records for d in r["gold"]
            )
        ),
        "user_message_chars": {
            "mean": int(statistics.mean(context_chars)) if context_chars else 0,
            "max": max(context_chars) if context_chars else 0,
        },
        "retrieval_misses": dict(warnings),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
