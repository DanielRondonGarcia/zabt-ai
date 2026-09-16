# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider registry implementation kept behind the legacy factory module."""

from __future__ import annotations

from typing import Callable, Any

from app.core.config import settings as default_settings
from app.models import TranscriptionBackend
from app.services.transcription.contracts import (
    BatchTranscriptionRequest,
    ProviderCapabilities,
    ProviderName,
)
from app.services.transcription.errors import ProviderSelectionError, TranscriptionConfigurationError
from app.services.transcription.openai_provider import (
    OPENAI_FILE_CAPABILITIES as _OPENAI_FILE_CAPABILITIES,
    OpenAIFileProvider,
)
from app.services.transcription.provider_contract import (
    HeartbeatCallback,
    StatusCallback,
    TranscriptionProvider,
    validate_batch_request,
)
from app.services.transcription.source import AudioSourceResolver
from app.services.transcription.types import TranscriptionConfig, TranscriptionResult

GPU_CAPABILITIES = ProviderCapabilities(
    medical=True, segments=True, words=True, speakers=True, duration=True,
    cloud_diarization=False,
)
OPENAI_FILE_CAPABILITIES = _OPENAI_FILE_CAPABILITIES


class ScopedGpuProvider:
    def __init__(self, backend: TranscriptionBackend) -> None:
        from app.services.transcription.gpu_client import GpuTranscriptionClient
        self.provider_name = backend.value
        self.model = None
        self.capabilities = GPU_CAPABILITIES
        self._client = GpuTranscriptionClient(backend=backend)
        self._closed = False

    def process_audio(self, audio_path: str, config: TranscriptionConfig | None = None,
                      on_status_change: StatusCallback | None = None,
                      on_heartbeat: HeartbeatCallback | None = None) -> TranscriptionResult:
        return self._client.process_audio(audio_path, config, on_status_change, on_heartbeat)

    def transcribe(self, request: BatchTranscriptionRequest, *, on_status_change: StatusCallback | None = None,
                   on_heartbeat: HeartbeatCallback | None = None) -> TranscriptionResult:
        validate_batch_request(request, self.capabilities, provider=self.provider_name, model=self.model)
        source = AudioSourceResolver.resolve_for_provider(request.source, self.provider_name,
                                                           storage_key=request.source.storage_key)
        config = TranscriptionConfig(
            language=request.language,
            allowed_languages=set(request.allowed_languages) if request.allowed_languages else None,
            storage_key=source.storage_key,
            transcription_type=request.transcription_type,
            timestamp_mode=request.timestamp_mode.value,
            response_format=request.response_format,
            speaker_required=request.speaker_required,
            model=request.model,
        )
        return self.process_audio(str(source.local_path or ""), config, on_status_change, on_heartbeat)

    async def transcribe_chunk(self, data: bytes) -> str:
        return await self._client.transcribe_chunk(data)

    def get_provider_name(self) -> str:
        return self._client.get_provider_name()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "ScopedGpuProvider":
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        self.close()


def _build_gpu_local(config: Any = default_settings) -> TranscriptionProvider:
    return ScopedGpuProvider(TranscriptionBackend.GPU_LOCAL)


def _build_runpod(config: Any = default_settings) -> TranscriptionProvider:
    _validate_runpod_config(config)
    return ScopedGpuProvider(TranscriptionBackend.RUNPOD)


def _build_openai_file(config: Any = default_settings) -> TranscriptionProvider:
    return OpenAIFileProvider(config)


ProviderBuilder = Callable[[Any], TranscriptionProvider]
PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    ProviderName.GPU_LOCAL.value: _build_gpu_local,
    ProviderName.RUNPOD.value: _build_runpod,
    ProviderName.OPENAI_FILE.value: _build_openai_file,
}


def _validate_runpod_config(config: Any) -> None:
    missing = [name for name, value in (("RUNPOD_API_KEY", config.RUNPOD_API_KEY),
                                        ("RUNPOD_ENDPOINT_ID", config.RUNPOD_ENDPOINT_ID)) if not value]
    if missing:
        raise TranscriptionConfigurationError(f"Provider 'runpod' requires: {', '.join(missing)}", setting=missing[0])


def _provider_value(config: Any, requested: str | ProviderName | None) -> str:
    canonical = getattr(config, "TRANSCRIPTION_PROVIDER", None)
    legacy = getattr(config, "TRANSCRIPTION_BACKEND", None)
    canonical_value = canonical.value if isinstance(canonical, (ProviderName, TranscriptionBackend)) else str(canonical or "").strip()
    legacy_value = legacy.value if isinstance(legacy, (ProviderName, TranscriptionBackend)) else str(legacy or "").strip()
    if canonical_value and legacy_value and canonical_value != legacy_value:
        raise TranscriptionConfigurationError(
            "TRANSCRIPTION_PROVIDER and compatibility TRANSCRIPTION_BACKEND disagree; configure one provider explicitly",
            setting="TRANSCRIPTION_PROVIDER",
        )
    raw = requested or canonical_value or legacy_value or "gpu-local"
    raw = raw.value if isinstance(raw, (ProviderName, TranscriptionBackend)) else str(raw).strip()
    if raw not in PROVIDER_BUILDERS:
        raise ProviderSelectionError(f"Unknown transcription provider {raw!r}", setting="TRANSCRIPTION_PROVIDER")
    return raw


def get_provider(config: Any = default_settings, provider: str | ProviderName | None = None) -> TranscriptionProvider:
    selected = _provider_value(config, provider)
    return PROVIDER_BUILDERS[selected](config)
