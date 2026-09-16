# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Official OpenAI file-transcription adapter.

This adapter intentionally implements only the general batch/file contract.
Medical requests stay on the local/RunPod MedASR providers, and realtime is a
separate capability that is not exposed by this provider.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

from app.models import TranscriptionType
from app.services.transcription.chunks import (
    AudioChunkSet,
    ChunkTranscription,
    extract_audio_chunks,
    merge_chunk_results,
)
from app.services.transcription.contracts import (
    AudioSource,
    BatchTranscriptionRequest,
    ProviderCapabilities,
    TimestampMode,
    UsageMetadata,
)
from app.services.transcription.errors import (
    InvalidAudioSourceError,
    ProviderRequestError,
    TranscriptionConfigurationError,
    TranscriptionError,
    UnsupportedCapabilityError,
)
from app.services.transcription.provider_contract import (
    HeartbeatCallback,
    StatusCallback,
    validate_batch_request,
)
from app.services.transcription.types import (
    ResultSegment,
    TranscriptionConfig,
    TranscriptionResult,
    WordTimestamp,
    coarse_text_segment,
)


SUPPORTED_MODELS = frozenset({"gpt-transcribe", "gpt-4o-mini-transcribe"})
SUPPORTED_TIMESTAMP_MODELS = frozenset({"whisper-1"})
SUPPORTED_AUDIO_FORMATS = frozenset({
    "flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm",
})
SUPPORTED_RESPONSE_FORMATS = frozenset({"json", "verbose_json"})
OPENAI_AUDIO_BASE_URL = "https://api.openai.com/v1"
OPENAI_FILE_SIZE_LIMIT_BYTES = 25_000_000
DEFAULT_SINGLE_REQUEST_MAX_BYTES = 20_000_000
DEFAULT_CHUNK_MAX_BYTES = 15_000_000
DEFAULT_CHUNK_DURATION_SECONDS = 600.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_RETRY_MAX_BACKOFF_SECONDS = 8.0
DEFAULT_FFMPEG_TIMEOUT_SECONDS = 900.0
DEFAULT_MAX_CHUNKS = 1024
DEFAULT_TIMESTAMP_MODEL = "whisper-1"

OPENAI_FILE_CAPABILITIES = ProviderCapabilities(
    batch=True,
    realtime=False,
    medical=False,
    segments=True,
    words=True,
    speakers=False,
    duration=True,
    cloud_diarization=False,
    audio_formats=SUPPORTED_AUDIO_FORMATS,
    response_formats=SUPPORTED_RESPONSE_FORMATS,
)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    return {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _notify_heartbeat(callback: HeartbeatCallback | None) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception:
        pass


def _resolve_api_key(config: Any) -> str:
    """Resolve the optional transcription override before the shared key."""

    for setting in ("TRANSCRIPTION_API_KEY", "OPENAI_API_KEY"):
        value = str(getattr(config, setting, "") or "").strip()
        if value:
            return value
    return ""


def _config_int(config: Any, name: str, default: int) -> int:
    value = getattr(config, name, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptionConfigurationError(
            f"{name} must be an integer",
            setting=name,
        ) from exc


def _config_float(config: Any, name: str, default: float) -> float:
    value = getattr(config, name, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptionConfigurationError(
            f"{name} must be a number",
            setting=name,
        ) from exc


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def _is_transient_provider_error(error: BaseException) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    status_code = _status_code(error)
    return status_code in {408, 429} or (status_code is not None and 500 <= status_code <= 599)


def _error_detail(error: BaseException) -> str:
    detail = " ".join(str(error).split())
    return detail[:240] or type(error).__name__


def _usage_metadata(response: Any) -> UsageMetadata | None:
    raw = _mapping(_get(response, "usage"))
    if not raw:
        return None

    usage_type = raw.get("type")
    if usage_type == "duration":
        seconds = _number(raw.get("seconds"))
        return UsageMetadata(
            input_units=seconds,
            total_units=seconds,
            unit="seconds",
            raw=raw,
        )
    if usage_type == "tokens":
        return UsageMetadata(
            input_units=raw.get("input_tokens"),
            output_units=raw.get("output_tokens"),
            total_units=raw.get("total_tokens"),
            unit="tokens",
            raw=raw,
        )
    return UsageMetadata(raw=raw)


def _word_timestamps(response: Any) -> list[WordTimestamp]:
    words: list[WordTimestamp] = []
    for item in _get(response, "words", []) or []:
        start = _number(_get(item, "start"))
        end = _number(_get(item, "end"))
        if start is None or end is None:
            continue
        words.append(
            WordTimestamp(
                word=str(_get(item, "word", "")),
                start=start,
                end=end,
                speaker_label=_get(item, "speaker") or _get(item, "speaker_label"),
            )
        )
    return words


def _segments(response: Any, words: list[WordTimestamp]) -> list[ResultSegment]:
    segments: list[ResultSegment] = []
    for item in _get(response, "segments", []) or []:
        start = _number(_get(item, "start"))
        end = _number(_get(item, "end"))
        if start is None or end is None:
            continue
        segment_words = [word for word in words if word.end > start and word.start < end]
        segments.append(
            ResultSegment(
                start=start,
                end=end,
                text=str(_get(item, "text", "")).strip(),
                speaker=_get(item, "speaker") or "SPEAKER_UNKNOWN",
                words=segment_words,
            )
        )

    if not segments and words:
        segments.append(
            ResultSegment(
                start=min(word.start for word in words),
                end=max(word.end for word in words),
                text=str(_get(response, "text", "")).strip(),
                speaker="SPEAKER_UNKNOWN",
                words=words,
            )
        )
    return segments


def _provider_usage_record(
    *,
    provider: str,
    model: str,
    role: str,
    usage: UsageMetadata | None,
    status: str = "succeeded",
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "role": role,
        "status": status,
        "usage": usage.raw if usage else None,
    }
    if error is not None:
        record["error"] = dict(error)
    return record


def _provider_usage_records(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = metadata.get("provider_usages", ())
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


class _TimestampPassUnavailable(TranscriptionError):
    """The optional timestamp model returned no requested timing data."""


class OpenAIFileProvider:
    """Scoped provider for the official ``audio.transcriptions.create`` API."""

    provider_name = "openai-file"
    capabilities = OPENAI_FILE_CAPABILITIES

    def __init__(
        self,
        config: Any,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        api_key = _resolve_api_key(config)
        if not api_key:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_API_KEY or OPENAI_API_KEY is required for provider 'openai-file'",
                setting="TRANSCRIPTION_API_KEY",
            )
        self.model = str(getattr(config, "TRANSCRIPTION_MODEL", "gpt-transcribe") or "").strip()
        self._validate_model(self.model)
        self.timestamp_model = str(
            getattr(config, "TRANSCRIPTION_TIMESTAMP_MODEL", DEFAULT_TIMESTAMP_MODEL)
            or ""
        ).strip()
        if not self.timestamp_model:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_TIMESTAMP_MODEL must not be empty",
                setting="TRANSCRIPTION_TIMESTAMP_MODEL",
            )
        self._single_request_max_bytes = _config_int(
            config,
            "TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES",
            DEFAULT_SINGLE_REQUEST_MAX_BYTES,
        )
        self._chunk_max_bytes = _config_int(
            config,
            "TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES",
            DEFAULT_CHUNK_MAX_BYTES,
        )
        self._chunk_duration_seconds = _config_float(
            config,
            "TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS",
            DEFAULT_CHUNK_DURATION_SECONDS,
        )
        self._max_retries = _config_int(
            config,
            "TRANSCRIPTION_OPENAI_MAX_RETRIES",
            DEFAULT_MAX_RETRIES,
        )
        self._retry_backoff_seconds = _config_float(
            config,
            "TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS",
            DEFAULT_RETRY_BACKOFF_SECONDS,
        )
        self._retry_max_backoff_seconds = _config_float(
            config,
            "TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS",
            DEFAULT_RETRY_MAX_BACKOFF_SECONDS,
        )
        self._ffmpeg_timeout_seconds = _config_float(
            config,
            "TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS",
            DEFAULT_FFMPEG_TIMEOUT_SECONDS,
        )
        self._max_chunks = _config_int(config, "TRANSCRIPTION_OPENAI_MAX_CHUNKS", DEFAULT_MAX_CHUNKS)
        self._validate_long_file_settings()
        self._sleep = sleep or time.sleep
        # Disable the SDK's automatic retries so this provider owns one bounded,
        # observable policy for both small and chunked requests.
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=OPENAI_AUDIO_BASE_URL,
            max_retries=0,
        )
        self._closed = False

    def _validate_long_file_settings(self) -> None:
        if not 0 < self._single_request_max_bytes < OPENAI_FILE_SIZE_LIMIT_BYTES:
            raise TranscriptionConfigurationError(
                f"TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES must be between 1 and "
                f"{OPENAI_FILE_SIZE_LIMIT_BYTES - 1}",
                setting="TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES",
            )
        if not 0 < self._chunk_max_bytes <= self._single_request_max_bytes:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES must be positive and no larger "
                "than TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES",
                setting="TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES",
            )
        if self._chunk_duration_seconds <= 0:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS must be greater than zero",
                setting="TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS",
            )
        if not 0 <= self._max_retries <= 5:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_MAX_RETRIES must be between 0 and 5",
                setting="TRANSCRIPTION_OPENAI_MAX_RETRIES",
            )
        if self._retry_backoff_seconds < 0 or self._retry_max_backoff_seconds < 0:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS and "
                "TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS must not be negative",
                setting="TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS",
            )
        if self._retry_max_backoff_seconds < self._retry_backoff_seconds:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS must be at least "
                "TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS",
                setting="TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS",
            )
        if self._ffmpeg_timeout_seconds <= 0:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS must be greater than zero",
                setting="TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS",
            )
        if self._max_chunks <= 0:
            raise TranscriptionConfigurationError(
                "TRANSCRIPTION_OPENAI_MAX_CHUNKS must be greater than zero",
                setting="TRANSCRIPTION_OPENAI_MAX_CHUNKS",
            )

    @staticmethod
    def _validate_model(model: str) -> None:
        if model not in SUPPORTED_MODELS:
            raise UnsupportedCapabilityError(
                "model",
                OpenAIFileProvider.provider_name,
                model,
                "openai-file supports only gpt-transcribe and gpt-4o-mini-transcribe in this slice",
            )

    def _validate_request(self, request: BatchTranscriptionRequest, model: str, response_format: str) -> None:
        validate_batch_request(
            request,
            self.capabilities,
            provider=self.provider_name,
            model=model,
        )
        self._validate_model(model)
        if response_format not in SUPPORTED_RESPONSE_FORMATS:
            raise UnsupportedCapabilityError("response_format", self.provider_name, model)
        if request.transcription_type == TranscriptionType.MEDICAL:
            raise UnsupportedCapabilityError("medical", self.provider_name, model)

    def _build_primary_params(
        self,
        request: BatchTranscriptionRequest,
        *,
        model: str,
        response_format: str,
        submitted_language: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "response_format": response_format,
        }
        if submitted_language:
            params["language"] = submitted_language
        for name in (
            "prompt",
            "temperature",
            "chunking_strategy",
            "include",
            "known_speaker_names",
            "known_speaker_references",
        ):
            if name in request.metadata and request.metadata[name] is not None:
                params[name] = request.metadata[name]
        return params

    def _build_timestamp_params(
        self,
        request: BatchTranscriptionRequest,
        *,
        submitted_language: str | None,
    ) -> dict[str, Any]:
        if request.timestamp_mode == TimestampMode.NONE:
            raise UnsupportedCapabilityError(
                "timestamps",
                self.provider_name,
                self.timestamp_model,
            )
        if self.timestamp_model not in SUPPORTED_TIMESTAMP_MODELS:
            raise UnsupportedCapabilityError(
                "timestamp_model",
                self.provider_name,
                self.timestamp_model,
                "The OpenAI timestamp pass currently supports only whisper-1",
            )

        params: dict[str, Any] = {
            "model": self.timestamp_model,
            "response_format": "verbose_json",
            "timestamp_granularities": [request.timestamp_mode.value],
        }
        if submitted_language:
            params["language"] = submitted_language
        # Keep only options supported by whisper-1. The newer primary-model
        # options (for example known speakers) must not leak into this pass.
        for name in ("prompt", "temperature"):
            if name in request.metadata and request.metadata[name] is not None:
                params[name] = request.metadata[name]
        return params

    def transcribe(
        self,
        request: BatchTranscriptionRequest,
        *,
        on_status_change: StatusCallback | None = None,
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> TranscriptionResult:
        model = request.model or self.model
        response_format = request.response_format or "json"
        self._validate_request(request, model, response_format)

        source = request.source
        if source.local_path is None:
            raise InvalidAudioSourceError(
                "Provider 'openai-file' requires a readable local audio file"
            )
        path = Path(source.local_path)
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in SUPPORTED_AUDIO_FORMATS:
            raise InvalidAudioSourceError(
                f"Unsupported audio format '.{suffix or 'unknown'}' for provider 'openai-file'"
            )
        if not path.is_file():
            raise InvalidAudioSourceError(f"Audio file does not exist: {path}")

        submitted_language = request.language
        if submitted_language is None and request.allowed_languages and len(request.allowed_languages) == 1:
            submitted_language = next(iter(request.allowed_languages))
        # Timestamping is intentionally a separate whisper-1 request. The
        # primary text request remains compatible with json output.
        params = self._build_primary_params(
            request,
            model=model,
            response_format=response_format,
            submitted_language=submitted_language,
        )

        try:
            file_size = path.stat().st_size
        except OSError as exc:
            raise InvalidAudioSourceError(f"Unable to inspect audio file: {path}") from exc

        if file_size <= self._single_request_max_bytes:
            if on_status_change:
                on_status_change("transcribing")
            response = self._request_file_with_retries(
                path,
                params,
                on_heartbeat=on_heartbeat,
            )
            primary_result = self._response_to_result(
                response,
                request,
                model=model,
                response_format=response_format,
                submitted_language=submitted_language,
            )
            if request.timestamp_mode != TimestampMode.NONE:
                if on_status_change:
                    on_status_change("timestamping")
                primary_result = self._with_timestamp_pass(
                    primary_result,
                    path,
                    request,
                    submitted_language=submitted_language,
                    on_heartbeat=on_heartbeat,
                )
            return primary_result

        workspace = Path(tempfile.mkdtemp(prefix="zabt-openai-"))
        chunk_set: AudioChunkSet | None = None
        try:
            if on_status_change:
                on_status_change("preparing_audio")
            _notify_heartbeat(on_heartbeat)
            chunk_set = extract_audio_chunks(
                path,
                workspace,
                chunk_duration_seconds=self._chunk_duration_seconds,
                max_chunk_bytes=self._chunk_max_bytes,
                max_chunks=self._max_chunks,
                command_timeout_seconds=self._ffmpeg_timeout_seconds,
            )
            chunk_results: list[ChunkTranscription] = []
            total_chunks = len(chunk_set.chunks)
            # Keep chunk requests sequential: correctness and rate-limit safety
            # take precedence over throughput for this bounded recovery path.
            for chunk in chunk_set.chunks:
                if on_status_change:
                    on_status_change(f"transcribing_chunk ({chunk.index}/{total_chunks})")
                _notify_heartbeat(on_heartbeat)
                response = self._request_file_with_retries(
                    chunk.path,
                    params,
                    on_heartbeat=on_heartbeat,
                )
                primary_result = self._response_to_result(
                    response,
                    request,
                    model=model,
                    response_format=response_format,
                    submitted_language=submitted_language,
                    fallback_duration=chunk.duration_seconds,
                )
                if request.timestamp_mode != TimestampMode.NONE:
                    if on_status_change:
                        on_status_change(f"timestamping_chunk ({chunk.index}/{total_chunks})")
                    primary_result = self._with_timestamp_pass(
                        primary_result,
                        chunk.path,
                        request,
                        submitted_language=submitted_language,
                        fallback_duration=chunk.duration_seconds,
                        on_heartbeat=on_heartbeat,
                    )
                chunk_results.append(
                    ChunkTranscription(
                        chunk=chunk,
                        result=primary_result,
                    )
                )
                if on_status_change:
                    on_status_change(f"transcribing_chunk ({chunk.index}/{total_chunks}) complete")
                _notify_heartbeat(on_heartbeat)

            result = merge_chunk_results(
                chunk_results,
                audio_duration_seconds=chunk_set.source_duration_seconds,
            )
            result.metadata = {
                **result.metadata,
                "chunk_max_bytes": self._chunk_max_bytes,
                "chunk_duration_seconds": self._chunk_duration_seconds,
            }
            return result
        finally:
            if chunk_set is not None:
                chunk_set.cleanup()
            else:
                shutil.rmtree(workspace, ignore_errors=True)

    def _request_file_with_retries(
        self,
        path: Path,
        params: dict[str, Any],
        *,
        on_heartbeat: HeartbeatCallback | None,
        operation: str = "file transcription",
    ) -> Any:
        for attempt in range(self._max_retries + 1):
            _notify_heartbeat(on_heartbeat)
            try:
                response = self._request_file_once(path, params)
            except InvalidAudioSourceError:
                raise
            except TranscriptionError:
                raise
            except Exception as exc:
                status_code = _status_code(exc)
                transient = _is_transient_provider_error(exc)
                if not transient or attempt >= self._max_retries:
                    attempt_count = attempt + 1
                    status_text = f" HTTP {status_code}" if status_code is not None else ""
                    raise ProviderRequestError(
                        f"OpenAI {operation} failed after {attempt_count} attempt(s)."
                        f"{status_text} {_error_detail(exc)}",
                        provider=self.provider_name,
                        status_code=status_code,
                        attempts=attempt_count,
                    ) from exc

                delay = min(
                    self._retry_backoff_seconds * (2 ** attempt),
                    self._retry_max_backoff_seconds,
                )
                _notify_heartbeat(on_heartbeat)
                if delay:
                    self._sleep(delay)
            else:
                _notify_heartbeat(on_heartbeat)
                return response

        raise AssertionError("bounded retry loop exited unexpectedly")

    def _request_file_once(self, path: Path, params: dict[str, Any]) -> Any:
        try:
            with path.open("rb") as audio_file:
                return self._client.audio.transcriptions.create(file=audio_file, **params)
        except OSError as exc:
            raise InvalidAudioSourceError(f"Unable to read audio file: {path}") from exc

    def _response_to_result(
        self,
        response: Any,
        request: BatchTranscriptionRequest,
        *,
        model: str,
        response_format: str,
        submitted_language: str | None,
        fallback_duration: float | None = None,
        usage_role: str = "primary",
    ) -> TranscriptionResult:
        usage = _usage_metadata(response)
        duration = _number(_get(response, "duration"))
        if duration is None and usage and usage.unit == "seconds":
            duration = _number(usage.total_units)
        text = response if isinstance(response, str) else str(_get(response, "text", "") or "")
        words = _word_timestamps(response)
        segments = _segments(response, words)
        if not segments:
            coarse_segment = coarse_text_segment(
                text,
                start=0.0,
                duration=fallback_duration if fallback_duration is not None else duration,
            )
            if coarse_segment is not None:
                segments = [coarse_segment]
        language = str(_get(response, "language") or submitted_language or "unknown")
        speaker_labels = {segment.speaker for segment in segments if segment.speaker != "SPEAKER_UNKNOWN"}
        gaps: list[str] = []
        if not segments:
            gaps.append("segments")
        if not words:
            gaps.append("words")
        if not speaker_labels:
            gaps.append("speakers")
        if duration is None:
            gaps.append("duration")
        if request.allowed_languages and not submitted_language:
            gaps.append("allowed_languages")

        return TranscriptionResult(
            text=text,
            language=language,
            segments=segments,
            provider_name=self.provider_name,
            recognition_method="openai-audio-transcriptions",
            audio_duration_seconds=duration,
            estimated_cost=None,
            model=model,
            usage=usage,
            metadata={
                "provider": self.provider_name,
                "model": model,
                "response_format": response_format,
                "timestamp_mode": request.timestamp_mode.value,
                "requested_language": request.language,
                "submitted_language": submitted_language,
                "allowed_languages": sorted(request.allowed_languages or ()),
                "usage": usage.raw if usage else None,
                "provider_usages": [
                    _provider_usage_record(
                        provider=self.provider_name,
                        model=model,
                        role=usage_role,
                        usage=usage,
                    )
                ],
            },
            capability_gaps=tuple(gaps),
        )

    def _with_timestamp_pass(
        self,
        primary_result: TranscriptionResult,
        path: Path,
        request: BatchTranscriptionRequest,
        *,
        submitted_language: str | None,
        fallback_duration: float | None = None,
        on_heartbeat: HeartbeatCallback | None,
    ) -> TranscriptionResult:
        """Attach optional Whisper timing while retaining primary text/usage.

        A timestamp failure is deliberately non-fatal. The primary result is
        still returned, but the result carries a stable capability gap and a
        bounded error record so callers cannot mistake coarse fallback timing
        for synchronized words.
        """

        timestamp_mode = request.timestamp_mode.value
        gap = f"timestamps:{timestamp_mode}"
        try:
            timestamp_params = self._build_timestamp_params(
                request,
                submitted_language=submitted_language,
            )
            timestamp_response = self._request_file_with_retries(
                path,
                timestamp_params,
                on_heartbeat=on_heartbeat,
                operation="timestamp transcription",
            )
            timestamp_result = self._response_to_result(
                timestamp_response,
                request,
                model=self.timestamp_model,
                response_format="verbose_json",
                submitted_language=submitted_language,
                fallback_duration=fallback_duration,
                usage_role="timestamps",
            )
            has_requested_timestamps = (
                bool(timestamp_result.segments)
                if request.timestamp_mode == TimestampMode.SEGMENT
                else any(segment.words for segment in timestamp_result.segments)
            )
            if not has_requested_timestamps:
                raise _TimestampPassUnavailable(
                    f"whisper-1 returned no {timestamp_mode} timestamps"
                )
        except Exception as exc:
            return self._timestamp_failure_result(
                primary_result,
                request,
                gap=gap,
                error=exc,
            )

        metadata = dict(primary_result.metadata)
        metadata["primary_model"] = primary_result.model
        metadata["timestamp_model"] = self.timestamp_model
        metadata["timing_source"] = {
            "provider": self.provider_name,
            "model": self.timestamp_model,
            "timestamp_mode": timestamp_mode,
        }
        metadata["timestamp_pass"] = {
            "provider": self.provider_name,
            "model": self.timestamp_model,
            "response_format": "verbose_json",
            "timestamp_granularities": [timestamp_mode],
            "status": "succeeded",
            "usage": timestamp_result.usage.raw if timestamp_result.usage else None,
        }
        metadata["provider_usages"] = (
            _provider_usage_records(primary_result.metadata)
            + _provider_usage_records(timestamp_result.metadata)
        )

        gaps = [item for item in primary_result.capability_gaps if item != gap]
        if timestamp_result.segments:
            gaps = [item for item in gaps if item != "segments"]
        if request.timestamp_mode == TimestampMode.WORD and any(
            segment.words for segment in timestamp_result.segments
        ):
            gaps = [item for item in gaps if item != "words"]
        duration = primary_result.audio_duration_seconds or timestamp_result.audio_duration_seconds
        if duration is not None:
            gaps = [item for item in gaps if item != "duration"]

        return TranscriptionResult(
            text=primary_result.text,
            language=(
                primary_result.language
                if primary_result.language != "unknown"
                else timestamp_result.language
            ),
            segments=timestamp_result.segments,
            provider_name=primary_result.provider_name,
            recognition_method=primary_result.recognition_method,
            audio_duration_seconds=duration,
            estimated_cost=primary_result.estimated_cost,
            model=primary_result.model,
            usage=primary_result.usage,
            metadata=metadata,
            capability_gaps=tuple(gaps),
        )

    def _timestamp_failure_result(
        self,
        primary_result: TranscriptionResult,
        request: BatchTranscriptionRequest,
        *,
        gap: str,
        error: BaseException,
    ) -> TranscriptionResult:
        detail = _error_detail(error)
        attempts = getattr(error, "attempts", 0)
        status_code = getattr(error, "status_code", None)
        error_record = {
            "type": type(error).__name__,
            "message": detail,
            "attempts": attempts if isinstance(attempts, int) else 0,
            "status_code": status_code if isinstance(status_code, int) else None,
        }
        metadata = dict(primary_result.metadata)
        metadata["primary_model"] = primary_result.model
        metadata["timestamp_model"] = self.timestamp_model
        metadata["timing_source"] = None
        metadata["timestamp_pass"] = {
            "provider": self.provider_name,
            "model": self.timestamp_model,
            "response_format": "verbose_json",
            "timestamp_granularities": [request.timestamp_mode.value],
            "status": "failed",
            "usage": None,
            "error": error_record,
        }
        metadata["provider_usages"] = _provider_usage_records(primary_result.metadata) + [
            _provider_usage_record(
                provider=self.provider_name,
                model=self.timestamp_model,
                role="timestamps",
                usage=None,
                status="failed",
                error=error_record,
            )
        ]
        gaps = list(primary_result.capability_gaps)
        if gap not in gaps:
            gaps.append(gap)
        return TranscriptionResult(
            text=primary_result.text,
            language=primary_result.language,
            segments=primary_result.segments,
            provider_name=primary_result.provider_name,
            recognition_method=primary_result.recognition_method,
            audio_duration_seconds=primary_result.audio_duration_seconds,
            estimated_cost=primary_result.estimated_cost,
            model=primary_result.model,
            usage=primary_result.usage,
            metadata=metadata,
            capability_gaps=tuple(gaps),
        )

    def process_audio(
        self,
        audio_path: str,
        config: TranscriptionConfig | None = None,
        on_status_change: StatusCallback | None = None,
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> TranscriptionResult:
        config = config or TranscriptionConfig()
        request = BatchTranscriptionRequest(
            source=AudioSource.from_local_path(audio_path),
            language=config.language,
            allowed_languages=frozenset(config.allowed_languages) if config.allowed_languages else None,
            transcription_type=config.transcription_type,
            timestamp_mode=config.timestamp_mode,
            speaker_required=config.speaker_required,
            response_format=config.response_format,
            model=config.model,
        )
        return self.transcribe(
            request,
            on_status_change=on_status_change,
            on_heartbeat=on_heartbeat,
        )

    async def transcribe_chunk(self, data: bytes) -> str:
        raise UnsupportedCapabilityError("realtime", self.provider_name, self.model)

    def get_provider_name(self) -> str:
        return self.provider_name

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "OpenAIFileProvider":
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        self.close()


__all__ = [
    "OPENAI_FILE_CAPABILITIES",
    "OPENAI_FILE_SIZE_LIMIT_BYTES",
    "OpenAIFileProvider",
    "SUPPORTED_AUDIO_FORMATS",
    "SUPPORTED_MODELS",
    "SUPPORTED_TIMESTAMP_MODELS",
    "SUPPORTED_RESPONSE_FORMATS",
]
