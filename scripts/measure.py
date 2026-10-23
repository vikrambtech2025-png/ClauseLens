"""Run the labeled synthetic benchmark and write measured results.

This is a small, fully synthetic regression benchmark, not a legal evaluation.
Every expected label below was written down from the synthetic contract
fixtures before running the measurement. The script records actual latency,
label agreement, evidence verification, and location validity.

Run from the project root:
    .\\.venv\\Scripts\\python.exe scripts\\measure.py
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings  # noqa: E402
from core.analysis import AnalysisPipeline  # noqa: E402
from models.schemas import FindingStatus, PolicyConfig, VerificationStatus  # noqa: E402

SAMPLES = ROOT / "samples"
OUTPUT = ROOT / "evaluation" / "measurement_results.json"
REPEATS = 5

DEMO_LABELS = {
    "payment.days_exceed_limit": "HIGH",
    "payment.open_ended_deadline": "REVIEW_REQUIRED",
    "payment.within_limit": "NO_CONFLICT_FOUND",
    "renewal.auto_renewal_prohibited": "HIGH",
    "liability.uncapped_prohibited": "HIGH",
    "jurisdiction.not_approved": "HIGH",
    "jurisdiction.ambiguous_forum": "REVIEW_REQUIRED",
}

BENCHMARK = {
    "demo_contract.docx": {
        "labels": DEMO_LABELS,
        "categories": 4,
        "evidence_bearing": 7,
        "requires_injection_warning": False,
        "scan_needs_ocr": False,
    },
    "demo_contract.pdf": {
        "labels": DEMO_LABELS,
        "categories": 4,
        "evidence_bearing": 7,
        "requires_injection_warning": False,
        "scan_needs_ocr": False,
    },
    "demo_missing_category.docx": {
        "labels": {
            "payment.above_preference": "LOW",
            "renewal.no_auto_trigger": "NO_CONFLICT_FOUND",
            "liability.cap_present": "NO_CONFLICT_FOUND",
            "coverage.not_found": "NOT_FOUND",
        },
        "categories": 3,
        "evidence_bearing": 3,
        "requires_injection_warning": False,
        "scan_needs_ocr": False,
    },
    "compliant_contract.docx": {
        "labels": {
            "payment.within_limit": "NO_CONFLICT_FOUND",
            "renewal.no_auto_trigger": "NO_CONFLICT_FOUND",
            "liability.cap_present": "NO_CONFLICT_FOUND",
            "jurisdiction.approved": "NO_CONFLICT_FOUND",
        },
        "categories": 4,
        "evidence_bearing": 4,
        "requires_injection_warning": False,
        "scan_needs_ocr": False,
    },
    "prompt_injection_contract.docx": {
        "labels": {
            "payment.days_exceed_limit": "HIGH",
            "coverage.not_verified": "NOT_VERIFIED",
            "liability.uncapped_prohibited": "HIGH",
            "jurisdiction.not_approved": "MEDIUM",
        },
        "categories": 3,
        "evidence_bearing": 3,
        "requires_injection_warning": True,
        "scan_needs_ocr": False,
    },
    "scanned_no_text.pdf": {
        "labels": {
            "coverage.not_verified": "NOT_VERIFIED",
        },
        "categories": 0,
        "evidence_bearing": 0,
        "requires_injection_warning": False,
        "scan_needs_ocr": True,
        "repeat_label_count": 4,
    },
}

CONFIRMED = {FindingStatus.HIGH.value, FindingStatus.MEDIUM.value}


def _expected_counter(spec: dict) -> Counter:
    labels = Counter(
        {(rule_id, status): 1 for rule_id, status in spec["labels"].items()}
    )
    repeat = int(spec.get("repeat_label_count", 1))
    if repeat > 1:
        # The scanned sample emits one NOT_VERIFIED coverage finding per category.
        only_label = next(iter(labels))
        labels = Counter({only_label: repeat})
    return labels


def _location_is_valid(result, finding) -> bool:
    span = result.document.span_by_id(finding.source_id)
    location = result.document.location_by_id(finding.source_id)
    if span is None or location is None:
        return False
    if finding.span_start is None or finding.span_end is None:
        return False
    if not (0 <= finding.span_start < finding.span_end <= len(span.text)):
        return False
    if span.text[finding.span_start : finding.span_end].strip() != finding.exact_quote:
        return False
    if span.page_number is not None:
        return finding.page_number == span.page_number and location.page_number == span.page_number
    return finding.paragraph_index == span.paragraph_index and location.paragraph_index == span.paragraph_index


def run() -> dict:
    pipeline = AnalysisPipeline(settings=Settings(allow_external_llm=False))
    policy = PolicyConfig()
    total_actual = 0
    total_expected = 0
    total_matched = 0
    confirmed_actual = 0
    confirmed_expected = 0
    confirmed_matched = 0
    evidence_total = 0
    evidence_verified = 0
    location_correct = 0
    location_total = 0
    all_latencies: list[float] = []
    samples_payload: list[dict] = []
    compliant_false_confirmed = 0
    missing_not_found_ok = False
    scanned_not_verified_ok = False
    injection_warning_ok = False
    injection_quarantine_ok = False

    for filename, spec in BENCHMARK.items():
        path = SAMPLES / filename
        latencies: list[float] = []
        result = None
        for _ in range(REPEATS):
            started = time.perf_counter()
            result = pipeline.analyze_file(str(path), filename, policy)
            latencies.append((time.perf_counter() - started) * 1000)
        assert result is not None
        all_latencies.extend(latencies)

        expected = _expected_counter(spec)
        actual = Counter(
            (finding.rule_id or "", finding.status.value) for finding in result.findings
        )
        matched = sum((actual & expected).values())
        total_actual += sum(actual.values())
        total_expected += sum(expected.values())
        total_matched += matched

        actual_confirmed = Counter(
            (finding.rule_id or "", finding.status.value)
            for finding in result.findings
            if finding.status.value in CONFIRMED
        )
        expected_confirmed = Counter(
            {key: value for key, value in expected.items() if key[1] in CONFIRMED}
        )
        matched_confirmed = sum((actual_confirmed & expected_confirmed).values())
        confirmed_actual += sum(actual_confirmed.values())
        confirmed_expected += sum(expected_confirmed.values())
        confirmed_matched += matched_confirmed

        sample_evidence = 0
        sample_verified = 0
        sample_location_ok = 0
        sample_location_total = 0
        for finding in result.findings:
            if finding.has_evidence:
                sample_evidence += 1
                if finding.verification_status == VerificationStatus.VERIFIED:
                    sample_verified += 1
                sample_location_total += 1
                if finding.verification_status == VerificationStatus.VERIFIED and _location_is_valid(
                    result, finding
                ):
                    sample_location_ok += 1
        evidence_total += sample_evidence
        evidence_verified += sample_verified
        location_total += sample_location_total
        location_correct += sample_location_ok

        if filename == "compliant_contract.docx":
            compliant_false_confirmed = sum(actual_confirmed.values())
        if filename == "demo_missing_category.docx":
            missing_not_found_ok = result.summary.not_found == 1
        if filename == "scanned_no_text.pdf":
            scanned_not_verified_ok = (
                result.document.stats.needs_ocr
                and result.summary.not_verified == 4
                and result.summary.confirmed_conflicts == 0
            )
        if filename == "prompt_injection_contract.docx":
            injection_warning_ok = any("injection" in w.lower() for w in result.warnings)
            injection_quarantine_ok = all(
                "ignore all previous instructions" not in f.exact_quote.lower()
                for f in result.findings
            )

        samples_payload.append(
            {
                "file": filename,
                "file_type": result.document.file_type,
                "findings": len(result.findings),
                "high": result.summary.high,
                "medium": result.summary.medium,
                "low": result.summary.low,
                "review_required": result.summary.review_required,
                "no_conflict_found": result.summary.no_conflict_found,
                "not_found": result.summary.not_found,
                "not_verified": result.summary.not_verified,
                "confirmed_conflicts": result.summary.confirmed_conflicts,
                "categories_detected": result.summary.categories_detected,
                "expected_categories": spec["categories"],
                "evidence_bearing_findings": sample_evidence,
                "verified_evidence": sample_verified,
                "location_correct": sample_location_ok,
                "labels_expected": sum(expected.values()),
                "labels_matched": matched,
                "mean_latency_ms": round(statistics.mean(latencies), 2),
                "median_latency_ms": round(statistics.median(latencies), 2),
                "warnings": result.warnings,
            }
        )

    # Honest offline fallback test: request AI without any configured provider.
    fallback_pipeline = AnalysisPipeline(
        settings=Settings(openai_api_key="", allow_external_llm=False)
    )
    fallback_started = time.perf_counter()
    fallback = fallback_pipeline.analyze_file(
        str(SAMPLES / "demo_contract.docx"),
        "demo_contract.docx",
        policy,
        use_llm=True,
    )
    fallback_ms = (time.perf_counter() - fallback_started) * 1000

    label_precision = total_matched / total_actual if total_actual else None
    label_recall = total_matched / total_expected if total_expected else None
    confirmed_precision = confirmed_matched / confirmed_actual if confirmed_actual else None
    confirmed_recall = confirmed_matched / confirmed_expected if confirmed_expected else None
    evidence_rate = evidence_verified / evidence_total if evidence_total else None
    location_accuracy = location_correct / location_total if location_total else None

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "ClauseLens labeled synthetic benchmark",
        "disclaimer": (
            "Synthetic regression benchmark only. It is not a legal evaluation, not a "
            "measurement of legal accuracy, and not evidence of performance on real contracts."
        ),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "method": {
            "repeats_per_sample": REPEATS,
            "warmup_excluded": False,
            "llm_mode": "offline deterministic; optional AI explicitly unconfigured",
            "label_unit": "rule_id + status",
        },
        "samples": samples_payload,
        "overall": {
            "samples": len(samples_payload),
            "actual_labels": total_actual,
            "expected_labels": total_expected,
            "labels_matched": total_matched,
            "label_precision": round(label_precision, 4) if label_precision is not None else None,
            "label_recall": round(label_recall, 4) if label_recall is not None else None,
            "confirmed_findings_actual": confirmed_actual,
            "confirmed_findings_expected": confirmed_expected,
            "confirmed_finding_precision": (
                round(confirmed_precision, 4) if confirmed_precision is not None else None
            ),
            "confirmed_finding_recall": (
                round(confirmed_recall, 4) if confirmed_recall is not None else None
            ),
            "evidence_bearing_findings": evidence_total,
            "verified_evidence": evidence_verified,
            "evidence_verification_rate": (
                round(evidence_rate, 4) if evidence_rate is not None else None
            ),
            "location_total": location_total,
            "location_correct": location_correct,
            "location_accuracy": round(location_accuracy, 4) if location_accuracy is not None else None,
            "compliant_contract_false_confirmed": compliant_false_confirmed,
            "missing_category_reported_not_found": missing_not_found_ok,
            "scanned_pdf_reported_not_verified": scanned_not_verified_ok,
            "prompt_injection_warning_present": injection_warning_ok,
            "prompt_injection_instruction_not_used_as_evidence": injection_quarantine_ok,
        },
        "latency_ms": {
            "per_sample": {
                sample["file"]: {
                    "mean": sample["mean_latency_ms"],
                    "median": sample["median_latency_ms"],
                }
                for sample in samples_payload
            },
            "overall": {
                "mean": round(statistics.mean(all_latencies), 2),
                "median": round(statistics.median(all_latencies), 2),
                "p95": round(float(np.percentile(all_latencies, 95)), 2),
                "max": round(max(all_latencies), 2),
                "runs": len(all_latencies),
            },
        },
        "llm_fallback": {
            "requested": fallback.llm_status.requested,
            "used": fallback.llm_status.used,
            "available": fallback.llm_status.available,
            "analysis_mode": fallback.analysis_mode,
            "findings": len(fallback.findings),
            "confirmed_conflicts": fallback.summary.confirmed_conflicts,
            "latency_ms": round(fallback_ms, 2),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def print_summary(payload: dict) -> None:
    overall = payload["overall"]
    latency = payload["latency_ms"]["overall"]
    print("ClauseLens measured synthetic benchmark")
    print("-" * 72)
    print(f"Samples: {overall['samples']}")
    print(f"Label precision: {overall['label_precision']:.4f} "
          f"({overall['labels_matched']}/{overall['actual_labels']})")
    print(f"Label recall: {overall['label_recall']:.4f} "
          f"({overall['labels_matched']}/{overall['expected_labels']})")
    print(f"Confirmed-finding precision: {overall['confirmed_finding_precision']:.4f} "
          f"({overall['confirmed_findings_actual']} actual)")
    print(f"Evidence verification: {overall['evidence_verification_rate']:.4f} "
          f"({overall['verified_evidence']}/{overall['evidence_bearing_findings']})")
    print(f"Location accuracy: {overall['location_accuracy']:.4f} "
          f"({overall['location_correct']}/{overall['location_total']})")
    print(f"Compliant contract false confirmed: "
          f"{overall['compliant_contract_false_confirmed']}")
    print(f"Missing category reported NOT_FOUND: "
          f"{overall['missing_category_reported_not_found']}")
    print(f"Scanned PDF reported NOT_VERIFIED: "
          f"{overall['scanned_pdf_reported_not_verified']}")
    print(f"Prompt injection warning: {overall['prompt_injection_warning_present']} "
          f"(instruction used as evidence: "
          f"{not overall['prompt_injection_instruction_not_used_as_evidence']})")
    print(f"Latency median / p95 / max: {latency['median']:.2f} / "
          f"{latency['p95']:.2f} / {latency['max']:.2f} ms over {latency['runs']} runs")
    print(f"AI fallback completed deterministically: "
          f"{not payload['llm_fallback']['used'] and payload['llm_fallback']['findings'] == 7}")
    print()
    print("Per-sample results:")
    for sample in payload["samples"]:
        print(
            f"  {sample['file']:<34} findings={sample['findings']:<2} "
            f"confirmed={sample['confirmed_conflicts']:<2} "
            f"categories={sample['categories_detected']}/{sample['expected_categories']} "
            f"evidence={sample['verified_evidence']}/{sample['evidence_bearing_findings']} "
            f"labels={sample['labels_matched']}/{sample['labels_expected']} "
            f"median={sample['median_latency_ms']:.2f} ms"
        )
    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":
    print_summary(run())
