# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Typed errors for the provider-neutral transcription boundary."""

from __future__ import annotations


class TranscriptionError(Exception):
    """Base class for errors that must not trigger provider fallback."""


class TranscriptionPreparationError(TranscriptionError):
    """The selected provider could not prepare a local transcription input."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class FFmpegUnavailableError(TranscriptionPreparationError):
    """The local FFmpeg toolchain required for long-file preparation is absent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="ffmpeg_unavailable")


class ChunkTooLargeError(TranscriptionPreparationError):
    """A generated provider chunk is larger than the configured safe limit."""

    def __init__(self, size_bytes: int, limit_bytes: int, *, chunk_name: str) -> None:
        super().__init__(
            f"Generated transcription chunk {chunk_name!r} is {size_bytes} bytes; "
            f"the configured safe limit is {limit_bytes} bytes. "
            "Lower TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS or increase the "
            "safe chunk limit only when it remains below OpenAI's 25 MB limit.",
            code="chunk_too_large",
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.chunk_name = chunk_name


class ProviderRequestError(TranscriptionError):
    """A provider request failed after the provider-specific retry policy."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None,
        attempts: int,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.attempts = attempts


class TranscriptionConfigurationError(TranscriptionError, ValueError):
    """The selected provider or one of its settings is invalid."""

    def __init__(self, message: str, *, setting: str | None = None) -> None:
        super().__init__(message)
        self.setting = setting


class ProviderSelectionError(TranscriptionConfigurationError):
    """No registered provider matches the requested provider identifier."""


class ProviderNotImplementedError(TranscriptionError, NotImplementedError):
    """A registered provider seam has no implementation in the current slice."""


class UnsupportedCapabilityError(TranscriptionError):
    """The provider cannot satisfy a requested batch or realtime capability."""

    def __init__(
        self,
        capability: str,
        provider: str,
        model: str | None = None,
        message: str | None = None,
    ) -> None:
        self.capability = capability
        self.provider = provider
        self.model = model
        detail = message or f"Provider {provider!r} does not support capability {capability!r}"
        if model:
            detail += f" for model {model!r}"
        super().__init__(detail)


class AudioSourceError(TranscriptionError, ValueError):
    """An audio source is empty, ambiguous, or unsuitable for resolution."""


class InvalidAudioSourceError(AudioSourceError):
    """The source cannot be represented as one local path or storage key."""


# Short aliases keep integrations readable without losing the typed classes.
ConfigurationError = TranscriptionConfigurationError
SourceResolutionError = AudioSourceError
