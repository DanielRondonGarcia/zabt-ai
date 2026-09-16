# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Legacy-compatible result/config types and lazy provider contract exports."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from app.models import TranscriptionType


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float
    speaker_label: str | None = None
    confidence: float | None = None


@dataclass
class ResultSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[WordTimestamp] = field(default_factory=list)


def coarse_text_segment(
    text: str,
    *,
    start: float,
    duration: float | None,
) -> ResultSegment | None:
    """Build one non-word-timestamped segment for text-only results."""

    text = str(text or "").strip()
    if not text or duration is None:
        return None
    if not math.isfinite(start) or not math.isfinite(duration) or duration <= 0:
        return None

    end = start + duration
    if not math.isfinite(end) or end <= start:
        return None
    return ResultSegment(
        start=start,
        end=end,
        text=text,
        speaker="SPEAKER_UNKNOWN",
    )


@dataclass
class TranscriptionResult:
    text: str
    language: str
    segments: list[ResultSegment]
    provider_name: str
    recognition_method: str
    audio_duration_seconds: float | None
    estimated_cost: float | None
    model: str | None = None
    usage: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    capability_gaps: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return self.provider_name

    @property
    def duration_seconds(self) -> float | None:
        return self.audio_duration_seconds


@dataclass
class TranscriptionConfig:
    language: str | None = None
    allowed_languages: set[str] | None = None
    min_speakers: int = 1
    max_speakers: int = 10
    storage_key: str | None = None
    transcription_type: TranscriptionType = TranscriptionType.GENERAL
    timestamp_mode: str = "none"
    response_format: str | None = None
    speaker_required: bool = False
    model: str | None = None


_CONTRACT_EXPORTS = {
    "AudioSource", "AudioSourceKind", "BatchTranscriptionRequest", "ProviderCapabilities",
    "ProviderName", "RealtimeTranscriptionRequest", "TimestampMode", "TranscriptionProgress", "UsageMetadata", "Usage",
}

__all__ = [
    "WordTimestamp",
    "ResultSegment",
    "coarse_text_segment",
    "TranscriptionResult",
    "TranscriptionConfig",
    *_CONTRACT_EXPORTS,
]


def __getattr__(name: str) -> Any:
    if name in _CONTRACT_EXPORTS:
        from app.services.transcription import contracts
        return getattr(contracts, name)
    raise AttributeError(name)
