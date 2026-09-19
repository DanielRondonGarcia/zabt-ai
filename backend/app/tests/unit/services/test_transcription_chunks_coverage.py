# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for transcription chunk planning/extraction/merge helpers."""

from enum import Enum
from pathlib import Path
import subprocess
import sys
import types

import pytest

_MODELS_MODULE = types.ModuleType("app.models")


class _TranscriptionType(str, Enum):
    GENERAL = "general"
    MEDICAL = "medical"


_MODELS_MODULE.TranscriptionType = _TranscriptionType
sys.modules.setdefault("app.models", _MODELS_MODULE)

_TRANSCRIPTION_PACKAGE = types.ModuleType("app.services.transcription")
_TRANSCRIPTION_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[3] / "services" / "transcription")
]
sys.modules.setdefault("app.services.transcription", _TRANSCRIPTION_PACKAGE)

from app.services.transcription import chunks as chunks_module
from app.services.transcription.chunks import (
    AudioChunk,
    AudioChunkSet,
    ChunkTranscription,
    extract_audio_chunks,
    merge_chunk_results,
    plan_chunk_ranges,
)
from app.services.transcription.contracts import UsageMetadata
from app.services.transcription.errors import (
    ChunkTooLargeError,
    FFmpegUnavailableError,
    TranscriptionPreparationError,
)
from app.services.transcription.types import ResultSegment, TranscriptionResult, WordTimestamp


def _completed(command, *, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)


def _source_file(tmp_path: Path) -> Path:
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"synthetic-audio")
    return source


def _chunk(index: int, tmp_path: Path, *, offset: float = 0.0, duration: float = 10.0) -> AudioChunk:
    return AudioChunk(
        index=index,
        path=tmp_path / f"chunk_{index:06d}.mp3",
        source_offset_seconds=offset,
        duration_seconds=duration,
        extension="mp3",
        content_type="audio/mpeg",
    )


def _result(
    text: str,
    *,
    start: float = 0.0,
    end: float = 1.0,
    words: list[WordTimestamp] | None = None,
    language: str = "en",
    duration: float | None = 1.0,
    usage: UsageMetadata | None = None,
    metadata: dict | None = None,
    gaps: tuple[str, ...] = (),
    segments: list[ResultSegment] | None = None,
) -> TranscriptionResult:
    if segments is None:
        segments = [
            ResultSegment(
                start=start,
                end=end,
                text=text,
                speaker="SPEAKER_00",
                words=words or [],
            )
        ]
    return TranscriptionResult(
        text=text,
        language=language,
        segments=segments,
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=duration,
        estimated_cost=usage.estimated_cost if usage else None,
        model="gpt-4o-transcribe",
        usage=usage,
        metadata=metadata or {"source": "unit"},
        capability_gaps=gaps,
    )


@pytest.mark.parametrize(
    ("duration", "chunk_duration", "expected"),
    [
        (60, 60, ((0, 60),)),
        (125, 60, ((0, 60), (60, 60), (120, 5))),
        (2.5, 1.0, ((0.0, 1.0), (1.0, 1.0), (2.0, 0.5))),
    ],
)
def test_plan_chunk_ranges_returns_ordered_non_overlapping_ranges(duration, chunk_duration, expected):
    assert plan_chunk_ranges(duration, chunk_duration) == expected


@pytest.mark.parametrize(
    ("duration", "chunk_duration", "max_chunks", "code"),
    [
        (0, 10, 1024, "invalid_media_duration"),
        (-1, 10, 1024, "invalid_media_duration"),
        (10, 0, 1024, "invalid_chunk_duration"),
        (10, -1, 1024, "invalid_chunk_duration"),
        (31, 10, 3, "too_many_chunks"),
    ],
)
def test_plan_chunk_ranges_rejects_invalid_duration_and_chunk_limits(
    duration, chunk_duration, max_chunks, code
):
    with pytest.raises(TranscriptionPreparationError) as exc_info:
        plan_chunk_ranges(duration, chunk_duration, max_chunks=max_chunks)

    assert exc_info.value.code == code


def test_extract_audio_chunks_builds_ffprobe_and_ffmpeg_commands_with_deterministic_outputs(
    tmp_path, monkeypatch
):
    source = _source_file(tmp_path)
    workspace = tmp_path / "chunks"
    calls = []
    monkeypatch.setattr(chunks_module.shutil, "which", lambda name: name)

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "check": True,
            "timeout": 12.5,
        }
        if command[0] == "ffprobe":
            return _completed(command, stdout="125.0\n")
        Path(command[-1]).write_bytes(f"audio-{Path(command[-1]).name}".encode())
        return _completed(command)

    chunk_set = extract_audio_chunks(
        source,
        workspace,
        chunk_duration_seconds=60,
        max_chunk_bytes=64,
        max_chunks=3,
        command_runner=runner,
        command_timeout_seconds=12.5,
    )

    assert chunk_set.source_duration_seconds == 125.0
    assert chunk_set.workspace == workspace
    assert [chunk.index for chunk in chunk_set.chunks] == [1, 2, 3]
    assert [chunk.path.name for chunk in chunk_set.chunks] == [
        "chunk_000001.mp3",
        "chunk_000002.mp3",
        "chunk_000003.mp3",
    ]
    assert [chunk.source_offset_seconds for chunk in chunk_set.chunks] == [0, 60, 120]
    assert [chunk.duration_seconds for chunk in chunk_set.chunks] == [60, 60, 5]
    assert {(chunk.extension, chunk.content_type) for chunk in chunk_set.chunks} == {("mp3", "audio/mpeg")}

    assert calls[0][0] == [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    first_ffmpeg = calls[1][0]
    assert first_ffmpeg == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "0.000",
        "-i",
        str(source),
        "-t",
        "60.000",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "24k",
        "-map_metadata",
        "-1",
        str(workspace / "chunk_000001.mp3"),
    ]
    assert calls[3][0][calls[3][0].index("-ss") + 1] == "120.000"
    assert calls[3][0][calls[3][0].index("-t") + 1] == "5.000"

    chunk_set.cleanup()
    assert not workspace.exists()


def test_audio_chunk_set_cleanup_is_noop_for_non_default_owner(tmp_path):
    workspace = tmp_path / "owned-elsewhere"
    workspace.mkdir()
    chunk_set = AudioChunkSet(
        source_duration_seconds=1.0,
        chunks=(),
        workspace=workspace,
        cleanup_owner="caller-owned",
    )

    chunk_set.cleanup()

    assert workspace.exists()


@pytest.mark.parametrize(
    ("ffprobe_stdout", "code"),
    [
        ("", "invalid_media_duration"),
        ("not-a-number", "invalid_media_duration"),
        ("0", "invalid_media_duration"),
        ("-1", "invalid_media_duration"),
        ("nan", "invalid_media_duration"),
    ],
)
def test_extract_audio_chunks_rejects_unusable_probe_durations(
    tmp_path, monkeypatch, ffprobe_stdout, code
):
    source = _source_file(tmp_path)
    monkeypatch.setattr(chunks_module.shutil, "which", lambda name: name)

    def runner(command, **kwargs):
        assert command[0] == "ffprobe"
        return _completed(command, stdout=ffprobe_stdout)

    with pytest.raises(TranscriptionPreparationError) as exc_info:
        extract_audio_chunks(
            source,
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
            command_runner=runner,
        )

    assert exc_info.value.code == code


def test_extract_audio_chunks_validates_source_size_toolchain_outputs_and_large_chunks(
    tmp_path, monkeypatch
):
    source = _source_file(tmp_path)

    with pytest.raises(TranscriptionPreparationError) as missing_source:
        extract_audio_chunks(
            tmp_path / "missing.mp3",
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
        )
    assert missing_source.value.code == "media_source_missing"

    with pytest.raises(TranscriptionPreparationError) as invalid_size:
        extract_audio_chunks(
            source,
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=0,
        )
    assert invalid_size.value.code == "invalid_chunk_size"

    monkeypatch.setattr(chunks_module.shutil, "which", lambda _name: None)
    with pytest.raises(FFmpegUnavailableError) as unavailable:
        extract_audio_chunks(
            source,
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
        )
    assert unavailable.value.code == "ffmpeg_unavailable"

    monkeypatch.setattr(chunks_module.shutil, "which", lambda name: name)

    def missing_output_runner(command, **kwargs):
        if command[0] == "ffprobe":
            return _completed(command, stdout="10")
        return _completed(command)

    with pytest.raises(TranscriptionPreparationError) as missing_output:
        extract_audio_chunks(
            source,
            tmp_path / "missing-output",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
            command_runner=missing_output_runner,
        )
    assert missing_output.value.code == "chunk_output_missing"

    def empty_output_runner(command, **kwargs):
        if command[0] == "ffprobe":
            return _completed(command, stdout="10")
        Path(command[-1]).write_bytes(b"")
        return _completed(command)

    with pytest.raises(TranscriptionPreparationError) as empty_output:
        extract_audio_chunks(
            source,
            tmp_path / "empty-output",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
            command_runner=empty_output_runner,
        )
    assert empty_output.value.code == "empty_chunk"

    def too_large_runner(command, **kwargs):
        if command[0] == "ffprobe":
            return _completed(command, stdout="10")
        Path(command[-1]).write_bytes(b"0123456789")
        return _completed(command)

    with pytest.raises(ChunkTooLargeError) as too_large:
        extract_audio_chunks(
            source,
            tmp_path / "too-large",
            chunk_duration_seconds=10,
            max_chunk_bytes=9,
            command_runner=too_large_runner,
        )
    assert too_large.value.code == "chunk_too_large"
    assert too_large.value.chunk_name == "chunk_000001.mp3"
    assert too_large.value.size_bytes == 10
    assert too_large.value.limit_bytes == 9


@pytest.mark.parametrize(
    ("raised", "expected_type", "expected_code"),
    [
        (FileNotFoundError("missing"), FFmpegUnavailableError, "ffmpeg_unavailable"),
        (
            subprocess.CalledProcessError(1, ["ffmpeg"], stderr="bad codec"),
            TranscriptionPreparationError,
            "ffmpeg_failed",
        ),
        (
            subprocess.TimeoutExpired(["ffmpeg"], timeout=1),
            TranscriptionPreparationError,
            "ffmpeg_timeout",
        ),
    ],
)
def test_extract_audio_chunks_maps_runner_failures_to_typed_preparation_errors(
    tmp_path, monkeypatch, raised, expected_type, expected_code
):
    source = _source_file(tmp_path)
    monkeypatch.setattr(chunks_module.shutil, "which", lambda name: name)

    def runner(command, **kwargs):
        raise raised

    with pytest.raises(expected_type) as exc_info:
        extract_audio_chunks(
            source,
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
            command_runner=runner,
        )

    assert exc_info.value.code == expected_code


def test_merge_chunk_results_offsets_deduplicates_orders_and_aggregates_usage(tmp_path):
    first_usage = UsageMetadata(
        input_units=10,
        output_units=1,
        total_units=11,
        unit="tokens",
        estimated_cost=0.01,
        cost_currency="USD",
        cost_unit="tokens",
        raw={"type": "tokens", "input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    )
    second_usage = UsageMetadata(
        input_units=5,
        output_units=2,
        total_units=7,
        unit="tokens",
        estimated_cost=0.02,
        cost_currency="USD",
        cost_unit="tokens",
        raw={"type": "tokens", "input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )
    first = _result(
        "hello world",
        start=8,
        end=10,
        words=[WordTimestamp("hello", 8, 8.5), WordTimestamp("world", 9.5, 10)],
        duration=10,
        usage=first_usage,
        gaps=("duration", "speakers"),
    )
    second = _result(
        "world again",
        start=0,
        end=2,
        words=[WordTimestamp("world", 0, 0.5), WordTimestamp("again", 1, 1.5)],
        duration=2,
        usage=second_usage,
        gaps=("speakers",),
    )

    merged = merge_chunk_results(
        [
            ChunkTranscription(_chunk(2, tmp_path, offset=10, duration=2), second),
            ChunkTranscription(_chunk(1, tmp_path, offset=0, duration=10), first),
        ],
        audio_duration_seconds=12,
    )

    assert merged.text == "hello world again"
    assert [(segment.start, segment.end, segment.text) for segment in merged.segments] == [
        (8, 10, "hello world"),
        (10, 12, "again"),
    ]
    assert [(word.word, word.start, word.end) for word in merged.segments[1].words] == [
        ("again", 11, 11.5)
    ]
    assert merged.audio_duration_seconds == 12
    assert merged.capability_gaps == ("speakers",)
    assert merged.usage.input_units == 15
    assert merged.usage.output_units == 3
    assert merged.usage.total_units == 18
    assert merged.usage.estimated_cost == pytest.approx(0.03)
    assert merged.usage.cost_currency == "USD"
    assert merged.metadata["chunked"] is True
    assert merged.metadata["chunk_count"] == 2
    assert merged.metadata["chunk_offsets_seconds"] == [0, 10]
    assert merged.metadata["usage"]["units"] == ["tokens"]


def test_merge_chunk_results_creates_coarse_segments_and_sums_chunk_durations(tmp_path):
    first = _result("first chunk", segments=[], duration=4, gaps=("segments", "duration"))
    second = _result("second chunk", segments=[], duration=6, gaps=("segments", "duration"))

    merged = merge_chunk_results(
        [
            ChunkTranscription(_chunk(1, tmp_path, offset=0, duration=4), first),
            ChunkTranscription(_chunk(2, tmp_path, offset=4, duration=6), second),
        ]
    )

    assert merged.text == "first chunk second chunk"
    assert [(segment.start, segment.end, segment.text, segment.speaker) for segment in merged.segments] == [
        (0, 4, "first chunk", "SPEAKER_UNKNOWN"),
        (4, 10, "second chunk", "SPEAKER_UNKNOWN"),
    ]
    assert merged.audio_duration_seconds == 10
    assert merged.capability_gaps == ("segments",)


def test_merge_chunk_results_exposes_provider_usage_and_timestamp_error_metadata(tmp_path):
    first_metadata = {
        "provider_usages": {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "transcription",
            "status": "succeeded",
            "usage": {"type": "duration", "seconds": 10},
        },
        "timestamp_pass": {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "timestamps",
            "status": "succeeded",
            "usage": {"type": "tokens", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        },
    }
    second_metadata = {
        "provider_usages": [
            {
                "provider": "openai",
                "model": "gpt-4o-mini-transcribe",
                "role": "transcription",
                "status": "failed",
                "usage": {"type": "duration", "seconds": 5},
                "error": {"code": "provider_timeout", "message": "timeout"},
            },
            "ignored-non-mapping",
        ],
        "timestamp_pass": {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "timestamps",
            "status": "failed",
            "usage": {"type": "tokens", "input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
            "error": {"code": "timestamp_failed"},
        },
    }

    merged = merge_chunk_results(
        [
            ChunkTranscription(_chunk(1, tmp_path, offset=0, duration=10), _result("alpha", metadata=first_metadata)),
            ChunkTranscription(_chunk(2, tmp_path, offset=10, duration=5), _result("beta", metadata=second_metadata)),
        ]
    )

    assert merged.metadata["provider_usage_calls"] == [
        {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "transcription",
            "status": "succeeded",
            "usage": {"type": "duration", "seconds": 10},
            "chunk_index": 1,
        },
        {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "transcription",
            "status": "failed",
            "usage": {"type": "duration", "seconds": 5},
            "error": {"code": "provider_timeout", "message": "timeout"},
            "chunk_index": 2,
        },
    ]
    assert merged.metadata["provider_usages"] == [
        {
            "provider": "openai",
            "model": "gpt-4o-mini-transcribe",
            "role": "transcription",
            "status": "partial",
            "usage": {
                "chunks": [
                    {"type": "duration", "seconds": 10},
                    {"type": "duration", "seconds": 5},
                ],
                "units": ["seconds"],
            },
            "call_count": 2,
            "chunks": [1, 2],
            "errors": [{"code": "provider_timeout", "message": "timeout"}],
        }
    ]
    assert merged.metadata["timestamp_pass"] == {
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "role": "timestamps",
        "status": "partial",
        "usage": {
            "chunks": [
                {"type": "tokens", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                {"type": "tokens", "input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
            ],
            "units": ["tokens"],
        },
        "chunk_count": 2,
        "errors": [{"code": "timestamp_failed"}],
    }


def test_merge_chunk_results_handles_empty_mixed_usage_and_unknown_provider_metadata(tmp_path):
    with pytest.raises(TranscriptionPreparationError) as empty:
        merge_chunk_results([])
    assert empty.value.code == "empty_chunk_results"

    token_usage = UsageMetadata(total_units=2, unit="tokens", raw={"type": "tokens", "total_tokens": 2})
    duration_usage = UsageMetadata(total_units=3, unit="seconds", raw={"type": "duration", "seconds": 3})
    mixed = merge_chunk_results(
        [
            ChunkTranscription(
                _chunk(1, tmp_path),
                _result(
                    "alpha",
                    language="unknown",
                    usage=token_usage,
                    metadata={"provider_usages": "ignored"},
                    gaps=("duration",),
                ),
            ),
            ChunkTranscription(
                _chunk(2, tmp_path, offset=10),
                _result(
                    "beta",
                    language="es",
                    usage=duration_usage,
                    metadata={
                        "provider_usages": [
                            {"status": "succeeded", "usage": {"opaque": True}},
                            {"status": "queued", "usage": {"opaque": False}},
                        ]
                    },
                    gaps=("duration",),
                ),
            ),
        ]
    )

    assert mixed.language == "es"
    assert mixed.usage.unit == "mixed"
    assert mixed.usage.raw["units"] == ["seconds", "tokens"]
    assert mixed.capability_gaps == ()
    assert mixed.metadata["provider_usages"] == [
        {
            "provider": "unknown",
            "model": "unknown",
            "role": "unknown",
            "status": "partial",
            "usage": {"chunks": [{"opaque": True}, {"opaque": False}], "units": ["None"]},
            "call_count": 2,
            "chunks": [2, 2],
        }
    ]
