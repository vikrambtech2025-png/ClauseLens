"""Heuristic fake / mocked / falsified document detector.

ClauseLens is a decision-support tool. This module adds a lightweight,
deterministic check that flags documents that look like fabricated or
placeholder content (e.g. a contract drafted to fool a partner) so the
user is alerted before relying on the analysis.

No LLM is required; all checks are local and fast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union

# ---- placeholder / template markers --------------------------------------
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\blorem\s+ipsum\b", re.IGNORECASE),
    re.compile(r"\bXXX+\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bdummy\b", re.IGNORECASE),
    re.compile(r"\bsample\s+(data|text|contract|agreement)\b", re.IGNORECASE),
    re.compile(r"\btest\s+(data|text|contract|agreement|clause)\b", re.IGNORECASE),
    re.compile(r"\bfoo\b", re.IGNORECASE),
    re.compile(r"\bbar\b", re.IGNORECASE),
    re.compile(r"\bexample\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\[insert[^\]]*\]", re.IGNORECASE),
    re.compile(r"\{\{[^}]*\}\}"),
    re.compile(r"<[^>]{1,40}>"),
    re.compile(r"_{3,}"),
    re.compile(r"\[name\]", re.IGNORECASE),
    re.compile(r"\[date\]", re.IGNORECASE),
    re.compile(r"\[amount\]", re.IGNORECASE),
    re.compile(r"\[company\]", re.IGNORECASE),
    re.compile(r"\[address\]", re.IGNORECASE),
]

# ---- essential contract element signals -----------------------------------
_PARTY_PATTERNS = [
    re.compile(r"\bbetween\b.{0,80}\band\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bparty\s+(a|b|one|two)\b", re.IGNORECASE),
    re.compile(r"\b(the\s+)?(provider|customer|vendor|client|licensor|licensee)\b", re.IGNORECASE),
    re.compile(r"\b(inc\.|llc|ltd\.?|corp\.?|gmbh|limited)\b", re.IGNORECASE),
]

_DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}\b"),
    re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\beffective\s+date\b", re.IGNORECASE),
    re.compile(r"\bdated\b", re.IGNORECASE),
]

_SIGNATURE_PATTERNS = [
    re.compile(r"\bsignature\b", re.IGNORECASE),
    re.compile(r"\bsigned\b", re.IGNORECASE),
    re.compile(r"\bexecuted\b", re.IGNORECASE),
    re.compile(r"\bby:\s*_", re.IGNORECASE),
]

_GOVERNING_LAW_PATTERNS = [
    re.compile(r"\bgoverning\s+law\b", re.IGNORECASE),
    re.compile(r"\bjurisdiction\b", re.IGNORECASE),
    re.compile(r"\bdispute\b", re.IGNORECASE),
    re.compile(r"\barbitration\b", re.IGNORECASE),
]

_AMOUNT_PATTERNS = [
    re.compile(r"\$\s*\d"),
    re.compile(r"\b\d+\s*(usd|eur|gbp|dollars?|euros?)\b", re.IGNORECASE),
    re.compile(r"\bfee(s)?\b", re.IGNORECASE),
    re.compile(r"\bpayment\b", re.IGNORECASE),
]


@dataclass
class FakeDataReport:
    is_fake: bool = False
    fake: bool = False
    detected: bool = False
    is_mock: bool = False
    is_falsified: bool = False
    score: int = 0
    threshold: int = 4
    reasons: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    verdict: str = "real"

    def __post_init__(self) -> None:
        self.is_fake = bool(self.is_fake)
        self.fake = self.is_fake
        self.detected = self.is_fake
        self.is_mock = self.is_fake
        self.is_falsified = self.is_fake
        self.verdict = "fake data" if self.is_fake else "real"


def _count_pattern_hits(text: str, patterns: Iterable[re.Pattern]) -> int:
    hits = 0
    for pat in patterns:
        if pat.search(text):
            hits += 1
    return hits


def _unique_word_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{3,}", text)
    if not words:
        return 0.0
    return len(set(w.casefold() for w in words)) / max(1, len(words))


def _repeated_paragraph_ratio(text: str) -> float:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 3:
        return 0.0
    seen: dict[str, int] = {}
    for p in paras:
        key = " ".join(p.split()).casefold()[:120]
        seen[key] = seen.get(key, 0) + 1
    dupes = sum(v - 1 for v in seen.values() if v > 1)
    return dupes / max(1, len(paras))


def _vowel_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    vowels = sum(1 for c in letters if c.lower() in "aeiou")
    return vowels / len(letters)


def _has_headings(text: str) -> bool:
    # markdown headings or numbered sections
    return bool(re.search(r"(^|\n)#{1,6}\s+\S", text)) or bool(
        re.search(r"(^|\n)\s*\d+\.\s+[A-Z]", text)
    )


class FakeDataDetector:
    """Detect fake/mocked/falsified document content."""

    def __init__(self, threshold: int = 4) -> None:
        self.threshold = threshold

    def detect(self, document_or_text: Union["Any", str]) -> FakeDataReport:
        # Accept Document, str, or anything with .raw_text
        if isinstance(document_or_text, str):
            text = document_or_text
        else:
            text = getattr(document_or_text, "raw_text", None)
            if text is None:
                text = str(document_or_text)
        text = text or ""
        reasons: list[str] = []
        indicators: list[str] = []
        score = 0

        # --- 1. placeholder / template markers -----------------------------
        placeholder_hits = []
        for pat in _PLACEHOLDER_PATTERNS:
            m = pat.search(text)
            if m:
                placeholder_hits.append(m.group(0)[:40])
        if placeholder_hits:
            unique = list(dict.fromkeys(placeholder_hits))[:8]
            reasons.append(
                "Placeholder/template markers found: " + ", ".join(unique)
            )
            indicators.append("placeholder_markers")
            score += min(3, len(unique))

        # --- 2. length / substance ---------------------------------------
        stripped = text.strip()
        if len(stripped) < 500:
            reasons.append("Document is very short (less than 500 characters).")
            indicators.append("too_short")
            score += 2
        if len(stripped.split()) < 80:
            reasons.append("Document has very few words.")
            indicators.append("few_words")
            score += 1

        # --- 3. text quality ---------------------------------------------
        ur = _unique_word_ratio(text)
        if ur and ur < 0.25 and len(text.split()) > 40:
            reasons.append(
                f"Low vocabulary diversity (unique-word ratio {ur:.2f})."
            )
            indicators.append("low_vocab_diversity")
            score += 2
        rr = _repeated_paragraph_ratio(text)
        if rr >= 0.4:
            reasons.append(
                f"Highly repetitive paragraphs (repeat ratio {rr:.2f})."
            )
            indicators.append("repetitive")
            score += 2
        vr = _vowel_ratio(text)
        if vr and vr < 0.25 and len(text) > 200:
            reasons.append("Text has an unusually low vowel ratio (possible gibberish).")
            indicators.append("gibberish")
            score += 2

        # --- 4. essential contract elements -------------------------------
        party_hits = _count_pattern_hits(text, _PARTY_PATTERNS)
        date_hits = _count_pattern_hits(text, _DATE_PATTERNS)
        sig_hits = _count_pattern_hits(text, _SIGNATURE_PATTERNS)
        gov_hits = _count_pattern_hits(text, _GOVERNING_LAW_PATTERNS)
        amt_hits = _count_pattern_hits(text, _AMOUNT_PATTERNS)

        missing = []
        if party_hits == 0:
            missing.append("parties")
        if date_hits == 0:
            missing.append("effective date")
        if sig_hits == 0:
            missing.append("signature block")
        if gov_hits == 0:
            missing.append("governing law / disputes")
        if amt_hits == 0:
            missing.append("payment / amounts")
        if missing:
            reasons.append("Missing typical contract elements: " + ", ".join(missing) + ".")
            indicators.append("missing_elements")
            score += min(3, len(missing))

        # --- 5. structure --------------------------------------------------
        if not _has_headings(text) and len(text.split()) > 60:
            reasons.append("No section headings or numbered clauses detected.")
            indicators.append("no_structure")
            score += 1

        # --- 6. suspicious all-caps blocks --------------------------------
        caps_blocks = re.findall(r"[A-Z]{20,}", text)
        if len(caps_blocks) >= 3:
            reasons.append("Multiple long all-caps runs (possible fabricated content).")
            indicators.append("caps_runs")
            score += 1

        is_fake = score >= self.threshold
        return FakeDataReport(
            is_fake=is_fake,
            score=score,
            threshold=self.threshold,
            reasons=reasons,
            indicators=indicators,
            signals=indicators,
        )


def detect_fake_data(document_or_text) -> FakeDataReport:
    """Convenience function."""
    return FakeDataDetector().detect(document_or_text)


def detect_text(text: str) -> FakeDataReport:
    """Alias: detect fake data from raw text."""
    return FakeDataDetector().detect(text)


# Class-level convenience
FakeDataDetector.detect_text = staticmethod(detect_text)


__all__ = ["FakeDataDetector", "FakeDataReport", "detect_fake_data", "detect_text"]