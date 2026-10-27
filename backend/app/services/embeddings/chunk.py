# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic meeting-content chunking for embedding points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from app.services.embeddings.canonicalize import canonicalize_text, flatten_structured_output

# These are whitespace-word windows, not tokenizer-exact token limits. The
# conservative size leaves headroom for gateways whose physical limit is 512
# provider tokens, while the overlap preserves context across adjacent chunks.
CONTENT_CHUNK_WORDS = 128
CONTENT_CHUNK_OVERLAP_WORDS = 32

# Hard UTF-8 byte cap for each emitted embedding input.
EMBEDDING_INPUT_MAX_BYTES = 480

# Deliberate bounded-context budget for the group introduction.
GROUP_CONTEXT_MAX_BYTES = 96
GROUP_CONTEXT_HEADER = "Group introduction: "


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _bounded_group_context(context: str) -> str:
    """Return the group introduction kept in each window, byte-bounded.

    Introductions that fit are kept verbatim. Longer ones are truncated to
    word boundaries within the byte budget and marked with an ellipsis, so
    the bounded context is visible in the embedding input instead of being
    silently clipped.
    """
    if _utf8_len(context) <= GROUP_CONTEXT_MAX_BYTES:
        return context
    truncated = ""
    for word in context.split():
        candidate = f"{truncated} {word}" if truncated else word
        if _utf8_len(f"{candidate}…") > GROUP_CONTEXT_MAX_BYTES:
            break
        truncated = candidate
    if not truncated:
        # One word larger than the whole budget still keeps a UTF-8-safe
        # character prefix, so the introduction is never reduced to a bare
        # ellipsis with all of its context lost.
        truncated = _char_word_pieces(
            context, GROUP_CONTEXT_MAX_BYTES - _utf8_len("…")
        )[0]
    return f"{truncated}…"


def _char_word_pieces(word: str, budget: int) -> list[str]:
    """Slice one token larger than ``budget`` into character pieces."""
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and _utf8_len(current) + _utf8_len(char) > budget:
            pieces.append(current)
            current = ""
        current += char
    if current:
        pieces.append(current)
    return pieces


def _window_end(words: list[str], start: int, budget: int) -> int:
    """Return one past the end of the longest word run fitting ``budget``."""
    total = 0
    index = start
    while index < len(words):
        addition = _utf8_len(words[index]) + (1 if index > start else 0)
        if total + addition > budget:
            break
        total += addition
        index += 1
    return index


def _byte_windows(
    text: str,
    *,
    budget: int,
    overlap: int = CONTENT_CHUNK_OVERLAP_WORDS,
) -> list[str]:
    """Keep ``text`` as one window when it fits, or subdivide it at word
    boundaries so every emitted window stays within ``budget`` UTF-8 bytes,
    preserving the same word overlap across adjacent windows. A single token
    larger than the whole budget is sliced at character boundaries so that no
    source text is ever dropped."""
    words = text.split()
    if not words:
        return []
    if _utf8_len(text) <= budget:
        return [" ".join(words)]
    windows: list[str] = []
    start = 0
    while start < len(words):
        end = _window_end(words, start, budget)
        if end == start:
            windows.extend(_char_word_pieces(words[start], budget))
            start += 1
            continue
        windows.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = max(end - overlap, start + 1)
    return windows


@dataclass(frozen=True)
class MeetingContentChunk:
    id: str
    meeting_id: int
    kind: str
    text: str
    chunk_index: int
    chunk_count: int
    embedding_text: str | None = None


def point_id_for(meeting_id: int, kind: str, chunk_index: int) -> str:
    """Return the stable UUID5 point id for a meeting content chunk."""
    return str(uuid5(NAMESPACE_OID, f"{meeting_id}:{kind}:{chunk_index}"))


def _window_text(
    text: str,
    *,
    size: int = CONTENT_CHUNK_WORDS,
    overlap: int = CONTENT_CHUNK_OVERLAP_WORDS,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    step = size - overlap
    if step <= 0:
        raise ValueError("chunk overlap must be smaller than chunk size")
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += step
    return chunks


def _chunked_text(
    meeting_id: int,
    kind: str,
    text: str,
    *,
    embedding_context: str | None = None,
) -> list[MeetingContentChunk]:
    canonical = canonicalize_text(text)
    context = canonicalize_text(embedding_context or "")
    prefix = ""
    if context:
        prefix = f"{GROUP_CONTEXT_HEADER}{_bounded_group_context(context)}\n\n"
    # Word windows bound token counts; byte windows additionally bound the
    # exact UTF-8 payload (including the group introduction prefix) sent to
    # providers whose physical input limit is smaller than a full window.
    budget = EMBEDDING_INPUT_MAX_BYTES - _utf8_len(prefix)
    windows = [
        bounded_window
        for window in _window_text(canonical)
        for bounded_window in _byte_windows(window, budget=budget)
        if bounded_window
    ]
    count = len(windows)
    return [
        MeetingContentChunk(
            id=point_id_for(meeting_id, kind, index),
            meeting_id=meeting_id,
            kind=kind,
            text=window,
            chunk_index=index,
            chunk_count=count,
            embedding_text=f"{prefix}{window}" if prefix else None,
        )
        for index, window in enumerate(windows)
        if window
    ]


def build_meeting_chunks(
    *,
    meeting_id: int,
    transcript_text: str | None = None,
    transliterated_text: str | None = None,
    summary_text: str | None = None,
    original_summary_text: str | None = None,
    structured_output: Any | None = None,
    structured_output_status: str | None = None,
    visual_segments: list[Any] | None = None,
    group_introduction: str | None = None,
) -> list[MeetingContentChunk]:
    """Build citation-safe text chunks with optional group context for embedding only."""
    chunks: list[MeetingContentChunk] = []
    summary = summary_text or original_summary_text
    chunks.extend(
        _chunked_text(
            meeting_id,
            "summary",
            summary or "",
            embedding_context=group_introduction,
        )
    )
    chunks.extend(
        _chunked_text(
            meeting_id,
            "transcript",
            transcript_text or "",
            embedding_context=group_introduction,
        )
    )
    chunks.extend(
        _chunked_text(
            meeting_id,
            "transliterated",
            transliterated_text or "",
            embedding_context=group_introduction,
        )
    )
    if structured_output_status == "completed" and structured_output is not None:
        chunks.extend(
            _chunked_text(
                meeting_id,
                "structured",
                flatten_structured_output(structured_output),
                embedding_context=group_introduction,
            )
        )

    visual_lines = []
    for segment in sorted(
        visual_segments or [],
        key=lambda item: (
            float(getattr(item, "start_time")),
            float(getattr(item, "end_time")),
            int(getattr(item, "sequence", 0)),
        ),
    ):
        caption = canonicalize_text(getattr(segment, "caption", None))
        if not caption:
            continue
        start_time = float(getattr(segment, "start_time"))
        end_time = float(getattr(segment, "end_time"))
        visual_lines.append(
            f"Visual description {start_time:.2f}s-{end_time:.2f}s: {caption}"
        )
    chunks.extend(
        _chunked_text(
            meeting_id,
            "visual",
            " ".join(visual_lines),
            embedding_context=group_introduction,
        )
    )
    return chunks
