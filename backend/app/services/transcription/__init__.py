# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public API for the provider-neutral transcription boundary."""

from app.services.transcription.factory import build_config, get_provider
from app.services.transcription.provider import (
    BatchTranscriptionProvider,
    RealtimeTranscriptionProvider,
    TranscriptionProvider,
)
from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    ProviderCapabilities,
    ProviderName,
    TimestampMode,
    TranscriptionProgress,
    Usage,
    UsageMetadata,
)
from app.services.transcription.types import ResultSegment, TranscriptionConfig, TranscriptionResult, WordTimestamp

__all__ = [
    "get_provider",
    "build_config",
    "BatchTranscriptionProvider",
    "RealtimeTranscriptionProvider",
    "TranscriptionProvider",
    "TranscriptionResult",
    "TranscriptionConfig",
    "BatchTranscriptionRequest",
    "AudioSource",
    "ProviderCapabilities",
    "ProviderName",
    "TimestampMode",
    "TranscriptionProgress",
    "UsageMetadata",
    "Usage",
    "ResultSegment",
    "WordTimestamp",
]
