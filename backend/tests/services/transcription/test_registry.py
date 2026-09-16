# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models import TranscriptionBackend, TranscriptionType
from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    ProviderName,
)
from app.services.transcription.errors import TranscriptionConfigurationError
from app.services.transcription.errors import ProviderSelectionError, UnsupportedCapabilityError
from app.services.transcription.openai_provider import OpenAIFileProvider, SUPPORTED_MODELS
from app.services.transcription.provider import validate_batch_request
from app.services.transcription.registry import PROVIDER_BUILDERS, get_provider


def _openai_provider(model: str = "gpt-transcribe") -> OpenAIFileProvider:
    client = MagicMock()
    return OpenAIFileProvider(
        SimpleNamespace(TRANSCRIPTION_API_KEY="test-key", TRANSCRIPTION_MODEL=model),
        client=client,
    )


def test_registry_contains_only_first_slice_provider_seams():
    assert set(PROVIDER_BUILDERS) == {"gpu-local", "runpod", "openai-file"}


def test_registry_requires_both_openai_credentials_without_fallback():
    config = SimpleNamespace(
        TRANSCRIPTION_PROVIDER="openai-file",
        TRANSCRIPTION_BACKEND=None,
        TRANSCRIPTION_API_KEY="",
        OPENAI_API_KEY="",
    )
    with pytest.raises(TranscriptionConfigurationError, match="TRANSCRIPTION_API_KEY or OPENAI_API_KEY"):
        get_provider(config)


def test_registry_builds_fresh_gpu_scopes():
    config = SimpleNamespace(TRANSCRIPTION_PROVIDER="gpu-local", TRANSCRIPTION_BACKEND=None)
    with patch(
        "app.services.transcription.registry.ScopedGpuProvider",
        side_effect=[object(), object()],
    ) as builder:
        first = get_provider(config)
        second = get_provider(config)
    assert first is not second
    assert builder.call_count == 2


def test_registry_keeps_explicit_gpu_and_runpod_rollback_targets():
    with patch(
        "app.services.transcription.registry.ScopedGpuProvider",
        return_value=object(),
    ) as builder:
        get_provider(SimpleNamespace(TRANSCRIPTION_PROVIDER="gpu-local", TRANSCRIPTION_BACKEND=None))
        get_provider(
            SimpleNamespace(
                TRANSCRIPTION_PROVIDER="runpod",
                TRANSCRIPTION_BACKEND=None,
                RUNPOD_API_KEY="test-key",
                RUNPOD_ENDPOINT_ID="test-endpoint",
            )
        )

    assert [call.args[0] for call in builder.call_args_list] == [
        TranscriptionBackend.GPU_LOCAL,
        TranscriptionBackend.RUNPOD,
    ]


def test_registry_rejects_conflicting_canonical_and_legacy_provider():
    config = SimpleNamespace(TRANSCRIPTION_PROVIDER="openai-file", TRANSCRIPTION_BACKEND="gpu-local")
    with pytest.raises(TranscriptionConfigurationError, match="disagree"):
        get_provider(config)


def test_registry_rejects_speaker_required_before_provider_io():
    request = BatchTranscriptionRequest(
        source=AudioSource.from_storage_key("meetings/1/audio.wav"),
        speaker_required=True,
    )
    with pytest.raises(UnsupportedCapabilityError) as raised:
        validate_batch_request(
            request,
            OpenAIFileProvider.capabilities,
            provider=ProviderName.OPENAI_FILE.value,
            model="gpt-transcribe",
        )
    assert raised.value.capability == "cloud_diarization"


def test_diarized_model_remains_a_future_only_candidate():
    assert "gpt-4o-transcribe-diarize" not in SUPPORTED_MODELS
    with pytest.raises(UnsupportedCapabilityError) as raised:
        OpenAIFileProvider._validate_model("gpt-4o-transcribe-diarize")
    assert raised.value.capability == "model"


def test_openai_file_is_batch_only():
    provider = _openai_provider()
    try:
        with pytest.raises(UnsupportedCapabilityError) as raised:
            asyncio.run(provider.transcribe_chunk(b"audio"))
        assert raised.value.capability == "realtime"
        provider._client.audio.transcriptions.create.assert_not_called()
    finally:
        provider.close()


@pytest.mark.parametrize("provider_name", ["openai-compatible", "ollama"])
def test_unverified_audio_provider_is_rejected_without_fallback(provider_name):
    with pytest.raises(ProviderSelectionError):
        get_provider(
            SimpleNamespace(
                TRANSCRIPTION_PROVIDER=provider_name,
                TRANSCRIPTION_BACKEND=None,
            )
        )


def test_openai_file_rejects_medical_transcription():
    provider = _openai_provider()
    try:
        request = BatchTranscriptionRequest(
            source=AudioSource.from_storage_key("meetings/1/audio.wav"),
            transcription_type=TranscriptionType.MEDICAL,
        )
        with pytest.raises(UnsupportedCapabilityError) as raised:
            provider.transcribe(request)
        assert raised.value.capability == "medical"
        provider._client.audio.transcriptions.create.assert_not_called()
    finally:
        provider.close()
