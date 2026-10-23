"""Clause segmentation with exact source offsets.

A segment is always a contiguous substring of one extracted document span. Short
sentences from the same paragraph/block are grouped into a clause-sized window
(up to ``max_segment_chars``) so cross-sentence terms such as governing law and
exclusive jurisdiction stay together. Overlong text is only split at safe
boundaries, and offsets always point into the original source text.
"""

from __future__ import annotations

from models.schemas import ClauseSegment, Document
from core.text_utils import split_long_segment, split_sentences_with_offsets


class ClauseSplitter:
    def __init__(self, target_segment_chars: int = 800, max_segment_chars: int = 1200) -> None:
        if target_segment_chars <= 0 or max_segment_chars < target_segment_chars:
            raise ValueError("Invalid segment size configuration")
        self.target_segment_chars = target_segment_chars
        self.max_segment_chars = max_segment_chars

    def split(self, document: Document) -> list[ClauseSegment]:
        segments: list[ClauseSegment] = []
        counter = 0
        for span in document.spans:
            windows = self._window_span(span.text)
            for text, start, end in windows:
                if not text.strip():
                    continue
                counter += 1
                segments.append(
                    ClauseSegment(
                        clause_id=f"C{counter:04d}",
                        text=text.strip(),
                        source_id=span.source_id,
                        page_number=span.page_number,
                        paragraph_index=span.paragraph_index,
                        section_id=span.section_id,
                        span_start=start,
                        span_end=end,
                        source_type=span.source_type,
                    )
                )
        return segments

    def _window_span(self, text: str) -> list[tuple[str, int, int]]:
        sentences = split_sentences_with_offsets(text)
        if not sentences:
            return []
        windows: list[tuple[str, int, int]] = []
        buffer_start = sentences[0][1]
        buffer_end = sentences[0][2]
        for sentence_text, start, end in sentences[1:]:
            proposed_length = end - buffer_start
            if proposed_length <= self.target_segment_chars:
                buffer_end = end
                continue
            # Flush the current window, then start a new one.
            window_text = text[buffer_start:buffer_end]
            if len(window_text) > self.max_segment_chars:
                for piece, piece_start, piece_end in split_long_segment(
                    window_text, 0, len(window_text), self.max_segment_chars
                ):
                    windows.append(
                        (piece, buffer_start + piece_start, buffer_start + piece_end)
                    )
            else:
                windows.append((window_text, buffer_start, buffer_end))
            buffer_start, buffer_end = start, end
        window_text = text[buffer_start:buffer_end]
        if len(window_text) > self.max_segment_chars:
            for piece, piece_start, piece_end in split_long_segment(
                window_text, 0, len(window_text), self.max_segment_chars
            ):
                windows.append((piece, buffer_start + piece_start, buffer_start + piece_end))
        else:
            windows.append((window_text, buffer_start, buffer_end))
        return windows
