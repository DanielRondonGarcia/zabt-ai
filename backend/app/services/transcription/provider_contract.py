# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Protocols and pre-I/O capability gates for transcription providers."""

from __future__ import annotations

from typing import Callable, Protocol, Self

from app.models import TranscriptionType
from app.services.transcription.contracts import (
    BatchTranscriptionRequest,
    ProviderCapabilities,
    RealtimeTranscriptionRequest,
    TranscriptionProgress,
)
from app.services.transcription.errors import UnsupportedCapabilityError
from app.services.transcription.types import TranscriptionConfig, TranscriptionResult

StatusCallback = Callable[[str | TranscriptionProgress], None]
HeartbeatCallback = Callable[[], None]


def validate_batch_request(
    request: BatchTranscriptionRequest,
    capabilities: ProviderCapabilities,
    *,
    provider: str,
    model: str | None = None,
) -> None:
    if not capabilities.batch:
        raise UnsupportedCapabilityError("batch", provider, model)
    if request.speaker_required and not capabilities.cloud_diarization:
        raise UnsupportedCapabilityError(
            "cloud_diarization", provider, model,
            "speaker_required=True requires a provider with cloud diarization",
        )
    if request.transcription_type == TranscriptionType.MEDICAL and not capabilities.medical:
        raise UnsupportedCapabilityError("medical", provider, model)
    if request.timestamp_mode.value == "segment" and not capabilities.segments:
        raise UnsupportedCapabilityError("segments", provider, model)
    if request.timestamp_mode.value == "word" and not capabilities.words:
        raise UnsupportedCapabilityError("words", provider, model)
    if request.response_format and capabilities.response_formats and request.response_format not in capabilities.response_formats:
        raise UnsupportedCapabilityError("response_format", provider, model)


class BatchTranscriptionProvider(Protocol):
    capabilities: ProviderCapabilities
    provider_name: str
    model: str | None

    def transcribe(self, request: BatchTranscriptionRequest, *, on_status_change: StatusCallback | None = None,
                   on_heartbeat: HeartbeatCallback | None = None) -> TranscriptionResult: ...
    def close(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None: ...


class RealtimeTranscriptionProvider(Protocol):
    capabilities: ProviderCapabilities

    async def transcribe_chunk(self, data: bytes, request: RealtimeTranscriptionRequest | None = None) -> str: ...


class TranscriptionProvider(Protocol):
    capabilities: ProviderCapabilities

    def process_audio(self, audio_path: str, config: TranscriptionConfig | None = None,
                      on_status_change: StatusCallback | None = None,
                      on_heartbeat: HeartbeatCallback | None = None) -> TranscriptionResult: ...
    def transcribe(self, request: BatchTranscriptionRequest, *, on_status_change: StatusCallback | None = None,
                   on_heartbeat: HeartbeatCallback | None = None) -> TranscriptionResult: ...
    async def transcribe_chunk(self, data: bytes) -> str: ...
    def get_provider_name(self) -> str: ...
    def close(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None: ...
