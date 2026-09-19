# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for transcription provider registry dispatch."""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

from app.models import TranscriptionBackend, TranscriptionType
from app.services.transcription.contracts import AudioSource, BatchTranscriptionRequest, ProviderName, TimestampMode
from app.services.transcription.errors import ProviderSelectionError, TranscriptionConfigurationError, UnsupportedCapabilityError
from app.services.transcription.types import TranscriptionConfig, TranscriptionResult
from app.services.transcription import registry


class Config:
    TRANSCRIPTION_PROVIDER = ""
    TRANSCRIPTION_BACKEND = ""
    RUNPOD_API_KEY = "runpod-key"
    RUNPOD_ENDPOINT_ID = "endpoint-id"


class FakeOpenAIProvider:
    def __init__(self, config):
        self.config = config
        self.provider_name = "openai-file"
        self.capabilities = registry.OPENAI_FILE_CAPABILITIES


class FakeClient:
    instances = []

    def __init__(self, backend):
        self.backend = backend
        self.closed = False
        self.process_calls = []
        self.chunk_calls = []
        FakeClient.instances.append(self)

    def process_audio(self, audio_path, config=None, on_status_change=None, on_heartbeat=None):
        self.process_calls.append((audio_path, config, on_status_change, on_heartbeat))
        return TranscriptionResult(
            text="done",
            language="en",
            segments=[],
            provider_name=self.backend.value,
            recognition_method="fake-gpu",
            audio_duration_seconds=1.0,
            estimated_cost=None,
        )

    async def transcribe_chunk(self, data):
        self.chunk_calls.append(data)
        return "chunk text"

    def get_provider_name(self):
        return f"client:{self.backend.value}"

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_builders(monkeypatch):
    FakeClient.instances = []
    gpu_module = types.ModuleType("app.services.transcription.gpu_client")
    gpu_module.GpuTranscriptionClient = FakeClient
    monkeypatch.setitem(sys.modules, "app.services.transcription.gpu_client", gpu_module)
    monkeypatch.setattr(registry, "OpenAIFileProvider", FakeOpenAIProvider)


def test_provider_selection_rejects_conflicting_configuration_and_unknown_provider():
    class Conflict(Config):
        TRANSCRIPTION_PROVIDER = "openai-file"
        TRANSCRIPTION_BACKEND = "gpu-local"

    with pytest.raises(TranscriptionConfigurationError) as conflict:
        registry.get_provider(Conflict())
    assert conflict.value.setting == "TRANSCRIPTION_PROVIDER"

    with pytest.raises(ProviderSelectionError) as unknown:
        registry.get_provider(Config(), provider="missing")
    assert unknown.value.setting == "TRANSCRIPTION_PROVIDER"


def test_provider_selection_uses_requested_canonical_legacy_and_default_values():
    provider = registry.get_provider(Config(), provider=ProviderName.OPENAI_FILE)
    assert isinstance(provider, FakeOpenAIProvider)

    class Canonical(Config):
        TRANSCRIPTION_PROVIDER = ProviderName.RUNPOD

    assert registry.get_provider(Canonical()).provider_name == "runpod"

    class Legacy(Config):
        TRANSCRIPTION_BACKEND = TranscriptionBackend.GPU_LOCAL

    assert registry.get_provider(Legacy()).provider_name == "gpu-local"
    assert registry.get_provider(Config()).provider_name == "gpu-local"


def test_runpod_configuration_requires_api_key_and_endpoint():
    class MissingKey(Config):
        RUNPOD_API_KEY = ""

    with pytest.raises(TranscriptionConfigurationError) as missing_key:
        registry.get_provider(MissingKey(), provider="runpod")
    assert missing_key.value.setting == "RUNPOD_API_KEY"

    class MissingEndpoint(Config):
        RUNPOD_ENDPOINT_ID = ""

    with pytest.raises(TranscriptionConfigurationError) as missing_endpoint:
        registry.get_provider(MissingEndpoint(), provider="runpod")
    assert missing_endpoint.value.setting == "RUNPOD_ENDPOINT_ID"


def test_scoped_gpu_provider_forwards_process_chunk_name_close_and_context_manager():
    provider = registry.ScopedGpuProvider(TranscriptionBackend.GPU_LOCAL)
    client = FakeClient.instances[-1]
    config = TranscriptionConfig(language="en")
    statuses = []
    heartbeats = []

    result = provider.process_audio("/tmp/audio.wav", config, statuses.append, lambda: heartbeats.append("beat"))

    assert result.text == "done"
    audio_path, forwarded_config, on_status_change, on_heartbeat = client.process_calls[0]
    assert (audio_path, forwarded_config, on_status_change) == ("/tmp/audio.wav", config, statuses.append)
    assert callable(on_heartbeat)
    assert provider.get_provider_name() == "client:gpu-local"

    import asyncio

    assert asyncio.run(provider.transcribe_chunk(b"abc")) == "chunk text"
    assert client.chunk_calls == [b"abc"]

    provider.close()
    provider.close()
    assert client.closed

    with registry.ScopedGpuProvider(TranscriptionBackend.RUNPOD) as scoped:
        scoped_client = FakeClient.instances[-1]
        assert scoped.provider_name == "runpod"
    assert scoped_client.closed


def test_scoped_gpu_transcribe_validates_request_and_maps_storage_source_to_config():
    provider = registry.ScopedGpuProvider(TranscriptionBackend.RUNPOD)
    request = BatchTranscriptionRequest(
        source=AudioSource.from_storage_key("audio/key.wav"),
        language="en",
        allowed_languages=frozenset({"en", "es"}),
        transcription_type=TranscriptionType.MEDICAL,
        timestamp_mode=TimestampMode.WORD,
        response_format="json",
        model="medical-model",
    )

    result = provider.transcribe(request)

    assert result.provider_name == "runpod"
    audio_path, config, *_ = FakeClient.instances[-1].process_calls[-1]
    assert audio_path == ""
    assert config.language == "en"
    assert config.allowed_languages == {"en", "es"}
    assert config.storage_key == "audio/key.wav"
    assert config.transcription_type == TranscriptionType.MEDICAL
    assert config.timestamp_mode == "word"
    assert config.response_format == "json"
    assert config.model == "medical-model"

    with pytest.raises(UnsupportedCapabilityError) as unsupported:
        provider.transcribe(BatchTranscriptionRequest(source=AudioSource.from_storage_key("audio/key.wav"), speaker_required=True))
    assert unsupported.value.capability == "cloud_diarization"


def test_factory_dispatch_uses_registered_builder_with_config():
    calls = []

    def builder(config):
        calls.append(config)
        return "provider"

    original = dict(registry.PROVIDER_BUILDERS)
    try:
        registry.PROVIDER_BUILDERS["custom"] = builder
        config = type("CustomConfig", (Config,), {"TRANSCRIPTION_PROVIDER": "custom"})()
        assert registry.get_provider(config) == "provider"
        assert calls == [config]
    finally:
        registry.PROVIDER_BUILDERS.clear()
        registry.PROVIDER_BUILDERS.update(original)
