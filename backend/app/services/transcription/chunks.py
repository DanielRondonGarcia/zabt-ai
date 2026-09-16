# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider-neutral planning, extraction, and merging for long audio inputs."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.transcription.contracts import UsageMetadata
from app.services.transcription.errors import (
    ChunkTooLargeError,
    FFmpegUnavailableError,
    TranscriptionPreparationError,
)
from app.services.transcription.types import (
    ResultSegment,
    TranscriptionResult,
    WordTimestamp,
    coarse_text_segment,
)


MP3_EXTENSION = "mp3"
MP3_CONTENT_TYPE = "audio/mpeg"
DEFAULT_CLEANUP_OWNER = "transcription-task"
_BOUNDARY_TEXT_WINDOW = 20
_BOUNDARY_TIME_TOLERANCE_SECONDS = 2.0

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class AudioChunk:
    """One deterministic local chunk and its source-media coordinates."""

    index: int
    path: Path
    source_offset_seconds: float
    duration_seconds: float
    extension: str
    content_type: str
    cleanup_owner: str = DEFAULT_CLEANUP_OWNER


@dataclass
class AudioChunkSet:
    """A temporary chunk workspace whose owner is responsible for cleanup."""

    source_duration_seconds: float
    chunks: tuple[AudioChunk, ...]
    workspace: Path
    cleanup_owner: str = DEFAULT_CLEANUP_OWNER

    def cleanup(self) -> None:
        if self.cleanup_owner != DEFAULT_CLEANUP_OWNER:
            return
        shutil.rmtree(self.workspace, ignore_errors=True)


@dataclass(frozen=True)
class ChunkTranscription:
    chunk: AudioChunk
    result: TranscriptionResult


def plan_chunk_ranges(
    duration_seconds: float,
    chunk_duration_seconds: float,
    *,
    max_chunks: int = 1024,
) -> tuple[tuple[float, float], ...]:
    """Return ordered, non-overlapping ``(offset, duration)`` ranges."""

    if duration_seconds <= 0:
        raise TranscriptionPreparationError(
            "Media duration must be greater than zero before chunking.",
            code="invalid_media_duration",
        )
    if chunk_duration_seconds <= 0:
        raise TranscriptionPreparationError(
            "TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS must be greater than zero.",
            code="invalid_chunk_duration",
        )
    count = math.ceil(duration_seconds / chunk_duration_seconds)
    if count > max_chunks:
        raise TranscriptionPreparationError(
            f"Media requires {count} chunks, exceeding the configured limit of {max_chunks}.",
            code="too_many_chunks",
        )
    return tuple(
        (
            index * chunk_duration_seconds,
            min(chunk_duration_seconds, duration_seconds - index * chunk_duration_seconds),
        )
        for index in range(count)
    )


def extract_audio_chunks(
    source_path: str | Path,
    workspace: str | Path,
    *,
    chunk_duration_seconds: float,
    max_chunk_bytes: int,
    max_chunks: int = 1024,
    command_runner: CommandRunner | None = None,
    ffmpeg_binary: str = "ffmpeg",
    ffprobe_binary: str = "ffprobe",
    command_timeout_seconds: float = 900.0,
) -> AudioChunkSet:
    """Probe media and extract deterministic mono 16 kHz MP3 chunks.

    The planner never slices the original container bytes. Every output is
    validated after FFmpeg writes it and remains owned by the returned
    temporary workspace until ``AudioChunkSet.cleanup()`` is called.
    """

    source = Path(source_path)
    root = Path(workspace)
    if not source.is_file():
        raise TranscriptionPreparationError(
            f"Media source does not exist: {source}",
            code="media_source_missing",
        )
    if max_chunk_bytes <= 0:
        raise TranscriptionPreparationError(
            "The configured transcription chunk byte limit must be greater than zero.",
            code="invalid_chunk_size",
        )

    ffmpeg_path = shutil.which(ffmpeg_binary)
    ffprobe_path = shutil.which(ffprobe_binary)
    if not ffmpeg_path or not ffprobe_path:
        raise FFmpegUnavailableError(
            "FFmpeg and ffprobe are required to transcribe media above the safe "
            "OpenAI single-request threshold. Install the ffmpeg package in the "
            "worker image or use the existing GPU/RunPod provider explicitly."
        )

    runner = command_runner or subprocess.run
    root.mkdir(parents=True, exist_ok=True)
    duration_seconds = _probe_duration(
        ffprobe_path,
        source,
        runner=runner,
        timeout_seconds=command_timeout_seconds,
    )
    ranges = plan_chunk_ranges(
        duration_seconds,
        chunk_duration_seconds,
        max_chunks=max_chunks,
    )

    chunks: list[AudioChunk] = []
    for index, (offset_seconds, duration) in enumerate(ranges, start=1):
        output_path = root / f"chunk_{index:06d}.{MP3_EXTENSION}"
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{offset_seconds:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
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
            str(output_path),
        ]
        _run_command(
            command,
            runner=runner,
            timeout_seconds=command_timeout_seconds,
            action="extract audio chunk",
        )
        if not output_path.is_file():
            raise TranscriptionPreparationError(
                f"FFmpeg did not produce expected chunk {output_path.name!r}.",
                code="chunk_output_missing",
            )
        size_bytes = output_path.stat().st_size
        if size_bytes <= 0:
            raise TranscriptionPreparationError(
                f"FFmpeg produced an empty chunk {output_path.name!r}.",
                code="empty_chunk",
            )
        if size_bytes > max_chunk_bytes:
            raise ChunkTooLargeError(
                size_bytes,
                max_chunk_bytes,
                chunk_name=output_path.name,
            )
        chunks.append(
            AudioChunk(
                index=index,
                path=output_path,
                source_offset_seconds=offset_seconds,
                duration_seconds=duration,
                extension=MP3_EXTENSION,
                content_type=MP3_CONTENT_TYPE,
            )
        )

    return AudioChunkSet(
        source_duration_seconds=duration_seconds,
        chunks=tuple(chunks),
        workspace=root,
    )


def merge_chunk_results(
    chunk_results: Sequence[ChunkTranscription],
    *,
    audio_duration_seconds: float | None = None,
) -> TranscriptionResult:
    """Merge ordered chunk results without inventing timing or speaker data."""

    if not chunk_results:
        raise TranscriptionPreparationError(
            "No chunk transcription results were produced.",
            code="empty_chunk_results",
        )

    ordered = sorted(chunk_results, key=lambda item: item.chunk.index)
    first = ordered[0].result
    text = _merge_text_parts(item.result.text for item in ordered)
    segments: list[ResultSegment] = []
    words: list[WordTimestamp] = []
    for item in ordered:
        offset = item.chunk.source_offset_seconds
        source_segments: Sequence[ResultSegment] = item.result.segments
        if not source_segments:
            coarse_segment = coarse_text_segment(
                item.result.text,
                start=0.0,
                duration=item.chunk.duration_seconds,
            )
            source_segments = (coarse_segment,) if coarse_segment is not None else ()

        for segment in source_segments:
            shifted_words = [
                _shift_word(word, offset)
                for word in segment.words
            ]
            unique_words = [word for word in shifted_words if not _is_duplicate_word(words, word)]
            shifted_start = segment.start + offset
            shifted_end = segment.end + offset
            segment_text = segment.text
            if segments and shifted_start <= segments[-1].end + _BOUNDARY_TIME_TOLERANCE_SECONDS:
                segment_text = _remove_boundary_text(segments[-1].text, segment_text)
                if segment.text.strip() and not segment_text:
                    continue
            shifted = ResultSegment(
                start=shifted_start,
                end=shifted_end,
                text=segment_text,
                speaker=segment.speaker,
                words=unique_words,
            )
            if _is_duplicate_segment(segments, shifted):
                continue
            words.extend(unique_words)
            segments.append(shifted)

    if audio_duration_seconds is None:
        durations = [item.result.audio_duration_seconds for item in ordered]
        if all(duration is not None for duration in durations):
            audio_duration_seconds = sum(duration for duration in durations if duration is not None)

    usage = _aggregate_usage(item.result.usage for item in ordered)
    provider_usage_calls = _collect_provider_usage_calls(ordered)
    gaps: list[str] = []
    for item in ordered:
        for gap in item.result.capability_gaps:
            if gap not in gaps:
                gaps.append(gap)
    if audio_duration_seconds is not None and "duration" in gaps:
        gaps.remove("duration")

    language = next(
        (
            item.result.language
            for item in ordered
            if item.result.language and item.result.language != "unknown"
        ),
        first.language,
    )
    metadata = dict(first.metadata)
    metadata.update(
        {
            "chunked": True,
            "chunk_count": len(ordered),
            "chunk_offsets_seconds": [item.chunk.source_offset_seconds for item in ordered],
            "usage": usage.raw if usage else None,
            "provider_usages": _aggregate_provider_usage_records(provider_usage_calls),
            "provider_usage_calls": provider_usage_calls,
        }
    )
    timestamp_pass = _aggregate_timestamp_pass(ordered)
    if timestamp_pass is not None:
        metadata["timestamp_pass"] = timestamp_pass
    return TranscriptionResult(
        text=text,
        language=language,
        segments=segments,
        provider_name=first.provider_name,
        recognition_method=first.recognition_method,
        audio_duration_seconds=audio_duration_seconds,
        estimated_cost=usage.estimated_cost if usage else None,
        model=first.model,
        usage=usage,
        metadata=metadata,
        capability_gaps=tuple(gaps),
    )


def _probe_duration(
    ffprobe_path: str,
    source: Path,
    *,
    runner: CommandRunner,
    timeout_seconds: float,
) -> float:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    completed = _run_command(
        command,
        runner=runner,
        timeout_seconds=timeout_seconds,
        action="probe media duration",
    )
    raw_duration = str(getattr(completed, "stdout", "") or "").strip()
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise TranscriptionPreparationError(
            "ffprobe returned no usable media duration.",
            code="invalid_media_duration",
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise TranscriptionPreparationError(
            "ffprobe returned an invalid media duration.",
            code="invalid_media_duration",
        )
    return duration


def _run_command(
    command: list[str],
    *,
    runner: CommandRunner,
    timeout_seconds: float,
    action: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise FFmpegUnavailableError(
            "The FFmpeg executable could not be started. Install ffmpeg in the worker image."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = str(getattr(exc, "stderr", "") or "").strip()
        bounded_detail = f": {detail[:240]}" if detail else ""
        raise TranscriptionPreparationError(
            f"FFmpeg failed to {action}{bounded_detail}",
            code="ffmpeg_failed",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionPreparationError(
            f"FFmpeg timed out while attempting to {action}.",
            code="ffmpeg_timeout",
        ) from exc


def _shift_word(word: WordTimestamp, offset: float) -> WordTimestamp:
    return WordTimestamp(
        word=word.word,
        start=word.start + offset,
        end=word.end + offset,
        speaker_label=word.speaker_label,
        confidence=word.confidence,
    )


def _is_duplicate_word(previous: Sequence[WordTimestamp], candidate: WordTimestamp) -> bool:
    if not previous:
        return False
    prior = previous[-1]
    return (
        _token_signature(prior.word) == _token_signature(candidate.word)
        and candidate.start <= prior.end + _BOUNDARY_TIME_TOLERANCE_SECONDS
    )


def _is_duplicate_segment(previous: Sequence[ResultSegment], candidate: ResultSegment) -> bool:
    if not previous:
        return False
    prior = previous[-1]
    return (
        _token_sequence_signature(prior.text) == _token_sequence_signature(candidate.text)
        and candidate.start <= prior.end + _BOUNDARY_TIME_TOLERANCE_SECONDS
    )


def _merge_text_parts(parts: Sequence[str] | Any) -> str:
    merged = ""
    for raw_part in parts:
        part = str(raw_part or "").strip()
        if not part:
            continue
        if not merged:
            merged = part
            continue
        previous_tokens = merged.split()
        current_tokens = part.split()
        overlap = _longest_boundary_overlap(previous_tokens, current_tokens)
        remainder = current_tokens[overlap:]
        if remainder:
            merged = f"{merged.rstrip()} {' '.join(remainder)}"
    return merged


def _remove_boundary_text(previous: str, current: str) -> str:
    previous_tokens = previous.split()
    current_tokens = current.split()
    overlap = _longest_boundary_overlap(previous_tokens, current_tokens)
    return " ".join(current_tokens[overlap:])


def _longest_boundary_overlap(previous: Sequence[str], current: Sequence[str]) -> int:
    max_overlap = min(_BOUNDARY_TEXT_WINDOW, len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if [
            _token_signature(token)
            for token in previous[-size:]
        ] == [
            _token_signature(token)
            for token in current[:size]
        ]:
            return size
    return 0


def _token_sequence_signature(value: str) -> tuple[str, ...]:
    return tuple(_token_signature(token) for token in value.split() if _token_signature(token))


def _token_signature(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def _aggregate_usage(values: Sequence[UsageMetadata | None] | Any) -> UsageMetadata | None:
    usages = [value for value in values if isinstance(value, UsageMetadata)]
    if not usages:
        return None
    raw_chunks = [dict(usage.raw) for usage in usages]
    units = {usage.unit for usage in usages}
    raw: Mapping[str, Any] = {"chunks": raw_chunks, "units": sorted(str(unit) for unit in units)}
    if len(units) != 1:
        return UsageMetadata(unit="mixed", raw=raw)

    unit = next(iter(units))
    return UsageMetadata(
        input_units=_sum_numbers(usage.input_units for usage in usages),
        output_units=_sum_numbers(usage.output_units for usage in usages),
        total_units=_sum_numbers(usage.total_units for usage in usages),
        unit=unit,
        estimated_cost=_sum_numbers(usage.estimated_cost for usage in usages),
        cost_currency=_first_equal(usage.cost_currency for usage in usages),
        cost_unit=_first_equal(usage.cost_unit for usage in usages),
        raw=raw,
    )


def _collect_provider_usage_calls(
    chunk_results: Sequence[ChunkTranscription],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in chunk_results:
        value = item.result.metadata.get("provider_usages", ())
        if isinstance(value, Mapping):
            value = (value,)
        if not isinstance(value, (list, tuple)):
            continue
        for record in value:
            if not isinstance(record, Mapping):
                continue
            call = dict(record)
            call["chunk_index"] = item.chunk.index
            calls.append(call)
    return calls


def _usage_from_raw(raw: Any) -> UsageMetadata | None:
    if not isinstance(raw, Mapping):
        return None
    if "chunks" in raw and isinstance(raw.get("chunks"), (list, tuple)):
        nested = [_usage_from_raw(item) for item in raw["chunks"]]
        return _aggregate_usage(nested)
    usage_type = raw.get("type")
    if usage_type == "duration":
        seconds = raw.get("seconds")
        return UsageMetadata(
            input_units=seconds,
            total_units=seconds,
            unit="seconds",
            raw=dict(raw),
        )
    if usage_type == "tokens":
        return UsageMetadata(
            input_units=raw.get("input_tokens"),
            output_units=raw.get("output_tokens"),
            total_units=raw.get("total_tokens"),
            unit="tokens",
            raw=dict(raw),
        )
    return UsageMetadata(raw=dict(raw))


def _aggregate_provider_usage_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for record in records:
        key = (
            str(record.get("provider") or "unknown"),
            str(record.get("model") or "unknown"),
            str(record.get("role") or "unknown"),
        )
        grouped.setdefault(key, []).append(record)

    aggregated: list[dict[str, Any]] = []
    for (provider, model, role), values in grouped.items():
        usages = [_usage_from_raw(value.get("usage")) for value in values]
        total = _aggregate_usage(usages)
        statuses = {str(value.get("status") or "succeeded") for value in values}
        if len(statuses) == 1:
            status = next(iter(statuses))
        elif "failed" in statuses:
            status = "partial"
        else:
            status = "partial"
        result: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "role": role,
            "status": status,
            "usage": total.raw if total else None,
            "call_count": len(values),
            "chunks": [value.get("chunk_index") for value in values],
        }
        errors = [dict(value["error"]) for value in values if isinstance(value.get("error"), Mapping)]
        if errors:
            result["errors"] = errors
        aggregated.append(result)
    return aggregated


def _aggregate_timestamp_pass(
    chunk_results: Sequence[ChunkTranscription],
) -> dict[str, Any] | None:
    passes: list[Mapping[str, Any]] = []
    for item in chunk_results:
        value = item.result.metadata.get("timestamp_pass")
        if isinstance(value, Mapping):
            entry = dict(value)
            entry["chunk_index"] = item.chunk.index
            passes.append(entry)
    if not passes:
        return None

    statuses = {str(value.get("status") or "unknown") for value in passes}
    if statuses == {"succeeded"}:
        status = "succeeded"
    elif statuses == {"failed"}:
        status = "failed"
    else:
        status = "partial"
    usages = [_usage_from_raw(value.get("usage")) for value in passes]
    total = _aggregate_usage(usages)
    first = dict(passes[0])
    first.update(
        {
            "status": status,
            "usage": total.raw if total else None,
            "chunk_count": len(passes),
        }
    )
    errors = [dict(value["error"]) for value in passes if isinstance(value.get("error"), Mapping)]
    if errors:
        first["errors"] = errors
    first.pop("chunk_index", None)
    return first


def _sum_numbers(values: Sequence[int | float | None] | Any) -> int | float | None:
    materialized = list(values)
    if not materialized or any(value is None or isinstance(value, bool) for value in materialized):
        return None
    if not all(isinstance(value, (int, float)) for value in materialized):
        return None
    total = sum(materialized)
    return int(total) if all(isinstance(value, int) for value in materialized) else float(total)


def _first_equal(values: Sequence[str | None] | Any) -> str | None:
    materialized = list(values)
    if materialized and all(value == materialized[0] for value in materialized):
        return materialized[0]
    return None


__all__ = [
    "AudioChunk",
    "AudioChunkSet",
    "ChunkTranscription",
    "extract_audio_chunks",
    "merge_chunk_results",
    "plan_chunk_ranges",
]
