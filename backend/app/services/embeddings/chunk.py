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


@dataclass(frozen=True)
class MeetingContentChunk:
    id: str
    meeting_id: int
    kind: str
    text: str
    chunk_index: int
    chunk_count: int


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


def _chunked_text(meeting_id: int, kind: str, text: str) -> list[MeetingContentChunk]:
    canonical = canonicalize_text(text)
    windows = _window_text(canonical)
    count = len(windows)
    return [
        MeetingContentChunk(
            id=point_id_for(meeting_id, kind, index),
            meeting_id=meeting_id,
            kind=kind,
            text=window,
            chunk_index=index,
            chunk_count=count,
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
) -> list[MeetingContentChunk]:
    """Build deterministic embedding chunks from the supported Meeting text fields."""
    chunks: list[MeetingContentChunk] = []
    summary = summary_text or original_summary_text
    chunks.extend(_chunked_text(meeting_id, "summary", summary or ""))
    chunks.extend(_chunked_text(meeting_id, "transcript", transcript_text or ""))
    chunks.extend(_chunked_text(meeting_id, "transliterated", transliterated_text or ""))
    if structured_output_status == "completed" and structured_output is not None:
        chunks.extend(
            _chunked_text(
                meeting_id,
                "structured",
                flatten_structured_output(structured_output),
            )
        )
    return chunks
