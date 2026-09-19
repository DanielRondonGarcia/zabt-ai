# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for the OpenAI file transcription provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import TranscriptionType
from app.services.transcription import openai_provider as provider_module
from app.services.transcription.chunks import AudioChunk, AudioChunkSet, ChunkTranscription
from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    TimestampMode,
)
from app.services.transcription.errors import (
    InvalidAudioSourceError,
    ProviderRequestError,
    TranscriptionConfigurationError,
    UnsupportedCapabilityError,
)
from app.services.transcription.openai_provider import OpenAIFileProvider
from app.services.transcription.types import TranscriptionConfig, TranscriptionResult


class Config:
    TRANSCRIPTION_API_KEY = "test-key"
    ACTSIS_API_KEY = ""
    OPENAI_API_KEY = ""
    TRANSCRIPTION_BASE_URL = "https://api.openai.com/v1"
    TRANSCRIPTION_CLOUD_DIARIZATION = False
    TRANSCRIPTION_MODEL = "gpt-transcribe"
    TRANSCRIPTION_TIMESTAMP_MODEL = "whisper-1"
    TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES = 100
    TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 0
    TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES = 50
    TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS = 60.0
    TRANSCRIPTION_OPENAI_MAX_RETRIES = 2
    TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS = 0.25
    TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS = 1.0
    TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS = 9.0
    TRANSCRIPTION_OPENAI_MAX_CHUNKS = 10


class FakeTranscriptions:
    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []

    def create(self, *, file, **params):
        self.calls.append({"file_name": Path(file.name).name, "params": dict(params)})
        next_item = self.queue.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item


class FakeAudio:
    def __init__(self, queue):
        self.transcriptions = FakeTranscriptions(queue)


class FakeClient:
    def __init__(self, queue=()):
        self.audio = FakeAudio(queue)
        self.closed = False

    def close(self):
        self.closed = True


class Response:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, *, exclude_none=True):
        if exclude_none:
            return {key: value for key, value in self.__dict__.items() if value is not None}
        return dict(self.__dict__)


class StatusError(RuntimeError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _audio_file(tmp_path: Path, name: str = "meeting.wav", *, size: int = 10) -> Path:
    path = tmp_path / name
    path.write_bytes(b"a" * size)
    return path


def _provider(queue=(), *, config=None, sleep=None):
    return OpenAIFileProvider(config or Config(), client=FakeClient(queue), sleep=sleep or (lambda _delay: None))


def _request(path: Path, **overrides) -> BatchTranscriptionRequest:
    values = {"source": AudioSource.from_local_path(path)}
    values.update(overrides)
    return BatchTranscriptionRequest(**values)


def _single_chunk_set(tmp_path: Path, *, duration: float = 60) -> AudioChunkSet:
    workspace = tmp_path / "fake-chunks"
    workspace.mkdir(exist_ok=True)
    chunk_path = workspace / "chunk_000001.mp3"
    chunk_path.write_bytes(b"chunk")
    return AudioChunkSet(
        source_duration_seconds=duration,
        chunks=(AudioChunk(1, chunk_path, 0, duration, "mp3", "audio/mpeg", cleanup_owner="unit"),),
        workspace=workspace,
        cleanup_owner="unit",
    )


def test_configuration_accepts_override_key_and_rejects_invalid_models_and_numbers():
    class OpenAIOnly(Config):
        TRANSCRIPTION_API_KEY = ""
        OPENAI_API_KEY = "shared-key"
        TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"

    provider = _provider(config=OpenAIOnly())
    assert provider.model == "gpt-4o-mini-transcribe"

    class MissingKey(Config):
        TRANSCRIPTION_API_KEY = ""
        OPENAI_API_KEY = ""

    with pytest.raises(TranscriptionConfigurationError) as missing:
        _provider(config=MissingKey())
    assert missing.value.setting == "TRANSCRIPTION_API_KEY"

    class BadModel(Config):
        TRANSCRIPTION_MODEL = "not-a-supported-model"

    with pytest.raises(UnsupportedCapabilityError) as bad_model:
        _provider(config=BadModel())
    assert bad_model.value.capability == "model"

    class BadInteger(Config):
        TRANSCRIPTION_OPENAI_MAX_RETRIES = "many"

    with pytest.raises(TranscriptionConfigurationError) as bad_integer:
        _provider(config=BadInteger())
    assert bad_integer.value.setting == "TRANSCRIPTION_OPENAI_MAX_RETRIES"


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("TRANSCRIPTION_TIMESTAMP_MODEL", ""),
        ("TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES", 0),
        ("TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES", provider_module.OPENAI_FILE_SIZE_LIMIT_BYTES),
        ("TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES", -1),
        ("TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES", 0),
        ("TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES", 101),
        ("TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS", 0),
        ("TRANSCRIPTION_OPENAI_MAX_RETRIES", -1),
        ("TRANSCRIPTION_OPENAI_MAX_RETRIES", 6),
        ("TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS", -1),
        ("TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS", 0.1),
        ("TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", 0),
        ("TRANSCRIPTION_OPENAI_MAX_CHUNKS", 0),
    ],
)
def test_configuration_rejects_invalid_long_file_settings(setting, value):
    config = type("BadConfig", (Config,), {setting: value})()

    with pytest.raises(TranscriptionConfigurationError) as exc_info:
        _provider(config=config)

    assert exc_info.value.setting == setting or exc_info.value.setting == "TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS"


def test_request_validation_rejects_unsupported_sources_formats_and_capabilities(tmp_path):
    provider = _provider()
    missing = tmp_path / "missing.wav"

    with pytest.raises(InvalidAudioSourceError):
        provider.transcribe(_request(missing))

    unsupported = _audio_file(tmp_path, "meeting.txt")
    with pytest.raises(InvalidAudioSourceError):
        provider.transcribe(_request(unsupported))

    local_file = _audio_file(tmp_path, "meeting.wav")
    with pytest.raises(InvalidAudioSourceError):
        provider.transcribe(BatchTranscriptionRequest(source=AudioSource.from_storage_key("audio/key.wav")))

    for request in [
        _request(local_file, response_format="srt"),
        _request(local_file, transcription_type=TranscriptionType.MEDICAL),
        _request(local_file, speaker_required=True),
    ]:
        with pytest.raises(UnsupportedCapabilityError):
            provider.transcribe(request)


def test_actsis_gateway_uses_actsis_key_and_parses_diarized_speakers(tmp_path):
    class ActsisConfig(Config):
        TRANSCRIPTION_API_KEY = ""
        ACTSIS_API_KEY = "actsis-test-key"
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"
        TRANSCRIPTION_CLOUD_DIARIZATION = True
        TRANSCRIPTION_MODEL = "whisper-1"

    path = _audio_file(tmp_path)
    response = Response(
        text="hola equipo",
        language="es",
        duration=2.0,
        segments=[Response(start=0.0, end=2.0, text="hola equipo", speaker="SPEAKER_00")],
        words=[Response(start=0.0, end=2.0, word="hola", speaker="SPEAKER_00")],
    )
    client = FakeClient([response])
    provider = OpenAIFileProvider(ActsisConfig(), client=client)
    result = provider.transcribe(
        _request(path, model="whisper-1", response_format="diarized_json", speaker_required=True)
    )

    assert provider.base_url == "https://ai.actsis.internal/v1"
    assert result.segments[0].speaker == "SPEAKER_00"
    assert result.capability_gaps == ()
    assert client.audio.transcriptions.calls[0]["params"]["response_format"] == "diarized_json"


def test_custom_gateway_does_not_fall_back_to_official_openai_key():
    class MissingActsisKey(Config):
        TRANSCRIPTION_API_KEY = ""
        ACTSIS_API_KEY = ""
        OPENAI_API_KEY = "official-key-must-not-leak"
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"

    with pytest.raises(TranscriptionConfigurationError):
        OpenAIFileProvider(MissingActsisKey(), client=FakeClient())


def test_small_file_transcription_builds_parameters_maps_response_usage_segments_words_and_gaps(tmp_path):
    path = _audio_file(tmp_path)
    response = Response(
        text="hello world",
        language="en",
        duration=2.0,
        usage=Response(type="tokens", input_tokens=10, output_tokens=2, total_tokens=12),
        words=[{"word": "hello", "start": 0, "end": 0.5, "speaker": "S1"}],
        segments=[{"text": "hello", "start": 0, "end": 1.0, "speaker": "S1"}],
    )
    provider = _provider([response])

    result = provider.transcribe(
        _request(
            path,
            language=None,
            allowed_languages=frozenset({"en"}),
            response_format="verbose_json",
            model="gpt-4o-mini-transcribe",
            metadata={
                "prompt": "domain words",
                "temperature": 0,
                "chunking_strategy": "auto",
                "include": ["logprobs"],
                "known_speaker_names": ["Ada"],
                "known_speaker_references": {"Ada": "s3://ref"},
                "ignored": "not submitted",
            },
        )
    )

    assert provider._client.audio.transcriptions.calls == [
        {
            "file_name": "meeting.wav",
            "params": {
                "model": "gpt-4o-mini-transcribe",
                "response_format": "verbose_json",
                "language": "en",
                "prompt": "domain words",
                "temperature": 0,
                "chunking_strategy": "auto",
                "include": ["logprobs"],
                "known_speaker_names": ["Ada"],
                "known_speaker_references": {"Ada": "s3://ref"},
            },
        }
    ]
    assert result.text == "hello world"
    assert result.language == "en"
    assert result.usage.unit == "tokens"
    assert result.usage.total_units == 12
    assert [(segment.text, segment.start, segment.end, segment.speaker) for segment in result.segments] == [
        ("hello", 0.0, 1.0, "S1")
    ]
    assert [(word.word, word.speaker_label) for word in result.segments[0].words] == [("hello", "S1")]
    assert result.capability_gaps == ()
    assert result.metadata["submitted_language"] == "en"
    assert result.metadata["provider_usages"][0]["usage"] == {
        "type": "tokens",
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }


def test_response_mapping_uses_string_and_duration_usage_fallbacks_and_records_gaps(tmp_path):
    path = _audio_file(tmp_path)
    provider = _provider([Response(text="coarse text", usage={"type": "duration", "seconds": 3.5})])

    coarse = provider.transcribe(_request(path, allowed_languages=frozenset({"en", "es"})))

    assert coarse.text == "coarse text"
    assert [(segment.start, segment.end, segment.text) for segment in coarse.segments] == [(0.0, 3.5, "coarse text")]
    assert coarse.audio_duration_seconds == 3.5
    assert set(coarse.capability_gaps) == {"words", "speakers", "allowed_languages"}

    text_only = provider._response_to_result(
        "plain text",
        _request(path),
        model="gpt-transcribe",
        response_format="json",
        submitted_language=None,
    )
    assert text_only.text == "plain text"
    assert set(text_only.capability_gaps) == {"segments", "words", "speakers", "duration"}


def test_retry_policy_retries_transient_errors_with_heartbeat_and_wraps_terminal_failures(tmp_path):
    path = _audio_file(tmp_path)
    sleeps = []
    heartbeats = []
    provider = _provider(
        [StatusError("busy", 429), Response(text="done", duration=1)],
        sleep=sleeps.append,
    )

    result = provider.transcribe(_request(path), on_heartbeat=lambda: heartbeats.append("beat"))

    assert result.text == "done"
    assert sleeps == [0.25]
    assert len(provider._client.audio.transcriptions.calls) == 2
    assert len(heartbeats) >= 3

    non_transient = _provider([StatusError("bad request", 400)])
    with pytest.raises(ProviderRequestError) as bad:
        non_transient.transcribe(_request(path))
    assert bad.value.status_code == 400
    assert bad.value.attempts == 1

    exhausted = _provider([StatusError("still busy", 500), StatusError("again", 500), StatusError("last", 500)])
    with pytest.raises(ProviderRequestError) as terminal:
        exhausted.transcribe(_request(path))
    assert terminal.value.status_code == 500
    assert terminal.value.attempts == 3


def test_status_and_heartbeat_callbacks_ignore_heartbeat_failures_and_report_small_file_stages(tmp_path):
    path = _audio_file(tmp_path)
    provider = _provider([Response(text="timed", duration=1), Response(text="timed", segments=[{"start": 0, "end": 1, "text": "timed"}])])
    statuses = []
    beats = []

    def heartbeat():
        beats.append("beat")
        raise RuntimeError("callback failure must be nonfatal")

    result = provider.transcribe(
        _request(path, timestamp_mode=TimestampMode.SEGMENT),
        on_status_change=statuses.append,
        on_heartbeat=heartbeat,
    )

    assert result.text == "timed"
    assert statuses == ["transcribing", "timestamping"]
    assert beats


def test_timestamp_success_uses_whisper_params_merges_metadata_and_removes_recovered_gaps(tmp_path):
    path = _audio_file(tmp_path)
    provider = _provider(
        [
            Response(text="primary", language="unknown", duration=None, usage={"type": "tokens", "total_tokens": 3}),
            Response(
                text="primary",
                language="en",
                duration=2,
                usage={"type": "duration", "seconds": 2},
                words=[{"word": "primary", "start": 0, "end": 1}],
                segments=[{"text": "primary", "start": 0, "end": 2}],
            ),
        ]
    )

    result = provider.transcribe(
        _request(
            path,
            timestamp_mode=TimestampMode.WORD,
            metadata={"prompt": "terms", "temperature": 0.2, "known_speaker_names": ["ignored"]},
        )
    )

    assert [call["params"] for call in provider._client.audio.transcriptions.calls] == [
        {"model": "gpt-transcribe", "response_format": "json", "prompt": "terms", "temperature": 0.2, "known_speaker_names": ["ignored"]},
        {"model": "whisper-1", "response_format": "verbose_json", "timestamp_granularities": ["word"], "prompt": "terms", "temperature": 0.2},
    ]
    assert result.language == "en"
    assert result.audio_duration_seconds == 2.0
    assert result.segments[0].words[0].word == "primary"
    assert "words" not in result.capability_gaps
    assert "duration" not in result.capability_gaps
    assert result.metadata["primary_model"] == "gpt-transcribe"
    assert result.metadata["timestamp_model"] == "whisper-1"
    assert result.metadata["timestamp_pass"]["status"] == "succeeded"
    assert [usage["role"] for usage in result.metadata["provider_usages"]] == ["primary", "timestamps"]


def test_timestamp_failure_and_unavailable_timestamps_are_nonfatal_capability_gaps(tmp_path):
    path = _audio_file(tmp_path)
    provider = _provider([Response(text="primary", duration=2), StatusError("timeout", 408), StatusError("timeout", 408), StatusError("timeout", 408)])

    failed = provider.transcribe(_request(path, timestamp_mode=TimestampMode.SEGMENT))

    assert failed.text == "primary"
    assert "timestamps:segment" in failed.capability_gaps
    assert failed.metadata["timestamp_pass"]["status"] == "failed"
    assert failed.metadata["timestamp_pass"]["error"]["type"] == "ProviderRequestError"
    assert failed.metadata["provider_usages"][-1]["status"] == "failed"

    unavailable = _provider([Response(text="primary", duration=2), Response(text="no timing", duration=2)])
    fallback = unavailable.transcribe(_request(path, timestamp_mode=TimestampMode.WORD))
    assert fallback.text == "primary"
    assert "timestamps:word" in fallback.capability_gaps
    assert fallback.metadata["timestamp_pass"]["error"]["type"] == "_TimestampPassUnavailable"

    bad_timestamp_model = _provider(
        [Response(text="primary", duration=2)],
        config=type("BadTimestamp", (Config,), {"TRANSCRIPTION_TIMESTAMP_MODEL": "gpt-transcribe"})(),
    )
    bad_result = bad_timestamp_model.transcribe(_request(path, timestamp_mode=TimestampMode.SEGMENT))
    assert "timestamps:segment" in bad_result.capability_gaps
    assert bad_result.metadata["timestamp_pass"]["error"]["type"] == "UnsupportedCapabilityError"


def test_official_openai_ignores_direct_upload_threshold_and_keeps_chunking(tmp_path, monkeypatch):
    class OfficialWithDirectThreshold(Config):
        TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 200

    path = _audio_file(tmp_path, size=101)
    chunk_set = _single_chunk_set(tmp_path)
    monkeypatch.setattr(provider_module, "extract_audio_chunks", lambda *args, **kwargs: chunk_set)
    merged = TranscriptionResult(
        text="merged",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=60,
        estimated_cost=None,
        model="gpt-transcribe",
    )
    monkeypatch.setattr(provider_module, "merge_chunk_results", lambda _results, *, audio_duration_seconds=None: merged)
    provider = _provider([Response(text="chunk", duration=60)], config=OfficialWithDirectThreshold())

    result = provider.transcribe(_request(path))

    assert result.text == "merged"
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == ["chunk_000001.mp3"]


def test_custom_gateway_default_keeps_chunking_large_files(tmp_path, monkeypatch):
    class CustomDefault(Config):
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"

    path = _audio_file(tmp_path, size=101)
    chunk_set = _single_chunk_set(tmp_path)
    monkeypatch.setattr(provider_module, "extract_audio_chunks", lambda *args, **kwargs: chunk_set)
    merged = TranscriptionResult(
        text="custom chunked",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=60,
        estimated_cost=None,
        model="gpt-transcribe",
    )
    monkeypatch.setattr(provider_module, "merge_chunk_results", lambda _results, *, audio_duration_seconds=None: merged)
    provider = _provider([Response(text="chunk", duration=60)], config=CustomDefault())

    result = provider.transcribe(_request(path))

    assert result.text == "custom chunked"
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == ["chunk_000001.mp3"]


def test_custom_gateway_direct_upload_sends_complete_file_without_chunk_extraction(tmp_path, monkeypatch):
    class CustomDirect(Config):
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"
        TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 200

    path = _audio_file(tmp_path, size=101)
    monkeypatch.setattr(
        provider_module,
        "extract_audio_chunks",
        lambda *args, **kwargs: pytest.fail("direct upload must not extract local chunks"),
    )
    provider = _provider([Response(text="direct", duration=60)], config=CustomDirect())

    result = provider.transcribe(_request(path))

    assert result.text == "direct"
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == ["meeting.wav"]


def test_custom_gateway_direct_upload_size_rejection_falls_back_once_to_chunks(tmp_path, monkeypatch):
    class CustomDirect(Config):
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"
        TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 200

    path = _audio_file(tmp_path, size=101)
    chunk_set = _single_chunk_set(tmp_path)
    extract_calls = []

    def fake_extract(*args, **kwargs):
        extract_calls.append((args, kwargs))
        return chunk_set

    monkeypatch.setattr(provider_module, "extract_audio_chunks", fake_extract)
    merged = TranscriptionResult(
        text="fallback chunked",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=60,
        estimated_cost=None,
        model="gpt-transcribe",
    )
    monkeypatch.setattr(provider_module, "merge_chunk_results", lambda _results, *, audio_duration_seconds=None: merged)
    provider = _provider(
        [StatusError("payload file too large", 413), Response(text="chunk", duration=60)],
        config=CustomDirect(),
    )
    statuses = []

    result = provider.transcribe(_request(path), on_status_change=statuses.append)

    assert result.text == "fallback chunked"
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == [
        "meeting.wav",
        "chunk_000001.mp3",
    ]
    assert len(extract_calls) == 1
    assert statuses == ["transcribing", "preparing_audio", "transcribing_chunk (1/1)", "transcribing_chunk (1/1) complete"]


def test_custom_gateway_direct_upload_litellm_400_size_rejection_falls_back_to_chunks(tmp_path, monkeypatch):
    class CustomDirect(Config):
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"
        TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 200

    path = _audio_file(tmp_path, size=101)
    chunk_set = _single_chunk_set(tmp_path)
    monkeypatch.setattr(provider_module, "extract_audio_chunks", lambda *args, **kwargs: chunk_set)
    merged = TranscriptionResult(
        text="fallback chunked",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=60,
        estimated_cost=None,
        model="gpt-transcribe",
    )
    monkeypatch.setattr(provider_module, "merge_chunk_results", lambda _results, *, audio_duration_seconds=None: merged)
    provider = _provider(
        [StatusError("LiteLLM BadRequest: file payload exceeds maximum size", 400), Response(text="chunk", duration=60)],
        config=CustomDirect(),
    )

    result = provider.transcribe(_request(path))

    assert result.text == "fallback chunked"
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == [
        "meeting.wav",
        "chunk_000001.mp3",
    ]


def test_custom_gateway_direct_upload_does_not_fallback_on_auth_or_server_errors(tmp_path, monkeypatch):
    class CustomDirect(Config):
        TRANSCRIPTION_BASE_URL = "https://ai.actsis.internal/v1"
        TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES = 200

    path = _audio_file(tmp_path, size=101)
    monkeypatch.setattr(
        provider_module,
        "extract_audio_chunks",
        lambda *args, **kwargs: pytest.fail("non-size errors must not fallback to chunks"),
    )
    provider = _provider([StatusError("invalid api key", 401)], config=CustomDirect())

    with pytest.raises(ProviderRequestError) as exc_info:
        provider.transcribe(_request(path))

    assert exc_info.value.status_code == 401
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == ["meeting.wav"]


def test_large_file_uses_fake_chunk_extraction_transcribes_chunks_merges_and_cleans_up(tmp_path, monkeypatch):
    path = _audio_file(tmp_path, size=101)
    workspace = tmp_path / "fake-chunks"
    workspace.mkdir()
    chunk_paths = [workspace / "chunk_000001.mp3", workspace / "chunk_000002.mp3"]
    for chunk_path in chunk_paths:
        chunk_path.write_bytes(b"chunk")
    chunk_set = AudioChunkSet(
        source_duration_seconds=120,
        chunks=(
            AudioChunk(1, chunk_paths[0], 0, 60, "mp3", "audio/mpeg", cleanup_owner="unit"),
            AudioChunk(2, chunk_paths[1], 60, 60, "mp3", "audio/mpeg", cleanup_owner="unit"),
        ),
        workspace=workspace,
        cleanup_owner="unit",
    )
    extract_calls = []
    cleaned = []

    def fake_extract(*args, **kwargs):
        extract_calls.append((args, kwargs))
        return chunk_set

    def fake_cleanup():
        cleaned.append(True)

    chunk_set.cleanup = fake_cleanup
    merged_result = TranscriptionResult(
        text="merged",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=120,
        estimated_cost=None,
        model="gpt-transcribe",
        metadata={"merged": True},
    )
    merge_calls = []

    def fake_merge(chunk_results, *, audio_duration_seconds=None):
        merge_calls.append((chunk_results, audio_duration_seconds))
        return merged_result

    monkeypatch.setattr(provider_module, "extract_audio_chunks", fake_extract)
    monkeypatch.setattr(provider_module, "merge_chunk_results", fake_merge)
    provider = _provider([Response(text="one", duration=60), Response(text="two", duration=60)])
    statuses = []
    beats = []

    result = provider.transcribe(_request(path), on_status_change=statuses.append, on_heartbeat=lambda: beats.append("beat"))

    assert result is merged_result
    assert result.metadata["chunk_max_bytes"] == 50
    assert result.metadata["chunk_duration_seconds"] == 60.0
    assert extract_calls[0][0][0] == path
    assert extract_calls[0][1] == {
        "chunk_duration_seconds": 60.0,
        "max_chunk_bytes": 50,
        "max_chunks": 10,
        "command_timeout_seconds": 9.0,
    }
    assert [call["file_name"] for call in provider._client.audio.transcriptions.calls] == ["chunk_000001.mp3", "chunk_000002.mp3"]
    assert [chunk_result.chunk.index for chunk_result in merge_calls[0][0]] == [1, 2]
    assert merge_calls[0][1] == 120
    assert statuses == [
        "preparing_audio",
        "transcribing_chunk (1/2)",
        "transcribing_chunk (1/2) complete",
        "transcribing_chunk (2/2)",
        "transcribing_chunk (2/2) complete",
    ]
    assert beats
    assert cleaned == [True]


def test_large_file_timestamp_chunks_report_timestamp_stages(tmp_path, monkeypatch):
    path = _audio_file(tmp_path, size=101)
    workspace = tmp_path / "chunks"
    workspace.mkdir()
    chunk_path = workspace / "chunk_000001.mp3"
    chunk_path.write_bytes(b"chunk")
    chunk_set = AudioChunkSet(
        source_duration_seconds=5,
        chunks=(AudioChunk(1, chunk_path, 0, 5, "mp3", "audio/mpeg", cleanup_owner="unit"),),
        workspace=workspace,
        cleanup_owner="unit",
    )
    monkeypatch.setattr(provider_module, "extract_audio_chunks", lambda *args, **kwargs: chunk_set)
    merged = TranscriptionResult(
        text="merged",
        language="en",
        segments=[],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=5,
        estimated_cost=None,
        model="gpt-transcribe",
    )
    monkeypatch.setattr(provider_module, "merge_chunk_results", lambda _results, *, audio_duration_seconds=None: merged)
    provider = _provider([
        Response(text="primary", duration=5),
        Response(text="primary", duration=5, segments=[{"start": 0, "end": 5, "text": "primary"}]),
    ])
    statuses = []

    result = provider.transcribe(_request(path, timestamp_mode=TimestampMode.SEGMENT), on_status_change=statuses.append)

    assert result.text == "merged"
    assert statuses == [
        "preparing_audio",
        "transcribing_chunk (1/1)",
        "timestamping_chunk (1/1)",
        "transcribing_chunk (1/1) complete",
    ]


def test_process_audio_context_manager_close_and_unsupported_realtime(tmp_path):
    path = _audio_file(tmp_path)
    provider = _provider([Response(text="processed", language="es", duration=1)])

    result = provider.process_audio(
        str(path),
        TranscriptionConfig(
            language="es",
            allowed_languages={"es"},
            response_format="json",
            model="gpt-transcribe",
        ),
    )

    assert result.text == "processed"
    assert provider.get_provider_name() == "openai-file"
    assert provider._client.audio.transcriptions.calls[0]["params"]["language"] == "es"

    provider.close()
    provider.close()
    assert provider._client.closed is True

    with _provider([Response(text="ctx", duration=1)]) as managed:
        assert managed is not None
    assert managed._client.closed is True


@pytest.mark.asyncio
async def test_transcribe_chunk_rejects_unsupported_realtime():
    provider = _provider()

    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await provider.transcribe_chunk(b"audio")

    assert exc_info.value.capability == "realtime"
