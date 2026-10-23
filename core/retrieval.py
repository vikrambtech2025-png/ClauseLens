"""Hybrid lexical clause retrieval.

The first working pipeline is intentionally lexical: weighted legal keyword
patterns plus BM25 ranking over sentence-level segments. A deterministic
keyword fallback remains available even if optional semantic retrieval is not
installed. Neighbouring segments are attached as context for interpretation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rank_bm25 import BM25Okapi

from core.text_utils import normalize_for_match, tokenize, truncate_with_notice
from models.schemas import (
    CategoryRetrieval,
    ClauseCategory,
    ClauseSegment,
    RetrievedPassage,
)


@dataclass(frozen=True)
class CategorySpec:
    core_terms: tuple[str, ...]
    query_terms: tuple[str, ...]
    strong_patterns: tuple[re.Pattern[str], ...]


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


CATEGORY_SPECS: dict[ClauseCategory, CategorySpec] = {
    ClauseCategory.PAYMENT: CategorySpec(
        core_terms=(
            "payment",
            "payable",
            "pay",
            "invoice",
            "invoiced",
            "remittance",
            "remit",
            "settlement",
            "fees",
            "compensation",
            "late fee",
            "overdue",
            "interest",
        ),
        query_terms=(
            "payment",
            "pay",
            "payable",
            "invoice",
            "net 30",
            "net 45",
            "net 60",
            "net 90",
            "remittance",
            "invoice settlement",
            "business days",
            "calendar days",
            "payable upon receipt",
            "due date",
            "late payment",
        ),
        strong_patterns=_patterns(
            r"\bnet\s*[-:]?\s*\d{1,3}\b",
            r"\b(?:within|after|from|no later than|not later than)\s+(?:a period of\s+)?\d{1,3}\s*(?:calendar|business|working)?\s*days?\b",
            r"\b\d{1,3}\s*(?:calendar|business|working)[- ]days?\b",
            r"\b(?:payable|payment|paid|pay)\b.{0,60}\b(?:upon receipt|on receipt|immediately|as soon as practicable)\b",
            r"\b(?:late|overdue)\b.{0,40}\b(?:fee|interest|charge)\b",
        ),
    ),
    ClauseCategory.RENEWAL_TERMINATION: CategorySpec(
        core_terms=(
            "renew",
            "renewal",
            "renewed",
            "renewing",
            "auto-renew",
            "automatic renewal",
            "automatically renew",
            "successive term",
            "successive terms",
            "evergreen",
            "non-renewal",
            "expiration",
            "termination",
            "terminate",
            "notice period",
            "notice",
            "term",
        ),
        query_terms=(
            "automatically renew",
            "auto renew",
            "automatic renewal",
            "successive terms",
            "successive renewal terms",
            "evergreen provision",
            "non-renewal notice",
            "notice before expiration",
            "termination for convenience",
            "renewal term",
        ),
        strong_patterns=_patterns(
            r"\b(?:automatically|auto)[- ]?renew(?:s|ed|al)?\b",
            r"\brenew(?:s|ed)?\s+automatically\b",
            r"\bsuccessive\s+(?:renewal\s+)?terms?\b",
            r"\bevergreen\b",
            r"\b(?:notice|notify)\b.{0,80}\b\d{1,3}\s*(?:calendar|business|working)?\s*days?\b",
            r"\b\d{1,3}\s*(?:calendar|business|working)?\s*days?\b.{0,40}\b(?:prior to|before|ahead of)\b",
            r"\bterminate\b.{0,60}\b(?:notice|days)\b",
        ),
    ),
    ClauseCategory.LIABILITY_INDEMNITY: CategorySpec(
        core_terms=(
            "liability",
            "liable",
            "indemnify",
            "indemnity",
            "indemnification",
            "hold harmless",
            "limitation of liability",
            "uncapped",
            "unlimited",
            "consequential",
            "indirect damages",
            "aggregate liability",
            "cap",
            "capped",
            "damages",
        ),
        query_terms=(
            "limitation of liability",
            "aggregate liability",
            "uncapped liability",
            "unlimited liability",
            "no limitation of liability",
            "indemnify",
            "indemnification",
            "hold harmless",
            "consequential damages",
            "liability cap",
            "liability shall not exceed",
            "gross negligence",
        ),
        strong_patterns=_patterns(
            r"\buncapped\b",
            r"\bunlimited\s+liability\b",
            r"\bno\s+(?:aggregate\s+)?limitation\s+of\s+liability\b",
            r"\bwithout\s+(?:any\s+)?limitation\s+of\s+liability\b",
            r"\bliability\b.{0,50}\b(?:shall|will)\s+not\s+be\s+(?:limited|capped)\b",
            r"\b(?:aggregate\s+)?liability\b.{0,60}\b(?:shall|will)\s+not\s+exceed\b",
            r"\b(?:liability|damages)\b.{0,50}\b(?:limited|capped)\s+to\b",
            r"\bindemnif(?:y|ication)\b",
            r"\bhold\s+harmless\b",
        ),
    ),
    ClauseCategory.JURISDICTION_DISPUTE: CategorySpec(
        core_terms=(
            "governing law",
            "governed",
            "jurisdiction",
            "venue",
            "arbitration",
            "arbitrator",
            "dispute",
            "disputes",
            "courts",
            "laws of",
            "exclusive jurisdiction",
            "forum",
            "litigation",
            "mediation",
        ),
        query_terms=(
            "governing law",
            "laws of",
            "governed by",
            "exclusive jurisdiction",
            "jurisdiction of the courts",
            "venue",
            "dispute resolution",
            "arbitration",
            "seat of arbitration",
            "conflict of laws",
            "legal proceedings",
        ),
        strong_patterns=_patterns(
            r"\bgovern(?:ed|ing)\b.{0,80}\blaws?\s+of\b",
            r"\bexclusive\s+jurisdiction\b",
            r"\b(?:venue|forum)\b.{0,60}\b(?:shall|will|is)\b",
            r"\b(?:courts?|tribunal)\s+of\b",
            r"\barbitrat(?:ion|or)\b",
            r"\bdispute(?:s)?\b.{0,80}\b(?:resolved|resolution|arbitration|courts?)\b",
            r"\bconflict\s+of\s+laws?\b",
        ),
    ),
}

# Extra query expansions improve BM25 recall for common legal synonyms.
QUERY_EXPANSIONS: dict[ClauseCategory, tuple[str, ...]] = {
    ClauseCategory.PAYMENT: ("receipt", "days", "terms", "settle", "money", "amount"),
    ClauseCategory.RENEWAL_TERMINATION: ("renewal", "automatically", "successive", "notice"),
    ClauseCategory.LIABILITY_INDEMNITY: ("liability", "unlimited", "indemnity", "damages"),
    ClauseCategory.JURISDICTION_DISPUTE: ("governing", "law", "jurisdiction", "courts", "dispute"),
}


class ClauseRetriever:
    def __init__(
        self,
        top_k: int = 8,
        min_score: float = 0.08,
        context_chars: int = 320,
    ) -> None:
        self.top_k = top_k
        self.min_score = min_score
        self.context_chars = context_chars

    def retrieve(
        self,
        segments: list[ClauseSegment],
        categories: Iterable[ClauseCategory] | None = None,
    ) -> dict[str, CategoryRetrieval]:
        categories = list(categories or ClauseCategory)
        if not segments:
            return {
                category.value: CategoryRetrieval(
                    category=category,
                    notes="No clause segments were available for retrieval.",
                )
                for category in categories
            }

        corpus = [tokenize(segment.text) for segment in segments]
        bm25 = BM25Okapi(corpus) if any(corpus) else None
        raw_bm25: dict[int, float] = {}
        if bm25 is not None:
            for category in categories:
                spec = CATEGORY_SPECS[category]
                query_tokens: list[str] = []
                for term in spec.query_terms + QUERY_EXPANSIONS[category]:
                    query_tokens.extend(tokenize(term))
                scores = bm25.get_scores(query_tokens)
                for index, score in enumerate(scores):
                    raw_bm25[(list(categories).index(category), index)] = float(score)

        results: dict[str, CategoryRetrieval] = {}
        for category_index, category in enumerate(categories):
            results[category.value] = self._rank_category(
                category=category,
                category_index=category_index,
                segments=segments,
                raw_bm25=raw_bm25,
            )
        return results

    # ------------------------------------------------------------------ ranking
    def _rank_category(
        self,
        category: ClauseCategory,
        category_index: int,
        segments: list[ClauseSegment],
        raw_bm25: dict[tuple[int, int], float],
    ) -> CategoryRetrieval:
        spec = CATEGORY_SPECS[category]
        keyword_scores: list[float] = []
        matched_by_index: list[list[str]] = []
        for segment in segments:
            score, matched = self._keyword_score(segment.text, spec)
            keyword_scores.append(score)
            matched_by_index.append(matched)

        bm25_scores = [max(0.0, raw_bm25.get((category_index, i), 0.0)) for i in range(len(segments))]
        max_keyword = max(keyword_scores, default=0.0)
        max_bm25 = max(bm25_scores, default=0.0)
        combined: list[float] = []
        for keyword_score, bm25_score in zip(keyword_scores, bm25_scores):
            keyword_norm = keyword_score / max_keyword if max_keyword else 0.0
            bm25_norm = bm25_score / max_bm25 if max_bm25 else 0.0
            value = 0.65 * keyword_norm + 0.35 * bm25_norm
            if keyword_score >= 3.0:
                value += 0.08
            combined.append(min(value, 1.0))

        ranked_indices = sorted(
            range(len(segments)),
            key=lambda i: (combined[i], keyword_scores[i], bm25_scores[i]),
            reverse=True,
        )

        passages: list[RetrievedPassage] = []
        seen_text: set[str] = set()
        for index in ranked_indices:
            # BM25 improves ranking, but a BM25-only hit is not enough to assert
            # that a clause belongs to a category. Requiring a legal keyword or
            # pattern hit keeps NOT_FOUND honest and prevents unrelated prose
            # from being surfaced as a candidate.
            if keyword_scores[index] <= 0:
                continue
            segment = segments[index]
            dedupe_key = normalize_for_match(segment.text)[:140]
            if dedupe_key in seen_text:
                continue
            seen_text.add(dedupe_key)
            before = segments[index - 1].text if index > 0 else ""
            after = segments[index + 1].text if index + 1 < len(segments) else ""
            before, _ = truncate_with_notice(before, self.context_chars)
            after, _ = truncate_with_notice(after, self.context_chars)
            passages.append(
                RetrievedPassage(
                    segment=segment,
                    score=round(combined[index], 4),
                    bm25_score=round(bm25_scores[index], 4),
                    keyword_score=round(keyword_scores[index], 4),
                    matched_terms=matched_by_index[index],
                    context_before=before,
                    context_after=after,
                )
            )
            if len(passages) >= self.top_k:
                break

        hits = sum(1 for score in keyword_scores if score > 0)
        has_signal = any(passage.keyword_score > 0 for passage in passages)
        notes = ""
        if not passages:
            notes = (
                "No lexical candidate matched. This is a retrieval result, not proof "
                "that the clause is absent."
            )
        return CategoryRetrieval(
            category=category,
            passages=passages,
            keyword_hits=hits,
            has_signal=has_signal,
            method="keyword_patterns + BM25 + neighbor_context",
            notes=notes,
        )

    @staticmethod
    def _keyword_score(text: str, spec: CategorySpec) -> tuple[float, list[str]]:
        normalized = normalize_for_match(text)
        score = 0.0
        matched: list[str] = []
        for term in spec.core_terms:
            term_norm = normalize_for_match(term)
            if not term_norm:
                continue
            # Word-boundary matching prevents "dispute" from matching
            # "undisputed", "term" from matching "terminal", and similar
            # false category signals.
            pattern = rf"(?<![a-z0-9]){re.escape(term_norm)}(?![a-z0-9])"
            if re.search(pattern, normalized):
                score += 1.0
                matched.append(term)
        for pattern in spec.strong_patterns:
            found = pattern.search(text)
            if found:
                score += 3.0
                matched.append(found.group(0)[:80])
        # Deduplicate matched terms while preserving order.
        matched = list(dict.fromkeys(matched))
        return score, matched
