# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider-neutral batch/realtime contracts introduced by PR1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from app.models import TranscriptionType
from app.services.transcription.errors import InvalidAudioSourceError
from app.services.transcription.types import (
    ResultSegment,
    TranscriptionConfig,
    TranscriptionResult,
    WordTimestamp,
)


class ProviderName(str, Enum):
    GPU_LOCAL = "gpu-local"
    RUNPOD = "runpod"
    OPENAI_FILE = "openai-file"


TranscriptionProviderName = ProviderName


class TimestampMode(str, Enum):
    NONE = "none"
    SEGMENT = "segment"
    WORD = "word"


TimestampGranularity = TimestampMode


class AudioSourceKind(str, Enum):
    LOCAL_PATH = "local_path"
    STORAGE_KEY = "storage_key"


@dataclass(frozen=True)
class AudioSource:
    local_path: Path | None = None
    storage_key: str | None = None

    def __post_init__(self) -> None:
        path_set = self.local_path is not None
        key_set = bool(self.storage_key and self.storage_key.strip())
        if path_set == key_set:
            raise InvalidAudioSourceError("AudioSource requires exactly one local_path or storage_key")
        if path_set:
            raw_path = str(self.local_path).strip()
            if not raw_path:
                raise InvalidAudioSourceError("A local audio path is required")
            object.__setattr__(self, "local_path", Path(raw_path).expanduser())
        else:
            object.__setattr__(self, "storage_key", self.storage_key.strip())

    @classmethod
    def from_local_path(cls, value: str | Path) -> "AudioSource":
        if not str(value).strip():
            raise InvalidAudioSourceError("A local audio path is required")
        return cls(local_path=Path(value))

    @classmethod
    def from_storage_key(cls, value: str) -> "AudioSource":
        return cls(storage_key=value)

    @property
    def kind(self) -> AudioSourceKind:
        return AudioSourceKind.LOCAL_PATH if self.local_path is not None else AudioSourceKind.STORAGE_KEY

    @property
    def value(self) -> Path | str:
        return self.local_path if self.local_path is not None else self.storage_key  # type: ignore[return-value]


@dataclass(frozen=True)
class ProviderCapabilities:
    batch: bool = True
    realtime: bool = False
    medical: bool = False
    segments: bool = False
    words: bool = False
    speakers: bool = False
    duration: bool = False
    cloud_diarization: bool = False
    audio_formats: frozenset[str] = frozenset()
    response_formats: frozenset[str] = frozenset()


@dataclass(frozen=True)
class UsageMetadata:
    input_units: int | float | None = None
    output_units: int | float | None = None
    total_units: int | float | None = None
    unit: str | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    cost_unit: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


TranscriptionUsage = UsageMetadata
Usage = UsageMetadata


@dataclass(frozen=True)
class TranscriptionProgress:
    stage: str
    percent: float | None = None
    message: str | None = None


@dataclass(frozen=True)
class BatchTranscriptionRequest:
    source: AudioSource
    language: str | None = None
    allowed_languages: frozenset[str] | None = None
    transcription_type: TranscriptionType = TranscriptionType.GENERAL
    timestamp_mode: TimestampMode = TimestampMode.NONE
    speaker_required: bool = False
    response_format: str | None = None
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_mode, TimestampMode):
            object.__setattr__(self, "timestamp_mode", TimestampMode(self.timestamp_mode))
        if not isinstance(self.transcription_type, TranscriptionType):
            object.__setattr__(self, "transcription_type", TranscriptionType(self.transcription_type))

    @property
    def audio_source(self) -> AudioSource:
        return self.source


@dataclass(frozen=True)
class RealtimeTranscriptionRequest:
    audio_format: str
    language: str | None = None


__all__ = [
    "AudioSource", "AudioSourceKind", "BatchTranscriptionRequest", "ProviderCapabilities",
    "ProviderName", "RealtimeTranscriptionRequest", "TimestampMode", "TimestampGranularity",
    "TranscriptionProgress",
    "TranscriptionConfig", "TranscriptionResult", "UsageMetadata", "TranscriptionUsage",
    "ResultSegment", "WordTimestamp",
]
