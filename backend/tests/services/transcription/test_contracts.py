# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import pytest

from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    ProviderCapabilities,
    ProviderName,
)
from app.services.transcription.errors import InvalidAudioSourceError, UnsupportedCapabilityError
from app.services.transcription.gpu_client import GpuTranscriptionClient
from app.services.transcription.provider import validate_batch_request
from app.services.transcription.source import AudioSourceResolver
from app.services.transcription.types import ResultSegment, TranscriptionResult


def test_contract_gate_requires_cloud_diarization_for_speakers():
    request = BatchTranscriptionRequest(
        source=AudioSource.from_storage_key("meetings/1/audio.wav"),
        speaker_required=True,
    )
    with pytest.raises(UnsupportedCapabilityError) as raised:
        validate_batch_request(request, ProviderCapabilities(), provider=ProviderName.GPU_LOCAL.value)
    assert raised.value.capability == "cloud_diarization"
    assert raised.value.provider == "gpu-local"


def test_audio_source_contract_rejects_ambiguous_values():
    with pytest.raises(InvalidAudioSourceError):
        AudioSource()
    with pytest.raises(InvalidAudioSourceError):
        AudioSource(local_path="audio.wav", storage_key="audio.wav")


def test_source_resolver_preserves_gpu_storage_key_for_signed_urls(tmp_path):
    local = tmp_path / "audio.wav"
    local.touch()
    source = AudioSourceResolver.resolve_for_provider(
        local,
        ProviderName.RUNPOD,
        storage_key="meetings/1/audio.wav",
    )
    assert source.storage_key == "meetings/1/audio.wav"
    assert source.local_path is None


def test_result_preserves_unavailable_duration_without_fabricating_zero():
    result = TranscriptionResult(
        text="hello",
        language="en",
        segments=[ResultSegment(start=0.0, end=1.0, text="hello")],
        provider_name="gpu-local",
        recognition_method="gpu_whisperx",
        audio_duration_seconds=None,
        estimated_cost=0.0,
    )
    parsed = GpuTranscriptionClient._parse_response(
        {"text": "hello", "language": "en", "segments": []}
    )

    assert result.duration_seconds is None
    assert parsed.audio_duration_seconds is None
