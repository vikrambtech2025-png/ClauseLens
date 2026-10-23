"""Small, dependency-free text helpers shared by the pipeline."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

_WS = re.compile(r"\s+")
_ABBREVIATIONS = {
    "no.",
    "nos.",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "inc.",
    "ltd.",
    "corp.",
    "co.",
    "vs.",
    "e.g.",
    "i.e.",
    "etc.",
    "u.s.",
    "u.k.",
    "art.",
    "sec.",
}


def normalize_whitespace(text: str) -> str:
    """Collapse whitespace without changing words or punctuation."""
    return _WS.sub(" ", (text or "").replace("\u00a0", " ")).strip()


def normalize_for_match(text: str) -> str:
    """Lowercased, punctuation-light representation used only for matching."""
    text = (text or "").casefold()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[^a-z0-9%$€£.'\-\s]", " ", text)
    return _WS.sub(" ", text).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str, fallback: str = "contract") -> str:
    """Strip directory components and unsafe characters from an uploaded name."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name[:180] or fallback


def truncate_with_notice(text: str, limit: int) -> tuple[str, bool]:
    if limit < 0:
        raise ValueError("limit must be >= 0")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _is_abbreviation(sentence: str) -> bool:
    tail = sentence.strip().casefold()
    return any(tail.endswith(abbr) for abbr in _ABBREVIATIONS)


def split_sentences_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Split text into sentence-like chunks while keeping exact offsets.

    This is intentionally conservative. If a boundary is ambiguous, the text is
    kept together rather than inventing a citation. Offsets always refer to the
    original ``text`` argument.
    """
    if not text:
        return []
    boundaries: list[int] = []
    for match in re.finditer(r"(?<=[.!?;])\s+(?=[A-Z0-9(\"'])", text):
        candidate = text[: match.start()]
        last = re.split(r"[\n]", candidate)[-1].strip()
        if not last:
            continue
        if last.endswith(";") and len(last) < 40:
            continue
        if _is_abbreviation(last):
            continue
        boundaries.append(match.start())

    chunks: list[tuple[str, int, int]] = []
    start = 0
    for boundary in boundaries:
        raw = text[start:boundary]
        if raw.strip():
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            chunks.append((raw.strip(), start + left, start + right))
        start = boundary
    raw = text[start:]
    if raw.strip():
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        chunks.append((raw.strip(), start + left, start + right))
    return chunks or [(text.strip(), len(text) - len(text.lstrip()), len(text.rstrip()))]


def split_long_segment(text: str, start: int, end: int, max_chars: int = 900) -> list[tuple[str, int, int]]:
    """Further split an overlong sentence at commas/semicolons.

    Only used for retrieval. Source offsets remain exact.
    """
    if end - start <= max_chars:
        return [(text, start, end)]
    pieces: list[tuple[str, int, int]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + max_chars, end)
        if window_end < end:
            cut_candidates = [
                text.rfind(", ", cursor + 100, window_end),
                text.rfind("; ", cursor + 100, window_end),
                text.rfind(" and ", cursor + 100, window_end),
                text.rfind(" ", cursor + 100, window_end),
            ]
            cut = max(cut_candidates)
            if cut > cursor:
                window_end = cut + 1
        piece = text[cursor:window_end].strip()
        if piece:
            left_pad = len(text[cursor:window_end]) - len(text[cursor:window_end].lstrip())
            pieces.append((piece, cursor + left_pad, window_end))
        cursor = window_end
    return pieces


def tokenize(text: str) -> list[str]:
    """Simple legal-text tokenizer with light plural normalization."""
    tokens = re.findall(r"[a-z0-9]+", normalize_for_match(text))
    result: list[str] = []
    for token in tokens:
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("es") and not token.endswith("ses"):
            token = token[:-2]
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        result.append(token)
    return result


def contains_any(text: str, terms: Iterable[str]) -> list[str]:
    norm = normalize_for_match(text)
    hits: list[str] = []
    for term in terms:
        if term.casefold() in norm:
            hits.append(term)
    return hits


def safe_pdf_text(text: str) -> str:
    """Make text safe for ReportLab's limited Helvetica fonts."""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "-",
        "\u00a0": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


def quote_excerpt(quote: str, limit: int = 130) -> str:
    quote = normalize_whitespace(quote)
    if len(quote) <= limit:
        return quote
    return quote[: limit - 1].rstrip() + "…"
