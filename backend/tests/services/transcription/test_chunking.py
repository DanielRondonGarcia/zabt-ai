# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Deterministic planning, extraction, and merge coverage for long media."""

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from app.services.transcription import chunks
from app.services.transcription.chunks import (
    AudioChunk,
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


def test_plan_chunk_ranges_is_ordered_and_bounded():
    assert plan_chunk_ranges(125, 60) == (
        (0, 60),
        (60, 60),
        (120, 5),
    )

    with pytest.raises(TranscriptionPreparationError, match="exceeding the configured limit"):
        plan_chunk_ranges(100, 10, max_chunks=9)


def test_extract_audio_chunks_uses_ffprobe_and_deterministic_mono_mp3_commands(tmp_path, monkeypatch):
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"synthetic-media")
    workspace = tmp_path / "chunks"
    commands = []

    monkeypatch.setattr(chunks.shutil, "which", lambda name: name)

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="125\n", stderr="")
        output = Path(command[-1])
        output.write_bytes(f"chunk-{output.name}".encode())
        return CompletedProcess(command, 0, stdout="", stderr="")

    chunk_set = extract_audio_chunks(
        source,
        workspace,
        chunk_duration_seconds=60,
        max_chunk_bytes=100,
        command_runner=runner,
    )

    assert [chunk.index for chunk in chunk_set.chunks] == [1, 2, 3]
    assert [chunk.source_offset_seconds for chunk in chunk_set.chunks] == [0, 60, 120]
    assert [chunk.extension for chunk in chunk_set.chunks] == ["mp3"] * 3
    assert [chunk.content_type for chunk in chunk_set.chunks] == ["audio/mpeg"] * 3
    assert [chunk.path.name for chunk in chunk_set.chunks] == [
        "chunk_000001.mp3",
        "chunk_000002.mp3",
        "chunk_000003.mp3",
    ]
    assert commands[0][0][0] == "ffprobe"
    ffmpeg_command = commands[1][0]
    assert ffmpeg_command[ffmpeg_command.index("-ac") + 1] == "1"
    assert ffmpeg_command[ffmpeg_command.index("-ar") + 1] == "16000"
    assert ffmpeg_command[ffmpeg_command.index("-b:a") + 1] == "24k"
    assert all(chunk.path.stat().st_size <= 100 for chunk in chunk_set.chunks)
    chunk_set.cleanup()
    assert not workspace.exists()


def test_extract_audio_chunks_rejects_output_over_safe_limit(tmp_path, monkeypatch):
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"synthetic-media")
    workspace = tmp_path / "chunks"
    monkeypatch.setattr(chunks.shutil, "which", lambda name: name)

    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="10\n", stderr="")
        Path(command[-1]).write_bytes(b"too-large")
        return CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ChunkTooLargeError, match="safe limit"):
        extract_audio_chunks(
            source,
            workspace,
            chunk_duration_seconds=10,
            max_chunk_bytes=4,
            command_runner=runner,
        )

    assert (workspace / "chunk_000001.mp3").exists()
    chunks.shutil.rmtree(workspace)


def test_extract_audio_chunks_reports_missing_ffmpeg_as_typed_error(tmp_path, monkeypatch):
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"synthetic-media")
    monkeypatch.setattr(chunks.shutil, "which", lambda _name: None)

    with pytest.raises(FFmpegUnavailableError, match="FFmpeg and ffprobe are required"):
        extract_audio_chunks(
            source,
            tmp_path / "chunks",
            chunk_duration_seconds=10,
            max_chunk_bytes=100,
        )


def _result(text, *, start, end, words, usage_seconds):
    return TranscriptionResult(
        text=text,
        language="en",
        segments=[
            ResultSegment(
                start=start,
                end=end,
                text=text,
                speaker="SPEAKER_UNKNOWN",
                words=words,
            )
        ],
        provider_name="openai-file",
        recognition_method="openai-audio-transcriptions",
        audio_duration_seconds=usage_seconds,
        estimated_cost=None,
        model="gpt-transcribe",
        usage=UsageMetadata(
            input_units=usage_seconds,
            total_units=usage_seconds,
            unit="seconds",
            raw={"type": "duration", "seconds": usage_seconds},
        ),
        metadata={"provider": "openai-file"},
        capability_gaps=("speakers", "duration"),
    )


def test_merge_chunk_results_offsets_timing_deduplicates_boundary_and_aggregates_usage(tmp_path):
    first_chunk = AudioChunk(1, tmp_path / "chunk_000001.mp3", 0, 10, "mp3", "audio/mpeg")
    second_chunk = AudioChunk(2, tmp_path / "chunk_000002.mp3", 10, 2, "mp3", "audio/mpeg")
    first = _result(
        "hello world",
        start=8,
        end=10,
        words=[
            WordTimestamp("hello", 8.0, 8.5),
            WordTimestamp("world", 9.5, 10.0),
        ],
        usage_seconds=10,
    )
    second = _result(
        "world again",
        start=0,
        end=2,
        words=[
            WordTimestamp("world", 0.0, 0.5),
            WordTimestamp("again", 1.0, 1.5),
        ],
        usage_seconds=2,
    )

    merged = merge_chunk_results(
        [
            ChunkTranscription(second_chunk, second),
            ChunkTranscription(first_chunk, first),
        ],
        audio_duration_seconds=12,
    )

    assert merged.text == "hello world again"
    assert [(segment.start, segment.end) for segment in merged.segments] == [(8, 10), (10, 12)]
    assert [segment.text for segment in merged.segments] == ["hello world", "again"]
    assert [(word.word, word.start, word.end) for word in merged.segments[0].words] == [
        ("hello", 8.0, 8.5),
        ("world", 9.5, 10.0),
    ]
    assert [(word.word, word.start, word.end) for word in merged.segments[1].words] == [
        ("again", 11.0, 11.5),
    ]
    assert all(segment.speaker == "SPEAKER_UNKNOWN" for segment in merged.segments)
    assert merged.usage.unit == "seconds"
    assert merged.usage.total_units == 12
    assert merged.usage.raw["chunks"] == [
        {"type": "duration", "seconds": 10},
        {"type": "duration", "seconds": 2},
    ]
    assert merged.capability_gaps == ("speakers",)


def test_merge_chunk_results_creates_coarse_segments_for_text_only_chunks(tmp_path):
    first_chunk = AudioChunk(1, tmp_path / "chunk_000001.mp3", 0, 5, "mp3", "audio/mpeg")
    second_chunk = AudioChunk(2, tmp_path / "chunk_000002.mp3", 5, 3, "mp3", "audio/mpeg")

    def text_only_result(text):
        return TranscriptionResult(
            text=text,
            language="en",
            segments=[],
            provider_name="openai-file",
            recognition_method="openai-audio-transcriptions",
            audio_duration_seconds=None,
            estimated_cost=None,
            model="gpt-transcribe",
            capability_gaps=("segments", "duration"),
        )

    merged = merge_chunk_results(
        [
            ChunkTranscription(first_chunk, text_only_result("first chunk")),
            ChunkTranscription(second_chunk, text_only_result("second chunk")),
        ],
        audio_duration_seconds=8,
    )

    assert [(segment.start, segment.end) for segment in merged.segments] == [
        (0, 5),
        (5, 8),
    ]
    assert [segment.text for segment in merged.segments] == ["first chunk", "second chunk"]
    assert all(segment.speaker == "SPEAKER_UNKNOWN" for segment in merged.segments)
    assert all(segment.words == [] for segment in merged.segments)
