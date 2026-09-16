# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Unit coverage for the official OpenAI file-transcription boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    TimestampMode,
)
from app.services.transcription.errors import (
    ChunkTooLargeError,
    ProviderRequestError,
    InvalidAudioSourceError,
    TranscriptionConfigurationError,
    TranscriptionError,
    UnsupportedCapabilityError,
)
from app.services.transcription import openai_provider
from app.services.transcription.chunks import AudioChunk, AudioChunkSet
from app.services.transcription.openai_provider import OpenAIFileProvider


def _client(response=None, *, side_effect=None):
    create = MagicMock(return_value=response, side_effect=side_effect)
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)),
        close=MagicMock(),
    )
    return client, create


def _provider(client, model="gpt-transcribe", *, sleep=None, **settings):
    config = {
        "TRANSCRIPTION_API_KEY": "test-key",
        "TRANSCRIPTION_MODEL": model,
    }
    config.update(settings)
    return OpenAIFileProvider(
        SimpleNamespace(**config),
        client=client,
        sleep=sleep,
    )


@pytest.mark.parametrize(
    ("suffix", "model"),
    [("wav", "gpt-transcribe"), ("mp3", "gpt-4o-mini-transcribe")],
)
def test_submits_primary_text_and_secondary_segment_timestamps(tmp_path, suffix, model):
    path = tmp_path / f"fixture.{suffix}"
    payload = b"RIFF-test-wav" if suffix == "wav" else b"ID3-test-mp3"
    path.write_bytes(payload)
    seen = []

    def create(*, file, **params):
        seen.append({"bytes": file.read(), "name": file.name, "params": params})
        if params["model"] == model:
            return {
                "text": "canonical primary text",
                "language": "en",
                "usage": {"type": "tokens", "input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            }
        return {
            "text": "segment timing text",
            "language": "en",
            "segments": [{"start": 0, "end": 1, "text": "segment timing text"}],
            "usage": {"type": "duration", "seconds": 1.0},
        }

    client, mocked_create = _client()
    client.audio.transcriptions.create.side_effect = create
    result = _provider(client, model).transcribe(
        BatchTranscriptionRequest(
            source=AudioSource.from_local_path(path),
            language="en",
            timestamp_mode=TimestampMode.SEGMENT,
            response_format="json",
            metadata={"prompt": "meeting"},
        )
    )

    assert [call["bytes"] for call in seen] == [payload, payload]
    assert all(call["name"].endswith(f"fixture.{suffix}") for call in seen)
    assert seen[0]["params"] == {
        "model": model,
        "response_format": "json",
        "language": "en",
        "prompt": "meeting",
    }
    assert seen[1]["params"] == {
        "model": "whisper-1",
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
        "language": "en",
        "prompt": "meeting",
    }
    assert seen[0]["params"]["model"] == model
    assert "timestamp_granularities" not in seen[0]["params"]
    assert result.model == model
    assert result.text == "canonical primary text"
    assert result.language == "en"
    assert result.metadata["response_format"] == "json"
    assert result.usage.unit == "tokens"
    assert result.usage.total_units == 14
    assert result.metadata["timestamp_pass"]["status"] == "succeeded"
    assert result.metadata["timestamp_pass"]["usage"] == {"type": "duration", "seconds": 1.0}
    assert [record["role"] for record in result.metadata["provider_usages"]] == [
        "primary",
        "timestamps",
    ]
    assert mocked_create.call_count == 2
    assert all(call.kwargs["file"].closed for call in mocked_create.call_args_list)


def test_word_timestamp_pass_keeps_primary_text_and_separates_usage_metadata(tmp_path):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client()

    def transcribe(*, file, **params):
        if params["model"] == "gpt-transcribe":
            return {
                "text": "high quality summary source",
                "language": "en",
                "usage": {"type": "tokens", "input_tokens": 20, "output_tokens": 7, "total_tokens": 27},
            }
        return {
            "text": "high quality summary source",
            "language": "en",
            "words": [
                {"word": "high", "start": 0.0, "end": 0.4},
                {"word": "quality", "start": 0.5, "end": 0.9},
            ],
            "usage": {"type": "duration", "seconds": 1.2},
        }

    create.side_effect = transcribe
    result = _provider(client).transcribe(
        BatchTranscriptionRequest(
            source=AudioSource.from_local_path(path),
            language="en",
            timestamp_mode=TimestampMode.WORD,
            response_format="json",
        )
    )

    assert result.text == "high quality summary source"
    assert result.model == "gpt-transcribe"
    assert result.usage.raw == {"type": "tokens", "input_tokens": 20, "output_tokens": 7, "total_tokens": 27}
    assert [(word.word, word.start, word.end) for word in result.segments[0].words] == [
        ("high", 0.0, 0.4),
        ("quality", 0.5, 0.9),
    ]
    assert result.segments[0].speaker == "SPEAKER_UNKNOWN"
    assert result.metadata["primary_model"] == "gpt-transcribe"
    assert result.metadata["timestamp_model"] == "whisper-1"
    assert result.metadata["timestamp_pass"]["response_format"] == "verbose_json"
    assert result.metadata["timestamp_pass"]["timestamp_granularities"] == ["word"]
    assert result.metadata["timestamp_pass"]["usage"] == {"type": "duration", "seconds": 1.2}
    assert create.call_count == 2
    assert create.call_args_list[0].kwargs["response_format"] == "json"
    assert create.call_args_list[1].kwargs["response_format"] == "verbose_json"
    assert create.call_args_list[1].kwargs["timestamp_granularities"] == ["word"]


def test_constructs_the_official_sdk_client_from_transcription_credentials():
    client, _ = _client()
    with patch("app.services.transcription.openai_provider.OpenAI", return_value=client) as sdk:
        provider = OpenAIFileProvider(
            SimpleNamespace(TRANSCRIPTION_API_KEY="test-key", TRANSCRIPTION_MODEL="gpt-transcribe")
        )
    sdk.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )
    provider.close()


def test_uses_shared_openai_key_when_transcription_override_is_empty():
    client, _ = _client()
    with patch("app.services.transcription.openai_provider.OpenAI", return_value=client) as sdk:
        provider = OpenAIFileProvider(
            SimpleNamespace(
                TRANSCRIPTION_API_KEY="",
                OPENAI_API_KEY="shared-key",
                TRANSCRIPTION_MODEL="gpt-transcribe",
            )
        )

    sdk.assert_called_once_with(
        api_key="shared-key",
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )
    provider.close()


def test_transcription_key_takes_precedence_over_shared_openai_key():
    client, _ = _client()
    with patch("app.services.transcription.openai_provider.OpenAI", return_value=client) as sdk:
        provider = OpenAIFileProvider(
            SimpleNamespace(
                TRANSCRIPTION_API_KEY="transcription-key",
                OPENAI_API_KEY="shared-key",
                TRANSCRIPTION_MODEL="gpt-transcribe",
            )
        )

    sdk.assert_called_once_with(
        api_key="transcription-key",
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )
    provider.close()


def test_fails_when_both_openai_credentials_are_empty():
    with pytest.raises(
        TranscriptionConfigurationError,
        match="TRANSCRIPTION_API_KEY or OPENAI_API_KEY",
    ):
        OpenAIFileProvider(
            SimpleNamespace(
                TRANSCRIPTION_API_KEY="",
                OPENAI_API_KEY="",
                TRANSCRIPTION_MODEL="gpt-transcribe",
            )
        )


def test_audio_client_ignores_summary_openai_base_url():
    client, _ = _client()
    with patch("app.services.transcription.openai_provider.OpenAI", return_value=client) as sdk:
        provider = OpenAIFileProvider(
            SimpleNamespace(
                TRANSCRIPTION_API_KEY="",
                OPENAI_API_KEY="shared-key",
                OPENAI_BASE_URL="https://openrouter.ai/api/v1",
                TRANSCRIPTION_MODEL="gpt-transcribe",
            )
        )

    sdk.assert_called_once_with(
        api_key="shared-key",
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )
    provider.close()


@pytest.mark.parametrize(
    ("usage", "unit", "input_units", "output_units", "total_units", "duration"),
    [
        ({"type": "duration", "seconds": 3.5}, "seconds", 3.5, None, 3.5, 3.5),
        ({"type": "tokens", "input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
         "tokens", 10, 2, 12, None),
    ],
)
def test_preserves_original_usage_units(tmp_path, usage, unit, input_units, output_units, total_units, duration):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, _ = _client({"text": "hello", "usage": usage})
    result = _provider(client).transcribe(
        BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
    )

    assert result.usage.unit == unit
    assert result.usage.input_units == input_units
    assert result.usage.output_units == output_units
    assert result.usage.total_units == total_units
    assert result.usage.raw == usage
    assert result.audio_duration_seconds == duration


def test_missing_fields_remain_gaps_and_segment_speaker_is_unknown(tmp_path):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, _ = _client({
        "text": "hello",
        "segments": [{"start": 0, "end": 1, "text": "hello"}],
    })
    result = _provider(client).transcribe(
        BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
    )

    assert result.language == "unknown"
    assert result.segments[0].speaker == "SPEAKER_UNKNOWN"
    assert set(result.capability_gaps) == {"words", "speakers", "duration"}


def test_text_only_response_with_duration_gets_coarse_segment(tmp_path):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, _ = _client({
        "text": "hello from a text-only response",
        "usage": {"type": "duration", "seconds": 2.5},
    })

    result = _provider(client).transcribe(
        BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
    )

    assert result.text == "hello from a text-only response"
    assert len(result.segments) == 1
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 2.5
    assert result.segments[0].text == result.text
    assert result.segments[0].speaker == "SPEAKER_UNKNOWN"
    assert result.segments[0].words == []


def test_text_only_response_without_duration_keeps_text_without_timing(tmp_path):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, _ = _client({"text": "timing is unavailable"})

    result = _provider(client).transcribe(
        BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
    )

    assert result.text == "timing is unavailable"
    assert result.segments == []
    assert "duration" in result.capability_gaps


@pytest.mark.parametrize(
    ("request_kwargs", "capability"),
    [
        ({"response_format": "diarized_json"}, "response_format"),
        ({"model": "whisper-1"}, "model"),
    ],
)
def test_invalid_model_or_response_options_fail_before_sdk_call(tmp_path, request_kwargs, capability):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client({"text": "should not run"})
    with pytest.raises(UnsupportedCapabilityError) as raised:
        _provider(client).transcribe(
            BatchTranscriptionRequest(
                source=AudioSource.from_local_path(path),
                **request_kwargs,
            )
        )

    assert raised.value.capability == capability
    create.assert_not_called()


def test_storage_only_source_is_rejected_without_fallback(tmp_path):
    client, create = _client({"text": "should not run"})
    with pytest.raises(InvalidAudioSourceError):
        _provider(client).transcribe(
            BatchTranscriptionRequest(source=AudioSource.from_storage_key("audio.wav"))
        )
    create.assert_not_called()


def test_sdk_failure_is_typed_and_never_retries_with_another_provider(tmp_path):
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client(side_effect=RuntimeError("provider unavailable"))
    with pytest.raises(TranscriptionError, match="OpenAI file transcription failed"):
        _provider(client).transcribe(
            BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
        )
    assert create.call_count == 1


def test_client_cleanup_is_idempotent():
    client, _ = _client()
    provider = _provider(client)
    provider.close()
    provider.close()
    assert client.close.call_count == 1


def test_file_below_safe_threshold_uses_one_request(tmp_path):
    path = tmp_path / "small.mp4"
    path.write_bytes(b"synthetic-mp4")
    client, create = _client({"text": "small file"})

    result = _provider(
        client,
        TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES=1024,
        TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES=512,
    ).transcribe(
        BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
    )

    assert result.text == "small file"
    assert create.call_count == 1


def test_timestamp_mode_none_does_not_make_a_secondary_request(tmp_path):
    path = tmp_path / "small.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client({
        "text": "primary only",
        "usage": {"type": "duration", "seconds": 1.0},
    })

    result = _provider(client).transcribe(
        BatchTranscriptionRequest(
            source=AudioSource.from_local_path(path),
            timestamp_mode=TimestampMode.NONE,
            response_format="json",
        )
    )

    assert result.text == "primary only"
    assert create.call_count == 1
    assert "timestamp_pass" not in result.metadata
    assert [record["role"] for record in result.metadata["provider_usages"]] == ["primary"]


def test_timestamp_failure_preserves_primary_text_and_records_gap_without_words(tmp_path):
    path = tmp_path / "failure.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client()

    def create_response(*, file, **params):
        if params["model"] == "gpt-transcribe":
            return {
                "text": "primary text survives timestamp failure",
                "usage": {"type": "duration", "seconds": 2.0},
            }
        raise _StatusError(503)

    create.side_effect = create_response
    result = _provider(
        client,
        TRANSCRIPTION_OPENAI_MAX_RETRIES=1,
        sleep=lambda _delay: None,
    ).transcribe(
        BatchTranscriptionRequest(
            source=AudioSource.from_local_path(path),
            timestamp_mode=TimestampMode.WORD,
            response_format="json",
        )
    )

    assert result.text == "primary text survives timestamp failure"
    assert result.segments
    assert all(segment.words == [] for segment in result.segments)
    assert "timestamps:word" in result.capability_gaps
    assert result.metadata["timestamp_pass"]["status"] == "failed"
    assert result.metadata["timestamp_pass"]["error"]["attempts"] == 2
    timestamp_record = next(
        record for record in result.metadata["provider_usages"] if record["role"] == "timestamps"
    )
    assert timestamp_record["status"] == "failed"
    assert create.call_count == 3


def test_large_mp4_uses_ordered_chunks_preserves_options_and_cleans_workspace(tmp_path, monkeypatch):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"x" * 32)
    workspaces = []

    def fake_extract(source, workspace, **kwargs):
        workspace.mkdir(parents=True, exist_ok=True)
        workspaces.append(workspace)
        first = workspace / "chunk_000001.mp3"
        second = workspace / "chunk_000002.mp3"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        return AudioChunkSet(
            source_duration_seconds=12.0,
            workspace=workspace,
            chunks=(
                AudioChunk(1, first, 0.0, 6.0, "mp3", "audio/mpeg"),
                AudioChunk(2, second, 6.0, 6.0, "mp3", "audio/mpeg"),
            ),
        )

    monkeypatch.setattr(openai_provider, "extract_audio_chunks", fake_extract)
    seen = []
    primary_count = 0

    def create(*, file, **params):
        nonlocal primary_count
        seen.append((file.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1], file.read(), params))
        if params["model"] == "gpt-transcribe":
            primary_count += 1
            return {
                "text": "hello there" if primary_count == 1 else "there friend",
                "language": "en",
                "usage": {"type": "duration", "seconds": 6.0},
            }
        return {
            "text": "hello there" if primary_count == 1 else "there friend",
            "language": "en",
            "words": (
                [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "there", "start": 0.6, "end": 1.0},
                ]
                if primary_count == 1
                else [
                    {"word": "there", "start": 0.0, "end": 0.4},
                    {"word": "friend", "start": 0.5, "end": 1.0},
                ]
            ),
            "usage": {"type": "duration", "seconds": 6.0},
        }

    client, _ = _client()
    client.audio.transcriptions.create.side_effect = create
    statuses = []
    heartbeats = []
    provider = _provider(
        client,
        TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES=10,
        TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES=9,
        TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS=6,
        sleep=lambda _delay: None,
    )

    result = provider.transcribe(
        BatchTranscriptionRequest(
            source=AudioSource.from_local_path(path),
            language="en",
            timestamp_mode=TimestampMode.WORD,
            response_format="json",
            metadata={"prompt": "meeting context"},
        ),
        on_status_change=statuses.append,
        on_heartbeat=lambda: heartbeats.append(True),
    )

    assert [name for name, _, _ in seen] == [
        "chunk_000001.mp3",
        "chunk_000001.mp3",
        "chunk_000002.mp3",
        "chunk_000002.mp3",
    ]
    assert [payload for _, payload, _ in seen] == [b"first", b"first", b"second", b"second"]
    assert [params["model"] for _, _, params in seen] == [
        "gpt-transcribe",
        "whisper-1",
        "gpt-transcribe",
        "whisper-1",
    ]
    assert all(params["language"] == "en" for _, _, params in seen)
    assert all(params["prompt"] == "meeting context" for _, _, params in seen)
    assert all("timestamp_granularities" not in params for _, _, params in seen[::2])
    assert all(params["response_format"] == "json" for _, _, params in seen[::2])
    assert all(params["response_format"] == "verbose_json" for _, _, params in seen[1::2])
    assert all(params["timestamp_granularities"] == ["word"] for _, _, params in seen[1::2])
    assert result.text == "hello there friend"
    assert result.audio_duration_seconds == 12.0
    assert result.metadata["chunked"] is True
    assert [(word.word, word.start, word.end) for segment in result.segments for word in segment.words] == [
        ("hello", 0.0, 0.5),
        ("there", 0.6, 1.0),
        ("there", 6.0, 6.4),
        ("friend", 6.5, 7.0),
    ]
    usage_by_role = {record["role"]: record for record in result.metadata["provider_usages"]}
    assert usage_by_role["primary"]["call_count"] == 2
    assert usage_by_role["primary"]["usage"]["chunks"] == [
        {"type": "duration", "seconds": 6.0},
        {"type": "duration", "seconds": 6.0},
    ]
    assert usage_by_role["timestamps"]["call_count"] == 2
    assert result.metadata["timestamp_pass"]["status"] == "succeeded"
    assert any(status == "preparing_audio" for status in statuses)
    assert "transcribing_chunk (1/2)" in statuses
    assert "timestamping_chunk (1/2)" in statuses
    assert "transcribing_chunk (2/2) complete" in statuses
    assert heartbeats
    assert workspaces and not workspaces[0].exists()


def test_large_file_chunk_limit_error_cleans_workspace(tmp_path, monkeypatch):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"x" * 32)
    workspaces = []

    def fake_extract(source, workspace, **kwargs):
        workspace.mkdir(parents=True, exist_ok=True)
        workspaces.append(workspace)
        raise ChunkTooLargeError(20, 10, chunk_name="chunk_000001.mp3")

    monkeypatch.setattr(openai_provider, "extract_audio_chunks", fake_extract)
    client, create = _client()

    with pytest.raises(ChunkTooLargeError, match="safe limit"):
        _provider(
            client,
            TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES=10,
            TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES=9,
        ).transcribe(BatchTranscriptionRequest(source=AudioSource.from_local_path(path)))

    create.assert_not_called()
    assert workspaces and not workspaces[0].exists()


class _StatusError(RuntimeError):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_transient_openai_errors_use_bounded_exponential_retries(tmp_path):
    path = tmp_path / "retry.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client(
        side_effect=[_StatusError(500), _StatusError(429), {"text": "recovered"}]
    )
    delays = []
    result = _provider(
        client,
        TRANSCRIPTION_OPENAI_MAX_RETRIES=2,
        TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS=1,
        TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS=8,
        sleep=delays.append,
    ).transcribe(BatchTranscriptionRequest(source=AudioSource.from_local_path(path)))

    assert result.text == "recovered"
    assert create.call_count == 3
    assert delays == [1, 2]


def test_transient_openai_errors_fail_after_retry_budget(tmp_path):
    path = tmp_path / "retry.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client(side_effect=[_StatusError(503)] * 3)

    with pytest.raises(ProviderRequestError, match="3 attempt") as raised:
        _provider(
            client,
            TRANSCRIPTION_OPENAI_MAX_RETRIES=2,
            sleep=lambda _delay: None,
        ).transcribe(BatchTranscriptionRequest(source=AudioSource.from_local_path(path)))

    assert raised.value.status_code == 503
    assert raised.value.attempts == 3
    assert create.call_count == 3


def test_non_transient_openai_error_fails_without_retry(tmp_path):
    path = tmp_path / "invalid.wav"
    path.write_bytes(b"RIFF-test")
    client, create = _client(side_effect=[_StatusError(400), {"text": "must not run"}])

    with pytest.raises(ProviderRequestError) as raised:
        _provider(client, sleep=lambda _delay: None).transcribe(
            BatchTranscriptionRequest(source=AudioSource.from_local_path(path))
        )

    assert raised.value.status_code == 400
    assert raised.value.attempts == 1
    assert create.call_count == 1
