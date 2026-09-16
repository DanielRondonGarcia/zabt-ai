# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Compatibility entry point for the scoped transcription registry."""

from __future__ import annotations

from app.core.config import settings
from app.models import UserTier
from app.services.transcription.contracts import ProviderName, TimestampMode
from app.services.transcription.errors import TranscriptionConfigurationError
from app.services.transcription.registry import PROVIDER_BUILDERS
from app.services.transcription.registry import get_provider as _get_provider
from app.services.transcription.types import TranscriptionConfig

_gpu_client = None  # legacy test/integration symbol; registry instances are fresh


def get_provider(user_tier: UserTier | None = None, provider: str | ProviderName | None = None):
    return _get_provider(settings, provider)


def build_config(user_tier: UserTier | None = None, language: str | None = None,
                 allowed_languages: set[str] | None = None) -> TranscriptionConfig:
    raw_timestamp = getattr(settings, "TRANSCRIPTION_TIMESTAMP_MODE", "none")
    if not isinstance(raw_timestamp, str):
        raw_timestamp = "none"
    try:
        timestamp_mode = TimestampMode(raw_timestamp)
    except ValueError as exc:
        raise TranscriptionConfigurationError("Unsupported TRANSCRIPTION_TIMESTAMP_MODE", setting="TRANSCRIPTION_TIMESTAMP_MODE") from exc
    if allowed_languages is None:
        raw = getattr(settings, "TRANSCRIPTION_ALLOWED_LANGUAGES", "")
        allowed_languages = {item.strip() for item in raw.split(",") if item.strip()} if isinstance(raw, str) else None
    configured_language = getattr(settings, "TRANSCRIPTION_LANGUAGE", None)
    response_format = getattr(settings, "TRANSCRIPTION_RESPONSE_FORMAT", None)
    model = getattr(settings, "TRANSCRIPTION_MODEL", None)
    return TranscriptionConfig(
        min_speakers=settings.DIARIZATION_MIN_SPEAKERS,
        max_speakers=settings.DIARIZATION_MAX_SPEAKERS,
        language=language if language is not None else configured_language,
        allowed_languages=allowed_languages,
        timestamp_mode=timestamp_mode.value,
        response_format=response_format if isinstance(response_format, str) else None,
        speaker_required=bool(getattr(settings, "TRANSCRIPTION_SPEAKER_REQUIRED", False)),
        model=model if isinstance(model, str) else None,
    )


__all__ = ["get_provider", "build_config", "PROVIDER_BUILDERS"]
