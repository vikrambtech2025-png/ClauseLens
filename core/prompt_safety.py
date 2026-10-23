"""Detection of untrusted instruction text inside contracts.

This is not a content filter. It identifies passages that look like attempts to
manipulate an automated reviewer (for example "ignore previous instructions").
Such passages are quarantined from deterministic rule matching because an
instruction is not a contractual term. The passage is still surfaced as a
warning so a human can inspect it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.schemas import ClauseSegment

INJECTION_PATTERN = re.compile(
    r"(?:"
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:of\s+)?"
    r"(?:the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions?|prompts?|rules?|directions?)\b"
    r"|\b(?:system|developer)\s+prompt\b"
    r"|\byou\s+are\s+now\s+(?:a|an|the)\b"
    r"|\b(?:report|state|declare|assume)\s+(?:that\s+)?(?:this\s+)?"
    r"(?:contract|agreement)\s+(?:is\s+)?(?:fully\s+)?(?:compliant|safe|risk[- ]free)\b"
    r"|\bdo\s+not\s+(?:mention|report|flag|disclose)\b"
    r"|\b(?:override|bypass|disable)\s+(?:the\s+)?(?:policy|rules?|checks?|safety)\b"
    r"|\breturn\s+(?:an?\s+)?(?:empty|no)\s+(?:findings?|issues?|risks?)\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InjectionHit:
    clause_id: str
    source_id: str
    paragraph_index: int | None
    page_number: int | None
    matched_text: str
    excerpt: str


def find_injection_hits(segments: list[ClauseSegment]) -> list[InjectionHit]:
    hits: list[InjectionHit] = []
    for segment in segments:
        match = INJECTION_PATTERN.search(segment.text)
        if not match:
            continue
        start = max(0, match.start() - 60)
        end = min(len(segment.text), match.end() + 80)
        hits.append(
            InjectionHit(
                clause_id=segment.clause_id,
                source_id=segment.source_id,
                paragraph_index=segment.paragraph_index,
                page_number=segment.page_number,
                matched_text=match.group(0),
                excerpt=segment.text[start:end].replace("\n", " ").strip(),
            )
        )
    return hits


def injection_warning(hits: list[InjectionHit]) -> str:
    locations: list[str] = []
    for hit in hits[:5]:
        if hit.page_number is not None:
            locations.append(f"page {hit.page_number}")
        elif hit.paragraph_index is not None:
            locations.append(f"paragraph {hit.paragraph_index}")
        else:
            locations.append(hit.source_id)
    suffix = "" if len(hits) <= 5 else f" and {len(hits) - 5} more"
    return (
        f"Potential prompt-injection language was detected in {len(hits)} passage(s) "
        f"({', '.join(locations)}{suffix}). Those passages were quarantined from rule "
        "matching; the quoted instruction is treated as untrusted text, not a contract term."
    )
