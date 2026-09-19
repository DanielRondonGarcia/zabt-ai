# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic meeting-content chunking for embedding points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from app.services.embeddings.canonicalize import canonicalize_text, flatten_structured_output

TRANSCRIPT_CHUNK_TOKENS = 512
TRANSCRIPT_CHUNK_OVERLAP = 200


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


def _window_text(text: str, *, size: int = TRANSCRIPT_CHUNK_TOKENS, overlap: int = TRANSCRIPT_CHUNK_OVERLAP) -> list[str]:
    tokens = text.split()
    if not tokens:
        return []
    if len(tokens) <= size:
        return [" ".join(tokens)]
    step = size - overlap
    if step <= 0:
        raise ValueError("chunk overlap must be smaller than chunk size")
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start += step
    return chunks


def _single_chunk(meeting_id: int, kind: str, text: str) -> list[MeetingContentChunk]:
    canonical = canonicalize_text(text)
    if not canonical:
        return []
    return [
        MeetingContentChunk(
            id=point_id_for(meeting_id, kind, 0),
            meeting_id=meeting_id,
            kind=kind,
            text=canonical,
            chunk_index=0,
            chunk_count=1,
        )
    ]


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
    chunks.extend(_single_chunk(meeting_id, "summary", summary or ""))
    chunks.extend(_chunked_text(meeting_id, "transcript", transcript_text or ""))
    chunks.extend(_chunked_text(meeting_id, "transliterated", transliterated_text or ""))
    if structured_output_status == "completed" and structured_output is not None:
        chunks.extend(_single_chunk(meeting_id, "structured", flatten_structured_output(structured_output)))
    return chunks
