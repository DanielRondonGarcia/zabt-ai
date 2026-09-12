# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Pure, bounded temporal fusion for transcript and visual evidence.

The builder deliberately works with persisted-model-like objects and mappings so
it does not introduce a fused persistence model.  It returns immutable values
that can be passed to a later summary stage without carrying storage sessions or
provider-specific state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from math import ceil, isfinite
import re
from typing import Any, Literal


Relevance = Literal["high", "medium", "low"]
Provenance = Literal["observed", "inferred"]
Completeness = Literal["complete", "incomplete"]


@dataclass(frozen=True)
class ContextItem:
    source: Literal["spoken", "visual"]
    source_id: int
    start: float
    end: float
    content: str
    relevance: Relevance
    provenance: Provenance
    uncertainty: str | None = None
    fragment_index: int = 0
    speaker: str | None = None
    confidence: float | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class ContextChunk:
    index: int
    start: float
    end: float
    items: tuple[ContextItem, ...]
    estimated_input_tokens: int
    complete: bool


@dataclass(frozen=True)
class ContextBuildResult:
    chunks: tuple[ContextChunk, ...]
    source_item_count: int
    assigned_item_count: int
    unassigned_items: tuple[ContextItem, ...]
    completeness: Completeness
    warning_codes: tuple[str, ...]


@dataclass(frozen=True)
class _SourceRecord:
    item: ContextItem
    bucket: int
    words: tuple[str, ...] = ()
    meaningful_change: bool = False


@dataclass
class _MutableChunk:
    index: int
    bucket: int
    start: float
    end: float
    items: list[ContextItem]
    estimated_input_tokens: int = 0


_HIGH_RELEVANCE = re.compile(
    r"\b(screen|slide|dashboard|document|log|code|error|terminal|spreadsheet|chart|table|page)\b",
    re.IGNORECASE,
)
_LOW_RELEVANCE = re.compile(
    r"\b(camera|webcam|face|speaker|talking head|room|portrait|background)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\S+")


def intervals_overlap(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> bool:
    """Return true for a strict overlap under the `[start, end)` convention."""
    return first_start < second_end and second_start < first_end


def estimate_input_tokens(text: str) -> int:
    """Use a deterministic conservative character estimate for request budgets."""
    return max(1, ceil(len(text.strip()) / 4))


def _read(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _source_id(value: Any, fallback: int) -> int:
    raw = _read(value, "id", "source_id", default=fallback)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("context source IDs must be integers") from exc


def _interval(value: Any) -> tuple[float, float]:
    start = float(_read(value, "start_time", "start", default=0.0))
    end = float(_read(value, "end_time", "end", default=start))
    if not isfinite(start) or not isfinite(end) or end <= start:
        raise ValueError("context evidence intervals must have finite end > start")
    return start, end


def _relevance(value: Any, content: str, source: Literal["spoken", "visual"]) -> Relevance:
    raw = _read(value, "relevance", default=None)
    if raw in {"high", "medium", "low"}:
        return raw
    if source == "spoken":
        return "high"
    if _HIGH_RELEVANCE.search(content):
        return "high"
    if _LOW_RELEVANCE.search(content):
        return "low"
    return "medium"


def _normalise(
    values: Iterable[Any],
    source: Literal["spoken", "visual"],
    chunk_seconds: float,
) -> list[_SourceRecord]:
    records: list[_SourceRecord] = []
    for index, value in enumerate(values, start=1):
        source_id = _source_id(value, index)
        start, end = _interval(value)
        raw_content = _read(value, "text", "content", "caption", default="")
        content = str(raw_content or "").strip()
        if not content:
            content = "(no evidence text)"
        provenance = _read(value, "provenance", default="observed")
        if provenance not in {"observed", "inferred"}:
            raise ValueError("context provenance must be observed or inferred")
        uncertainty = _read(value, "uncertainty", default=None)
        confidence = _read(value, "confidence", default=None)
        confidence = float(confidence) if confidence is not None else None
        if uncertainty is None and confidence is not None and confidence < 0.7:
            uncertainty = "low confidence visual evidence"
        item = ContextItem(
            source=source,
            source_id=source_id,
            start=start,
            end=end,
            content=content,
            relevance=_relevance(value, content, source),
            provenance=provenance,
            uncertainty=str(uncertainty) if uncertainty is not None else None,
            fragment_index=0,
            speaker=_read(value, "speaker", default=None) if source == "spoken" else None,
            confidence=confidence,
            evidence_ref=_read(value, "evidence_ref", default=None)
            or f"{source}:{source_id}",
        )
        raw_words = _read(value, "words", default=None)
        words = tuple(
            str(_read(word, "word", "text", default=word)).strip()
            for word in raw_words or ()
        )
        words = tuple(word for word in words if word)
        records.append(
            _SourceRecord(
                item=item,
                bucket=max(0, int(start // chunk_seconds)),
                words=words,
                meaningful_change=bool(
                    _read(value, "meaningful_change", "is_meaningful_change", default=False)
                ),
            )
        )
    return records


def _similar_visual(first: ContextItem, second: ContextItem) -> bool:
    first_text = " ".join(first.content.lower().split())
    second_text = " ".join(second.content.lower().split())
    return SequenceMatcher(None, first_text, second_text).ratio() >= 0.92


def _deduplicate_visuals(
    records: list[_SourceRecord],
    chunk_seconds: float,
) -> list[_SourceRecord]:
    kept: list[_SourceRecord] = []
    previous: _SourceRecord | None = None
    for record in records:
        duplicate = False
        if (
            previous is not None
            and record.bucket - previous.bucket <= 1
            and not record.meaningful_change
            and _similar_visual(previous.item, record.item)
        ):
            duplicate = True
        if not duplicate:
            kept.append(record)
        previous = record
    return kept


def correlate_evidence(
    spoken_segments: Iterable[Any],
    visual_segments: Iterable[Any],
    *,
    chunk_seconds: float = 120,
) -> tuple[tuple[ContextItem, tuple[ContextItem, ...]], ...]:
    """Pair each spoken item with only genuinely overlapping visual evidence."""
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than zero")
    spoken = _normalise(spoken_segments, "spoken", chunk_seconds)
    visual = _deduplicate_visuals(
        _normalise(visual_segments, "visual", chunk_seconds), chunk_seconds
    )
    return tuple(
        (
            spoken_record.item,
            tuple(
                visual_record.item
                for visual_record in visual
                if intervals_overlap(
                    spoken_record.item.start,
                    spoken_record.item.end,
                    visual_record.item.start,
                    visual_record.item.end,
                )
            ),
        )
        for spoken_record in spoken
    )


def _split_text(text: str, max_tokens: int) -> list[str]:
    max_chars = max(1, max_tokens * 4)
    fragments: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            fragments.append(" ".join(current).strip())
            current.clear()

    for word in _WORD_RE.findall(text):
        if len(word) > max_chars:
            flush()
            fragments.extend(word[offset : offset + max_chars] for offset in range(0, len(word), max_chars))
            continue
        candidate = " ".join((*current, word))
        if current and estimate_input_tokens(candidate) > max_tokens:
            flush()
            current.append(word)
        else:
            current.append(word)
    flush()
    return [fragment for fragment in fragments if fragment]


class ContextBuilder:
    """Build chronological, budgeted context without silently dropping evidence."""

    def __init__(self, chunk_seconds: float = 120, max_input_tokens: int = 6000):
        if chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be greater than zero")
        if max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be greater than zero")
        self.chunk_seconds = float(chunk_seconds)
        self.max_input_tokens = int(max_input_tokens)

    def build(
        self,
        spoken_segments: Iterable[Any],
        visual_segments: Iterable[Any],
    ) -> ContextBuildResult:
        spoken = _normalise(spoken_segments, "spoken", self.chunk_seconds)
        raw_visual = _normalise(visual_segments, "visual", self.chunk_seconds)
        visual = _deduplicate_visuals(raw_visual, self.chunk_seconds)
        records = sorted(
            [*spoken, *visual],
            key=lambda record: (
                record.item.start,
                record.item.end,
                0 if record.item.source == "spoken" else 1,
                record.item.source_id,
            ),
        )

        chunks: list[_MutableChunk] = []
        unassigned: list[ContextItem] = []
        for record in records:
            item_tokens = estimate_input_tokens(record.item.content)
            if item_tokens > self.max_input_tokens:
                if record.item.source != "spoken":
                    unassigned.append(record.item)
                    continue
                fragments = _split_text(record.item.content, self.max_input_tokens)
                for fragment_index, fragment in enumerate(fragments):
                    self._append(
                        chunks,
                        replace(record.item, content=fragment, fragment_index=fragment_index),
                        record.bucket,
                    )
                continue
            self._append(chunks, record.item, record.bucket)

        warning_codes: list[str] = []
        if unassigned:
            warning_codes.extend(("context_budget_overflow", "unassigned_context_items"))
        frozen_chunks = tuple(
            ContextChunk(
                index=chunk.index,
                start=chunk.start,
                end=chunk.end,
                items=tuple(chunk.items),
                estimated_input_tokens=chunk.estimated_input_tokens,
                complete=not unassigned,
            )
            for chunk in chunks
        )
        assigned_sources = {
            (item.source, item.source_id)
            for chunk in frozen_chunks
            for item in chunk.items
        }
        return ContextBuildResult(
            chunks=frozen_chunks,
            source_item_count=len(spoken) + len(raw_visual),
            assigned_item_count=len(assigned_sources),
            unassigned_items=tuple(unassigned),
            completeness="complete" if not unassigned else "incomplete",
            warning_codes=tuple(warning_codes),
        )

    def _append(
        self,
        chunks: list[_MutableChunk],
        item: ContextItem,
        bucket: int,
    ) -> None:
        item_tokens = estimate_input_tokens(item.content)
        if chunks and chunks[-1].bucket == bucket:
            current = chunks[-1]
            if current.estimated_input_tokens + item_tokens <= self.max_input_tokens:
                current.items.append(item)
                current.estimated_input_tokens += item_tokens
                return

        if chunks and chunks[-1].bucket > bucket:
            bucket = chunks[-1].bucket
        index = len(chunks)
        start = bucket * self.chunk_seconds
        chunks.append(
            _MutableChunk(
                index=index,
                bucket=bucket,
                start=start,
                end=start + self.chunk_seconds,
                items=[item],
                estimated_input_tokens=item_tokens,
            )
        )


def build_context(
    spoken_segments: Iterable[Any],
    visual_segments: Iterable[Any],
    *,
    chunk_seconds: float = 120,
    max_input_tokens: int = 6000,
) -> ContextBuildResult:
    """Convenience wrapper around :class:`ContextBuilder`."""
    return ContextBuilder(
        chunk_seconds=chunk_seconds,
        max_input_tokens=max_input_tokens,
    ).build(spoken_segments, visual_segments)


def build_multimodal_context(
    spoken_segments: Iterable[Any],
    visual_segments: Iterable[Any],
    *,
    chunk_seconds: float = 120,
    max_input_tokens: int = 6000,
) -> ContextBuildResult:
    """Named alias for callers that prefer the feature-level terminology."""
    return build_context(
        spoken_segments,
        visual_segments,
        chunk_seconds=chunk_seconds,
        max_input_tokens=max_input_tokens,
    )


__all__ = [
    "ContextBuilder",
    "ContextBuildResult",
    "ContextChunk",
    "ContextItem",
    "build_context",
    "build_multimodal_context",
    "correlate_evidence",
    "estimate_input_tokens",
    "intervals_overlap",
]
