# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import asyncio
import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from math import ceil, isfinite
from typing import Any

from celery import Celery, chain
from celery.exceptions import Ignore
from celery.signals import task_failure, task_prerun, task_success, worker_shutdown

from app.core.config import settings
from app.core.logging import init_sentry, init_logfire, get_logger

init_sentry()
init_logfire(service_name="zabt-worker")
logger = get_logger(__name__)

if settings.LOGFIRE_TOKEN:
    import logfire
    logfire.instrument_celery()
from app.db.engine import engine
from app.models import (
    Group,
    Meeting,
    TranscriptSegment,
    TranscriptionType,
    User,
    VisualSegment,
)
from app.services.meeting import meeting_service
from app.services.storage import get_storage_for_provider, storage
from app.services.transcription import (
    BatchTranscriptionRequest,
    TimestampMode,
    build_config,
    get_provider,
)
from app.services.transcription.contracts import ProviderName
from app.services.transcription.source import AudioSourceResolver
from app.services.styles import style_service
from app.services import ai_agent
from app.services.ai_agent import summarize_transcript
from app.services.ai_provider import get_completion_client
from app.services.template import template_service
from app.services.storage_configuration import StorageConfigurationValidationError
from app.services import analytics
from app.services.notifications import notify
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.chunk import build_meeting_chunks
from app.services.vector_store import EmbeddingPoint, get_vector_store
from app.services.meeting_recovery import (
    MeetingRecoveryPlan,
    claim_recovery_plan,
    dispatch_recovery_plan,
)
from app.services.meeting_processing_audit import meeting_processing_audit
from app.services.visual_breakdown.direct_service import DirectVisionService

from sqlalchemy import func
from sqlmodel import Session, select


@worker_shutdown.connect
def flush_analytics(**kwargs):
    analytics.shutdown()

celery_app = Celery("worker", broker=settings.REDIS_URL, backend=settings.REDIS_URL)


# ── Notification task ────────────────────────────────────────────────────────

@celery_app.task(name="send_notification", ignore_result=True)
def send_notification(
    emoji: str, label: str, user_email: str,
    meeting_title: str | None, meeting_id: int | None, extra: dict[str, str],
    *, processing_run_id: int | None = None, dispatch_attempt_id: str | None = None,
) -> None:
    """Deliver a notification via the configured provider. Best-effort."""
    from app.services.notifications import get_provider
    from app.services.notifications.provider import NotificationEvent

    provider = get_provider()
    if provider is None:
        return
    event = NotificationEvent(
        event_type=label,
        emoji=emoji,
        label=label,
        user_email=user_email,
        meeting_title=meeting_title,
        meeting_id=meeting_id,
        extra=extra,
    )
    if processing_run_id is not None or dispatch_attempt_id is not None:
        if processing_run_id is None or not dispatch_attempt_id or meeting_id is None:
            return
        eligible = asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, processing_run_id, dispatch_attempt_id
            )
        )
        if not eligible:
            return
    provider.send(event)

# ── Shared temp directory for passing file paths between stages ──────────────
# Must be a volume shared across all worker replicas — /tmp is per-container.
TEMP_DIR = os.environ.get("TEMP_DIR", "/media/tmp")
os.makedirs(TEMP_DIR, exist_ok=True)


def _configured_transcription_provider() -> str:
    value = getattr(settings, "TRANSCRIPTION_PROVIDER", None) or getattr(settings, "TRANSCRIPTION_BACKEND", None)
    return value.value if hasattr(value, "value") else str(value or ProviderName.GPU_LOCAL.value)


def _temp_path_for(
    meeting_id: int,
    source_key: str | None,
    *,
    run_id: int,
    attempt_id: str,
) -> str:
    """Return a temp path owned by exactly one processing attempt."""
    if run_id is None or not attempt_id:
        raise ValueError("Attempt-owned temp paths require a run and attempt identity")
    suffix = os.path.splitext(source_key or "")[1] or ".audio"
    attempt_token = sha256(attempt_id.encode("utf-8")).hexdigest()
    return os.path.join(TEMP_DIR, f"zabt_meeting_{meeting_id}_{run_id}_{attempt_token}{suffix}")


def _youtube_attempt_artifacts(
    owner_id: int, meeting_id: int, run_id: int, attempt_id: str, video_id: str
) -> tuple[str, str]:
    attempt_token = sha256(attempt_id.encode("utf-8")).hexdigest()
    download_id = f"{video_id}_meeting_{meeting_id}_run_{run_id}_attempt_{attempt_token}"
    file_key = (
        f"users/{owner_id}/meetings/youtube/{meeting_id}/runs/{run_id}/"
        f"attempts/{attempt_token}/{video_id}.mp3"
    )
    return download_id, file_key


def _youtube_attempt_temp_dir(meeting_id: int, run_id: int, attempt_id: str) -> str:
    attempt_token = sha256(attempt_id.encode("utf-8")).hexdigest()
    return os.path.join(TEMP_DIR, f"zabt_youtube_{meeting_id}_{run_id}_{attempt_token}")


def _cleanup_youtube_attempt_artifacts(
    *,
    work_dir: str | None,
    handoff_path: str | None,
    file_key: str | None,
    object_write_started: bool,
    promoted: bool,
    meeting_id: int,
    run_id: int,
    attempt_id: str,
    storage_client=None,
) -> None:
    if work_dir and os.path.isdir(work_dir):
        try:
            shutil.rmtree(work_dir)
        except OSError:
            logger.warning("Could not remove YouTube attempt directory", exc_info=True)
    if not promoted and handoff_path and os.path.exists(handoff_path):
        try:
            os.remove(handoff_path)
        except OSError:
            logger.warning("Could not remove YouTube handoff file", exc_info=True)
    if not object_write_started or promoted or not file_key:
        return
    try:
        if not meeting_processing_audit.youtube_audio_was_promoted(
            run_id=run_id, attempt_id=attempt_id, meeting_id=meeting_id
        ):
            artifact_storage = storage if storage_client is None else storage_client
            artifact_storage.delete_file(file_key)
    except Exception:
        logger.warning("Could not remove unpromoted YouTube attempt object", exc_info=True)


_LOCAL_MEDIA_PROBE_TIMEOUT_SECONDS = 10.0
_LOCAL_MEDIA_AUDIO_PROBE_TIMEOUT_SECONDS = 60.0
_LOCAL_MEDIA_SILENCE_THRESHOLD_DB = -90.0
_TRANSCRIPTION_SKIPPED_NO_AUDIO = "transcription_skipped_no_audio"


@dataclass(frozen=True)
class _LocalMediaProbe:
    has_video: bool
    has_audio: bool
    duration_seconds: float | None
    audio_is_silent: bool | None = None


def _probe_audio_silence(path: str, audio_stream_count: int) -> bool | None:
    """Return whether every audio stream is near-digital silence, or None if unknown."""
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-i",
                path,
                "-map",
                "0:a",
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_LOCAL_MEDIA_AUDIO_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("local media audio probe timed out path=%s", path)
        return None
    except OSError as exc:
        logger.warning("local media audio probe unavailable path=%s error=%s", path, exc)
        return None

    if completed.returncode != 0:
        logger.warning(
            "local media audio probe failed path=%s returncode=%s",
            path,
            completed.returncode,
        )
        return None

    max_volume_values = re.findall(
        r"\bmax_volume:\s*(-inf|[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*dB\b",
        completed.stderr or "",
        flags=re.IGNORECASE,
    )
    if len(max_volume_values) != audio_stream_count:
        logger.warning(
            "local media audio probe returned incomplete metadata path=%s expected_streams=%s reported_streams=%s",
            path,
            audio_stream_count,
            len(max_volume_values),
        )
        return None

    for value in max_volume_values:
        if value.casefold() == "-inf":
            continue
        try:
            peak_db = float(value)
        except ValueError:
            return None
        if not isfinite(peak_db):
            return None
        if peak_db > _LOCAL_MEDIA_SILENCE_THRESHOLD_DB:
            return False
    return True


def _probe_local_media(path: str) -> _LocalMediaProbe | None:
    """Best-effort stream inspection for media that is already local.

    Probe failures intentionally fall through to the existing provider path so
    this guard cannot turn a previously supported media format into a pipeline
    failure.
    """
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_LOCAL_MEDIA_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("local media stream probe timed out path=%s", path)
        return None
    except OSError as exc:
        logger.warning("local media stream probe unavailable path=%s error=%s", path, exc)
        return None

    if completed.returncode != 0:
        logger.warning(
            "local media stream probe failed path=%s returncode=%s",
            path,
            completed.returncode,
        )
        return None

    try:
        payload = json.loads(completed.stdout or "")
        streams = payload.get("streams") or []
        if not isinstance(streams, list):
            return None
    except (AttributeError, TypeError, ValueError):
        logger.warning("local media stream probe returned invalid metadata path=%s", path)
        return None

    format_data = payload.get("format") or {}
    if not isinstance(format_data, dict):
        format_data = {}

    duration_seconds: float | None = None
    duration_values = [
        format_data.get("duration"),
        *(stream.get("duration") for stream in streams if isinstance(stream, dict)),
    ]
    for value in duration_values:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(candidate) and candidate > 0:
            duration_seconds = candidate
            break

    has_video = any(
        isinstance(stream, dict) and stream.get("codec_type") == "video"
        for stream in streams
    )
    audio_stream_count = sum(
        1 for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    )
    return _LocalMediaProbe(
        has_video=has_video,
        has_audio=audio_stream_count > 0,
        duration_seconds=duration_seconds,
        audio_is_silent=(
            _probe_audio_silence(path, audio_stream_count)
            if has_video and audio_stream_count
            else None
        ),
    )


def _mark_transcription_skipped_for_no_audio(
    meeting_id: int,
    duration_seconds: float | None,
    run_id: int,
    attempt_id: str,
) -> bool:
    """Mark the current attempt as skipping transcription without deleting prior outputs."""
    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        if meeting is None:
            return False

        meeting.status = "processing"
        meeting.sub_status = _TRANSCRIPTION_SKIPPED_NO_AUDIO
        meeting.processing_heartbeat_at = datetime.utcnow()
        if duration_seconds is not None:
            meeting.duration_seconds = int(duration_seconds)
        session.add(meeting)
        session.commit()

    meeting_service._publish_sub_status(meeting_id, _TRANSCRIPTION_SKIPPED_NO_AUDIO)
    return True


_AUDITED_STAGE_TASKS = {
    "stage_download",
    "stage_youtube_download",
    "stage_transcribe",
    "stage_transliterate",
    "stage_optional_visual_breakdown",
    "stage_visual_breakdown",
    "stage_summarize",
    "stage_extract_intelligence",
    "stage_embedding",
    "finalize_recovered_meeting",
}


def _audit_headers(
    run_id: int | None,
    meeting_id: int,
    stage: str,
    attempt_id: str | None = None,
) -> dict[str, str]:
    headers = {"meeting_id": str(meeting_id), "stage": stage}
    if run_id is not None:
        headers["meeting_processing_run_id"] = str(run_id)
    if attempt_id is not None:
        headers["dispatch_attempt_id"] = attempt_id
    return headers


def _get_audit_header(task, key: str) -> str | None:
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    if isinstance(headers, dict):
        value = headers.get(key)
        return str(value) if value is not None else None
    return None


def _task_request_args(task) -> tuple | list | None:
    request = getattr(task, "request", None)
    args = getattr(request, "args", None)
    return args if args is not None else None


def _task_request_id(task) -> str | None:
    request = getattr(task, "request", None)
    value = getattr(request, "id", None)
    return str(value) if value is not None else None


def _audit_run_id_from_task(task) -> int | None:
    value = _get_audit_header(task, "meeting_processing_run_id")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _audit_attempt_id_from_task(task) -> str | None:
    return _get_audit_header(task, "dispatch_attempt_id")


def _required_processing_identity(task) -> tuple[int, str]:
    run_id = _audit_run_id_from_task(task)
    attempt_id = _audit_attempt_id_from_task(task)
    if run_id is None or not attempt_id:
        raise Ignore()
    return run_id, attempt_id


def _visual_processing_identity(
    task, *, allow_standalone: bool
) -> tuple[int | None, str | None]:
    run_header = _get_audit_header(task, "meeting_processing_run_id")
    attempt_header = _get_audit_header(task, "dispatch_attempt_id")
    if run_header is None and attempt_header is None:
        if allow_standalone:
            return None, None
        raise Ignore()
    return _required_processing_identity(task)


def _update_sub_status_for_attempt(
    meeting_id: int, run_id: int, attempt_id: str, sub_status: str | None
) -> bool:
    """Update current-attempt progress or heartbeat under the audit lock."""
    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        if meeting is None:
            return False
        if sub_status is not None:
            meeting.status = "processing"
            meeting.sub_status = sub_status
        meeting.processing_heartbeat_at = datetime.utcnow()
        session.add(meeting)
        session.commit()
    if sub_status is not None:
        meeting_service._publish_sub_status(meeting_id, sub_status)
    return True


def _meeting_id_from_task_args(args) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _build_pipeline_signatures(
    meeting_id: int,
    stages: list,
    run_id: int | None,
    attempt_id: str | None = None,
):
    signatures = []
    for index, stage in enumerate(stages):
        stage_name = stage.name
        signature = stage.s(meeting_id) if index == 0 else stage.s()
        link_errors = [] if stage_name == "stage_embedding" else [on_stage_failure.s()]
        signatures.append(
            signature.set(
                headers=_audit_headers(run_id, meeting_id, stage_name, attempt_id),
                link_error=link_errors,
            )
        )
    return signatures


@task_prerun.connect
def _record_audit_stage_started(task_id=None, task=None, args=None, **kwargs):
    stage = getattr(task, "name", None)
    if stage not in _AUDITED_STAGE_TASKS:
        return
    if stage == "stage_summarize":
        # The provider-admission claim is the summary stage's logical start.
        return
    meeting_id = _meeting_id_from_task_args(args)
    if meeting_id is None:
        return
    run_id = _audit_run_id_from_task(task)
    attempt_id = _audit_attempt_id_from_task(task)
    if run_id is None or attempt_id is None:
        return
    meeting_processing_audit.stage_started(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        stage=stage,
        task_id=task_id,
        metadata={"celery_task_name": stage},
    )


@task_success.connect
def _record_audit_stage_succeeded(sender=None, result=None, **kwargs):
    task = sender
    stage = getattr(task, "name", None)
    if stage not in _AUDITED_STAGE_TASKS:
        return
    args = _task_request_args(task)
    meeting_id = _meeting_id_from_task_args(args)
    if meeting_id is None:
        meeting_id = result if isinstance(result, int) else None
    if meeting_id is None:
        return
    run_id = _audit_run_id_from_task(task)
    attempt_id = _audit_attempt_id_from_task(task)
    if run_id is None or attempt_id is None:
        return
    task_id = _task_request_id(task)
    meeting_processing_audit.stage_completed(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        stage=stage,
        task_id=task_id,
        metadata={"celery_state": "SUCCESS"},
    )
    terminal_visual_success = (
        stage == "stage_visual_breakdown"
        and _get_audit_header(task, "meeting_processing_terminal_success") == "true"
    )
    if terminal_visual_success or stage == "stage_embedding" or (
        stage == "finalize_recovered_meeting" and result == meeting_id
    ):
        meeting_processing_audit.run_completed(
            run_id,
            attempt_id=attempt_id,
            message=(
                "Visual analysis completed"
                if terminal_visual_success
                else "Processing pipeline completed"
            ),
        )


@task_failure.connect
def _record_audit_stage_failed(task_id=None, exception=None, args=None, sender=None, **kwargs):
    stage = getattr(sender, "name", None)
    if stage not in _AUDITED_STAGE_TASKS:
        return
    meeting_id = _meeting_id_from_task_args(args)
    if meeting_id is None:
        return
    run_id = _audit_run_id_from_task(sender)
    attempt_id = _audit_attempt_id_from_task(sender)
    if run_id is None or attempt_id is None:
        return
    meeting_processing_audit.stage_failed(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        stage=stage,
        task_id=task_id,
        error=exception,
        metadata={"celery_task_name": stage},
    )
    meeting_processing_audit.run_failed(
        run_id,
        attempt_id=attempt_id,
        error=exception,
    )


# ── Error callback (linked to each stage via link_error) ─────────────────────

@celery_app.task(name="on_stage_failure")
def on_stage_failure(request, exc, traceback):
    """Fail the exact run while keeping visual-only failures meeting-scoped."""
    # The first argument of the original task is always meeting_id
    meeting_id = request.args[0] if request.args else None
    if meeting_id is None:
        logger.warning("on_stage_failure: could not determine meeting_id request_args=%s", str(request.args))
        return

    logger.info("on_stage_failure triggered meeting_id=%s exc=%s", meeting_id, str(exc))
    headers = getattr(request, "headers", None) or {}
    run_id = None
    attempt_id = None
    if isinstance(headers, dict):
        try:
            header_run_id = headers.get("meeting_processing_run_id")
            run_id = int(header_run_id) if header_run_id else None
        except (TypeError, ValueError):
            run_id = None
        attempt_id = headers.get("dispatch_attempt_id")
    if run_id is None or not attempt_id:
        logger.warning("on_stage_failure: missing processing identity meeting_id=%s", meeting_id)
        return
    # task_failure is the authority for per-stage events. This callback only
    # updates meeting status for the exact current dispatch attempt.
    failed_meeting = meeting_service.mark_failed(
        meeting_id,
        str(exc),
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
    )
    if failed_meeting is None:
        return
    meeting_processing_audit.run_failed(run_id, attempt_id=attempt_id, error=exc)
    if failed_meeting.status == "completed":
        return

    # This check admits both failure effects. Its DB session closes before
    # either network call; a retry/cancellation after admission may race them.
    if not meeting_processing_audit.failure_effects_are_eligible(
        meeting_id=meeting_id,
        run_id=run_id,
        attempt_id=attempt_id,
    ):
        return

    # Track failure in PostHog
    try:
        if failed_meeting and failed_meeting.owner_id:
            error_reason = str(exc)[:200]
            analytics.capture(
                failed_meeting.owner_id,
                "transcription_failed",
                {
                    "meeting_id": meeting_id,
                    "error_reason": error_reason,
                    "source_type": failed_meeting.source_type,
                    "transcription_type": failed_meeting.transcription_type,
                },
            )
    except Exception:
        logger.exception("on_stage_failure analytics failed meeting_id=%s", meeting_id)

    # Send failure email (fire-and-forget — never raises)
    try:
        from app.services.email import email_service
        if failed_meeting and failed_meeting.owner_id:
            with Session(engine) as session:
                user = session.get(User, failed_meeting.owner_id)
            if user and user.email:
                email_service.send_failure_email(user.email, failed_meeting, str(exc))
    except Exception:
        logger.exception("on_stage_failure email lookup failed meeting_id=%s", meeting_id)

    # Meeting-scoped temp files may belong to a later attempt; leave them intact.


# ── Stage 1: Download ────────────────────────────────────────────────────────

@celery_app.task(name="stage_download")
def stage_download(meeting_id: int) -> int:
    """Download audio from storage. Skips download for RunPod (it fetches via presigned URL)."""
    logger.info("stage_download started meeting_id=%s", meeting_id)
    run_id, attempt_id = _required_processing_identity(stage_download)
    temp_audio_path: str | None = None

    try:
        if not _update_sub_status_for_attempt(
            meeting_id, run_id, attempt_id, "downloading"
        ):
            raise Ignore()
        meeting = meeting_service.get(Meeting, meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found.")

        if not meeting.file_path:
            raise ValueError("No S3 file path associated with meeting.")

        # RunPod fetches audio directly via presigned URL — skip local download
        if _configured_transcription_provider() == ProviderName.RUNPOD.value:
            logger.info("stage_download skipped (RunPod mode) meeting_id=%s", meeting_id)
        else:
            download_url = get_storage_for_provider(
                meeting.storage_provider
            ).get_presigned_download_url(meeting.file_path)
            temp_audio_path = _temp_path_for(
                meeting_id, meeting.file_path, run_id=run_id, attempt_id=attempt_id
            )
            urllib.request.urlretrieve(download_url, temp_audio_path)

        logger.info("stage_download complete meeting_id=%s owner_id=%s", meeting_id, meeting.owner_id)
    except Ignore:
        raise
    except Exception:
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except OSError:
                logger.warning("Could not remove failed attempt-owned download", exc_info=True)
        logger.exception("stage_download failed meeting_id=%s", meeting_id)
        raise

    return meeting_id


# ── Stage 2: Transcribe (includes provider's align + diarize) ────────────────


def _resolve_meeting_language_for_transcription(
    session: "Session", meeting_id: int,
) -> tuple[str | None, set[str]]:
    """Return (forced_whisper_lang, allowed_whisper_langs) for a meeting.

    forced is the whisper code of the meeting's requested language (the source
    side of a transliteration pair, if applicable). allowed is the set derived
    from the user's full preference list, with `forced` added if set.
    """
    from app.models.base import Meeting
    from app.services.languages import catalog as lang_catalog
    from app.services.languages import preferences as lang_prefs

    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return None, set()

    forced: str | None = None
    if meeting.requested_language:
        entry = lang_catalog.get_entry(session, meeting.requested_language)
        if entry:
            if entry.transliterate_from:
                source_entry = lang_catalog.get_entry(session, entry.transliterate_from)
                forced = source_entry.whisper_lang if source_entry else None
            else:
                forced = entry.whisper_lang

    allowed: set[str] = set()
    if meeting.owner_id:
        allowed = lang_prefs.get_allowed_whisper_langs(session, meeting.owner_id)
        if forced:
            allowed.add(forced)

    return forced, allowed


def _save_transcription_for_attempt(
    meeting_id: int,
    run_id: int,
    attempt_id: str,
    result,
    *,
    owner_id: int | None,
    user_tier,
) -> bool:
    """Apply transcript rows and duration only while this attempt holds both locks."""
    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        if meeting is None:
            return False

        old_segments = session.exec(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
        ).all()
        for old in old_segments:
            session.delete(old)

        for segment in result.segments:
            words = [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "speaker": word.speaker_label,
                }
                for word in segment.words
            ]
            session.add(
                TranscriptSegment(
                    meeting_id=meeting_id,
                    start_time=segment.start,
                    end_time=segment.end,
                    text=segment.text,
                    speaker=segment.speaker or "SPEAKER_UNKNOWN",
                    words=words,
                )
            )

        meeting.transcript_text = result.text
        if result.audio_duration_seconds is not None:
            meeting.duration_seconds = int(result.audio_duration_seconds)
        if user_tier is not None and result.audio_duration_seconds is not None:
            user = session.get(User, owner_id) if owner_id else None
            if user:
                user.minutes_used_this_month += ceil(result.audio_duration_seconds / 60)
                session.add(user)
        session.add(meeting)
        session.commit()
    return True


@celery_app.task(name="stage_transcribe")
def stage_transcribe(meeting_id: int) -> int:
    """Run the transcription provider pipeline (transcribe + align + diarize)."""
    logger.info("stage_transcribe started meeting_id=%s", meeting_id)

    run_id, attempt_id = _required_processing_identity(stage_transcribe)
    is_runpod = _configured_transcription_provider() == ProviderName.RUNPOD.value
    temp_audio_path: str | None = None

    def on_transcribe_progress(stage: str):
        """Callback from provider — updates sub_status for each internal stage."""
        sub_status = stage.split(" (")[0]
        _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, sub_status)

    def on_transcription_heartbeat() -> None:
        """Refresh liveness without allowing metadata failures to abort work."""
        try:
            _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None)
        except Exception:
            logger.warning("Transcription heartbeat update failed", exc_info=True)

    try:
        with Session(engine) as session:
            candidate = session.get(Meeting, meeting_id)
            if candidate is not None:
                temp_audio_path = _temp_path_for(
                    meeting_id,
                    candidate.file_path,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
            meeting, _run = meeting_processing_audit._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if meeting is None:
                raise Ignore()
            temp_audio_path = _temp_path_for(
                meeting_id, meeting.file_path, run_id=run_id, attempt_id=attempt_id
            )
            if not is_runpod and not os.path.exists(temp_audio_path):
                raise FileNotFoundError(f"Temp audio file not found: {temp_audio_path}")

            owner_id = meeting.owner_id
            file_key = meeting.file_path
            storage_provider = meeting.storage_provider
            transcription_type = meeting.transcription_type
            user: User | None = session.get(User, owner_id) if owner_id else None
            user_tier = user.tier if user else None
            meeting.status = "processing"
            meeting.sub_status = "transcribing"
            meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()

        meeting_service._publish_sub_status(meeting_id, "transcribing")

        if not is_runpod:
            media_probe = _probe_local_media(temp_audio_path)
            if media_probe and media_probe.has_video and (
                not media_probe.has_audio or media_probe.audio_is_silent is True
            ):
                skip_reason = "silent_audio_stream" if media_probe.has_audio else "no_audio_stream"
                logger.info(
                    "stage_transcribe skipped: video has no audible audio "
                    "meeting_id=%s duration_seconds=%s reason=%s",
                    meeting_id,
                    media_probe.duration_seconds,
                    skip_reason,
                )
                skipped = _mark_transcription_skipped_for_no_audio(
                    meeting_id,
                    media_probe.duration_seconds,
                    run_id,
                    attempt_id,
                )
                if not skipped:
                    raise Ignore()
                return meeting_id

        # Resolve language preferences for this meeting
        with Session(engine) as session:
            forced_lang, allowed_langs = _resolve_meeting_language_for_transcription(
                session, meeting_id
            )

        # Build the provider-neutral request while preserving storage-key
        # resolution for GPU/RunPod and a local file for OpenAI.
        config = build_config(
            user_tier=user_tier,
            language=forced_lang,
            allowed_languages=allowed_langs or None,
        )
        config.storage_provider = storage_provider
        config.transcription_type = transcription_type or TranscriptionType.GENERAL

        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()
        with get_provider(user_tier=user_tier) as provider:
            source = AudioSourceResolver.resolve_for_provider(
                temp_audio_path,
                getattr(provider, "provider_name", _configured_transcription_provider()),
                storage_key=file_key,
            )
            request = BatchTranscriptionRequest(
                source=source,
                language=config.language,
                allowed_languages=frozenset(config.allowed_languages) if config.allowed_languages else None,
                transcription_type=config.transcription_type,
                timestamp_mode=TimestampMode(config.timestamp_mode),
                speaker_required=config.speaker_required,
                response_format=config.response_format,
                model=config.model,
                chunking_strategy=config.chunking_strategy,
                storage_provider=config.storage_provider,
            )
            result = provider.transcribe(
                request,
                on_status_change=on_transcribe_progress,
                on_heartbeat=on_transcription_heartbeat,
            )

        if not _save_transcription_for_attempt(
            meeting_id,
            run_id,
            attempt_id,
            result,
            owner_id=owner_id,
            user_tier=user_tier,
        ):
            raise Ignore()

    except Ignore:
        raise
    except Exception as provider_exc:
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore() from provider_exc
        logger.exception("stage_transcribe failed meeting_id=%s", meeting_id)
        raise

    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except OSError:
                logger.warning("Could not remove attempt-owned transcription temp file", exc_info=True)

    def _duration_tier(seconds: float | None) -> str:
        if seconds is None:
            return "unknown"
        if seconds < 600:
            return "short"
        if seconds <= 3600:
            return "medium"
        return "long"

    transcription_notification: tuple[str, str | None, str] | None = None
    with Session(engine) as session:
        meeting_for_analytics = session.get(Meeting, meeting_id)
        if meeting_for_analytics and meeting_for_analytics.owner_id:
            analytics.capture(
                meeting_for_analytics.owner_id,
                "transcription_completed",
                {
                    "meeting_id": meeting_id,
                    "duration_tier": _duration_tier(result.audio_duration_seconds),
                },
            )
            user_obj = session.get(User, meeting_for_analytics.owner_id)
            duration_minutes = (
                ceil(result.audio_duration_seconds / 60)
                if result.audio_duration_seconds is not None
                else None
            )
            transcription_notification = (
                (user_obj.email if user_obj else None)
                or str(meeting_for_analytics.owner_id),
                meeting_for_analytics.title,
                f"{duration_minutes} min" if duration_minutes is not None else "unknown",
            )

            # Capture language-resolution telemetry (best-effort)
            try:
                analytics.capture(
                    meeting_for_analytics.owner_id,
                    "transcription_language_resolved",
                    {
                        "meeting_id": meeting_id,
                        "requested_language": meeting_for_analytics.requested_language,
                        "forced_whisper_lang": forced_lang,
                        "detected_language": result.language,
                        "detection_inside_allowed_set": (
                            result.language in allowed_langs if allowed_langs else None
                        ),
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to capture transcription_language_resolved", exc_info=True
                )

    if transcription_notification is not None:
        notification_email, notification_title, duration_label = transcription_notification
        notify(
            "transcription_completed",
            notification_email,
            notification_title,
            meeting_id=meeting_id,
            processing_run_id=run_id,
            dispatch_attempt_id=attempt_id,
            extra={"Duration": duration_label},
        )

    logger.info("stage_transcribe complete meeting_id=%s", meeting_id)
    return meeting_id


# ── Stage 2b: Transliterate ──────────────────────────────────────────────────

@celery_app.task(name="stage_transliterate")
def stage_transliterate(meeting_id: int) -> int:
    """If meeting.requested_language has a transliteration target, populate
    meeting.transliterated_text with the alternate-script version."""
    from app.services.languages import catalog as lang_catalog
    from app.services.languages.transliteration import transliterate

    logger.info("stage_transliterate started meeting_id=%s", meeting_id)
    run_id, attempt_id = _required_processing_identity(stage_transliterate)

    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        if meeting is None:
            raise Ignore()
        if meeting.requested_language is None:
            return meeting_id

        requested = lang_catalog.get_entry(session, meeting.requested_language)
        if requested is None:
            return meeting_id

        # Decide source / target pair
        if requested.transliterate_from:
            source_code = requested.transliterate_from
            target_code = requested.code
        else:
            target_entry = lang_catalog.transliteration_target_for(session, requested.code)
            if target_entry is None:
                return meeting_id  # nothing to do
            # Only transliterate if the user has the alternate-script entry in their preferences
            if meeting.owner_id:
                from app.services.languages import preferences as lang_prefs
                user_prefs = lang_prefs.get_preferences(session, meeting.owner_id)
                if target_entry.code not in user_prefs:
                    return meeting_id
            source_code = requested.code
            target_code = target_entry.code

        if not meeting.transcript_text:
            logger.info("stage_transliterate: no transcript_text yet for %s", meeting_id)
            return meeting_id
        source_text = meeting.transcript_text

    claim = meeting_processing_audit.claim_transliteration(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        task_id=_task_request_id(stage_transliterate),
    )
    if claim is None:
        raise Ignore()

    try:
        roman = transliterate(
            text=source_text,
            source_code=source_code,
            target_code=target_code,
        )
    except Exception:
        logger.exception("Transliteration failed for meeting %s", meeting_id)
        raise

    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        if meeting is None:
            raise Ignore()
        meeting.transliterated_text = roman
        session.add(meeting)
        session.commit()

    return meeting_id


# ── Stage 3: Summarize ───────────────────────────────────────────────────────

@celery_app.task(name="stage_summarize")
def stage_summarize(meeting_id: int, template_id: int | None = None) -> int:
    """Build ephemeral evidence context and generate the meeting summary.

    Completion is deliberately deferred to ``stage_extract_intelligence`` so a
    meeting cannot appear completed while the transcript-only intelligence pass
    is still running.
    """
    logger.info("stage_summarize started meeting_id=%s template_id=%s", meeting_id, template_id)
    run_id, attempt_id = _required_processing_identity(stage_summarize)
    claim = meeting_processing_audit.claim_summary(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        task_id=_task_request_id(stage_summarize),
    )
    if claim is None:
        raise Ignore()
    if not _update_sub_status_for_attempt(
        meeting_id, run_id, attempt_id, "building_context"
    ):
        raise Ignore()

    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting:
        raise ValueError(f"Meeting {meeting_id} not found.")

    from app.services.multimodal_context import build_context

    with Session(engine) as session:
        transcript_segments = list(
            session.exec(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting_id)
                .order_by(TranscriptSegment.start_time)
            )
        )
        visual_segments = list(
            session.exec(
                select(VisualSegment)
                .where(VisualSegment.meeting_id == meeting_id)
                .order_by(VisualSegment.sequence)
            )
        )

    context_result = None
    if transcript_segments or visual_segments:
        if not transcript_segments and meeting.transcript_text:
            transcript_segments = [
                {
                    "id": 0,
                    "start_time": 0.0,
                    "end_time": max(float(meeting.duration_seconds or 0), 1.0),
                    "text": meeting.transcript_text,
                }
            ]
        context_result = build_context(
            transcript_segments,
            visual_segments,
            chunk_seconds=settings.SUMMARY_CHUNK_SECONDS,
            max_input_tokens=settings.SUMMARY_MAX_INPUT_TOKENS,
        )
    if context_result and context_result.warning_codes:
        logger.warning(
            "summary context completed with bounded warnings meeting_id=%s warnings=%s",
            meeting_id,
            ",".join(context_result.warning_codes),
        )

    summary_text = None
    active_template = None
    completion_client = None
    if meeting.transcript_text or visual_segments:
        style_examples = style_service.get_style_examples()

        # Resolve template: explicit override → user default → system default
        try:
            if template_id is not None:
                active_template = template_service.get_accessible(template_id, meeting.owner_id)
            elif meeting.owner_id:
                active_template = template_service.get_active_default(meeting.owner_id)
        except Exception as e:
            print(f"[{meeting_id}] Warning: could not resolve template ({e}), falling back to system default")
            try:
                active_template = template_service.get_active_default(meeting.owner_id) if meeting.owner_id else None
            except Exception:
                active_template = None

        if not _update_sub_status_for_attempt(
            meeting_id, run_id, attempt_id, "summarizing"
        ):
            raise Ignore()
        completion_client = get_completion_client(
            meeting.owner_id,
            purpose="summary",
            fallback_client=ai_agent._client,
            fallback_model=settings.OPENAI_MODEL,
        )
        summary_text = summarize_transcript(
            meeting.transcript_text or "",
            style_examples=style_examples,
            template_body=active_template.body if active_template else None,
            template_id=str(active_template.id) if active_template else None,
            upload_date=meeting.created_at.strftime("%B %d, %Y") if meeting.created_at else None,
            context=context_result,
            output_language=meeting.requested_language,
            completion_client=completion_client,
        )

    inferred_title = None
    if summary_text:
        if not _update_sub_status_for_attempt(
            meeting_id, run_id, attempt_id, "summarizing"
        ):
            raise Ignore()
        from app.services.ai_agent import infer_title

        inferred_title = infer_title(
            summary_text,
            output_language=meeting.requested_language,
            completion_client=completion_client,
        )

    saved_meeting = meeting_service.save_summary_for_attempt(
        meeting_id,
        run_id,
        attempt_id,
        summary_text,
        template_id=active_template.id if active_template else None,
        template_name=active_template.name if active_template else None,
        inferred_title=inferred_title,
    )
    if saved_meeting is None:
        raise Ignore()

    if saved_meeting.owner_id:
        def _word_count_tier(n: int) -> str:
            if n < 200:
                return "short"
            if n <= 500:
                return "medium"
            return "long"

        analytics.capture(
            saved_meeting.owner_id,
            "summary_generated",
            {
                "meeting_id": meeting_id,
                "template_id": active_template.id if active_template else None,
                "word_count_tier": _word_count_tier(len(summary_text.split()) if summary_text else 0),
            },
        )

    # Send summary email and notification only after the exact-attempt write commits.
    if saved_meeting.owner_id:
        try:
            from app.services.email import email_service
            with Session(engine) as session:
                user = session.get(User, saved_meeting.owner_id)
            if user and user.email:
                if asyncio.run(
                    meeting_processing_audit.success_effects_are_eligible(
                        meeting_id, run_id, attempt_id
                    )
                ):
                    email_service.send_summary_email(user.email, saved_meeting)
                notify(
                    "summary_generated",
                    user.email,
                    saved_meeting.title,
                    meeting_id=meeting_id,
                    processing_run_id=run_id,
                    dispatch_attempt_id=attempt_id,
                )
        except Exception:
            logger.exception("stage_summarize email lookup failed meeting_id=%s", meeting_id)

    logger.info(
        "stage_summarize complete meeting_id=%s owner_id=%s",
        meeting_id,
        saved_meeting.owner_id,
    )
    return meeting_id


# ── Stage 4: Extract Meeting Intelligence ───────────────────────────────────

@celery_app.task(name="stage_extract_intelligence")
def stage_extract_intelligence(meeting_id: int) -> int:
    """Extract intelligence, then complete only the current processing attempt."""
    logger.info("stage_extract_intelligence started meeting_id=%s", meeting_id)
    run_id, attempt_id = _required_processing_identity(stage_extract_intelligence)
    claim = meeting_processing_audit.claim_intelligence(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        task_id=_task_request_id(stage_extract_intelligence),
    )
    if claim is None:
        raise Ignore()

    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting:
        raise ValueError(f"Meeting {meeting_id} not found.")
    if meeting.owner_id is None:
        raise Ignore()
    if not meeting.transcript_text:
        logger.warning("stage_extract_intelligence: no transcript for meeting_id=%s", meeting_id)
        if not meeting_service.complete_without_intelligence_for_attempt(
            meeting_id,
            processing_run_id=run_id,
            dispatch_attempt_id=attempt_id,
            owner_id=meeting.owner_id,
        ):
            raise Ignore()
        return meeting_id

    if not meeting_service.begin_intelligence_for_attempt(
        meeting_id,
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
        owner_id=meeting.owner_id,
    ):
        raise Ignore()

    from app.services.meeting_intelligence import (
        HighlightSaveRejected,
        intelligence_service,
    )

    meeting_type = meeting.meeting_type or "generic"

    highlights_data = intelligence_service.extract_highlights(meeting.transcript_text)
    highlight_result = intelligence_service.save_highlights(
        meeting_id,
        highlights_data,
        owner_id=meeting.owner_id,
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
    )
    if highlight_result is HighlightSaveRejected.STALE_OR_CANCELLED:
        raise Ignore()

    structured_output = intelligence_service.extract_structured_output(
        meeting.transcript_text, meeting_type
    )
    if not meeting_service.complete_intelligence_for_attempt(
        meeting_id,
        structured_output,
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
        owner_id=meeting.owner_id,
    ):
        raise Ignore()

    logger.info("stage_extract_intelligence complete meeting_id=%s type=%s", meeting_id, meeting_type)

    return meeting_id


# ── Calendar sync (Celery Beat) ──────────────────────────────────────────────

@celery_app.task(name="sync_calendars", ignore_result=True)
def sync_calendars() -> None:
    import asyncio
    from collections import defaultdict

    from app.models.calendar_event import CalendarEvent
    from app.models.integration import IntegrationProvider
    from app.services.integration import integration_service
    from app.services.calendar_sync import calendar_sync_service

    integrations = integration_service.get_all_active_by_provider(IntegrationProvider.MICROSOFT)
    logger.info("sync_calendars: found %d active Microsoft integrations", len(integrations))
    if not integrations:
        return

    # Single session + one batch query across all integrations instead of
    # re-querying per integration (ZABT-API-1Z).
    with Session(engine) as session:
        integration_ids = [i.id for i in integrations]
        existing_by_integration: dict[int, list[CalendarEvent]] = defaultdict(list)
        for ev in session.exec(
            select(CalendarEvent).where(
                CalendarEvent.integration_id.in_(integration_ids)
            )
        ).all():
            existing_by_integration[ev.integration_id].append(ev)

        for integration in integrations:
            try:
                count = asyncio.run(
                    calendar_sync_service.sync_for_integration(
                        integration,
                        session=session,
                        existing_events=existing_by_integration.get(integration.id, []),
                    )
                )
                logger.info("sync_calendars: synced %d events for integration %s", count, integration.id)
            except Exception:
                logger.exception("sync_calendars: failed for integration %s", integration.id)


# ── Email share task ────────────────────────────────────────────────────────

@celery_app.task(name="send_meeting_summary_emails", ignore_result=True)
def send_meeting_summary_emails(
    meeting_id: int, user_id: int, recipient_emails: list[str]
) -> None:
    """Send meeting summary emails via Microsoft Graph (async bridge)."""
    import asyncio
    from app.services.email_share import email_share_service

    logger.info(
        "send_meeting_summary_emails meeting_id=%s user_id=%s recipients=%d",
        meeting_id, user_id, len(recipient_emails),
    )
    asyncio.run(
        email_share_service.send_to_recipients(meeting_id, user_id, recipient_emails)
    )


@celery_app.task(name="dispatch_meeting_bots", ignore_result=True)
def dispatch_meeting_bots() -> None:
    """Check for upcoming meetings with auto_join=True and dispatch bots."""
    import asyncio
    from datetime import datetime, timedelta
    from app.models.calendar_event import CalendarEvent, BotStatus
    from app.services.bot_orchestration import bot_orchestration_service

    # Use naive UTC to match DB storage (Graph API datetimes stored without tzinfo)
    now = datetime.utcnow()
    # Dispatch for meetings starting within next 2 min OR already started up to 5 min ago
    window_start = now - timedelta(minutes=5)
    window_end = now + timedelta(minutes=2)

    with Session(engine) as session:
        # Dispatch any auto_join event in the time window that isn't already
        # completed or currently being recorded. This avoids the idle/scheduled
        # status dance — failed attempts auto-retry on the next cycle.
        events = session.exec(
            select(CalendarEvent).where(
                CalendarEvent.auto_join == True,  # noqa: E712
                CalendarEvent.bot_status == BotStatus.IDLE,
                CalendarEvent.start_time >= window_start,
                CalendarEvent.start_time <= window_end,
                CalendarEvent.join_url.isnot(None),
                CalendarEvent.meeting_id.is_(None),  # not already transcribed
            )
        ).all()

        # Copy needed data before session closes (avoid DetachedInstanceError)
        event_ids = [ev.id for ev in events]

    logger.warning(
        "dispatch_meeting_bots: found %d events to dispatch (window=%s to %s)",
        len(event_ids), window_start.isoformat(), window_end.isoformat(),
    )

    for event_id in event_ids:
        try:
            # Re-load event inside dispatch_bot's own session
            with Session(engine) as session:
                event = session.get(CalendarEvent, event_id)
                if event:
                    asyncio.run(bot_orchestration_service.dispatch_bot(event))
        except Exception:
            logger.exception(
                "dispatch_meeting_bots: failed for event %s", event_id
            )


@celery_app.task(name="cleanup_abandoned_uploads", ignore_result=True)
def cleanup_abandoned_uploads() -> None:
    """Abort multipart uploads that were never completed (older than 24h).

    MinIO is always attempted; AWS S3 is attempted only when it is configured.
    Each provider gets its own cleanup pass, so a listing or abort failure on
    one provider never skips the other provider's pass.
    """
    cleanup_targets: list[tuple[str, Any]] = [("minio", storage)]
    try:
        cleanup_targets.append(("aws_s3", get_storage_for_provider("aws_s3")))
    except StorageConfigurationValidationError:
        logger.info("cleanup_abandoned_uploads: AWS S3 is not configured")
    except Exception:
        logger.warning(
            "cleanup_abandoned_uploads: AWS S3 storage resolution failed",
            exc_info=True,
        )

    for provider_name, provider_storage in cleanup_targets:
        try:
            pending = provider_storage.list_pending_multipart_uploads(older_than_hours=24)
            for entry in pending:
                provider_storage.abort_multipart_upload(
                    object_key=entry["Key"],
                    upload_id=entry["UploadId"],
                )
                logger.info(
                    "Aborted abandoned upload: provider=%s key=%s upload_id=%s initiated=%s",
                    provider_name, entry["Key"], entry["UploadId"], entry["Initiated"],
                )
        except Exception:
            logger.warning(
                "cleanup_abandoned_uploads failed provider=%s",
                provider_name,
                exc_info=True,
            )


# ── Stage 5: Optional Visual Breakdown ───────────────────────────────────────


def _sanitize_visual_error(error: object) -> str:
    """Keep provider failures bounded and free of URLs, tokens, and payloads."""
    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}"
    else:
        message = str(error)
    message = re.sub(r"https?://\S+", "<redacted-url>", message)
    message = re.sub(
        r"(?i)(token|secret|password|api[_-]?key)=\S+",
        r"\1=<redacted>",
        message,
    )
    message = re.sub(r"\s+", " ", message).strip()
    return message[:200] or "visual processing failed"


def _acquire_visual_lease(meeting_id: int, run_epoch: str) -> bool:
    """Acquire a Redis lease; a Redis outage falls back to the DB row lock."""
    key = f"visual-breakdown:{meeting_id}:{run_epoch}"
    ttl = max(int(getattr(settings, "VISION_TIMEOUT", 1800)) + 300, 300)
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL)
        acquired = client.set(key, run_epoch, nx=True, ex=ttl)
        try:
            client.close()
        except Exception:
            pass
        # redis-py returns None when SET NX loses the lease; only a truthy
        # acknowledgement means this worker owns the epoch.
        return bool(acquired)
    except Exception:
        logger.warning("visual lease unavailable; relying on database convergence", exc_info=True)
        return True


def _safe_visual_params(result) -> dict:
    """Persist only bounded configuration/metric values returned by the worker."""
    allowed = {
        "fps",
        "phash_threshold",
        "ocr_diff_threshold",
        "ensemble_min_signals",
        "confidence_threshold",
        "max_frames",
        "candidate_count",
        "change_threshold",
        "skip_reason",
        "warning_code",
    }
    params = getattr(result, "params", {}) or {}
    return {
        key: value
        for key, value in params.items()
        if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
    }


def _emit_visual_side_effects(
    finalization: dict,
    result=None,
    *,
    processing_run_id: int | None = None,
    dispatch_attempt_id: str | None = None,
) -> None:
    """Emit only the side effects claimed by the atomic finalization."""
    if not finalization.get("applied") or finalization.get("owner_id") is None:
        return

    owner_id = finalization["owner_id"]
    meeting_id = finalization["meeting_id"]
    status = finalization.get("status")
    if finalization.get("completion_event"):
        metrics = getattr(result, "stage_metrics", {}) if result is not None else {}
        total_duration_ms = sum(
            value.get("duration_ms", 0)
            for value in metrics.values()
            if isinstance(value, dict)
        )
        analytics.capture(
            owner_id,
            "visual_breakdown_completed",
            {
                "meeting_id": meeting_id,
                "segment_count": finalization.get("segment_count", 0),
                "model": getattr(result, "model", None),
                "total_duration_ms": total_duration_ms,
            },
        )
        for stage_name, stage_metrics in metrics.items():
            if isinstance(stage_metrics, dict):
                analytics.capture(
                    owner_id,
                    "visual_breakdown_stage_completed",
                    {"meeting_id": meeting_id, "stage": stage_name, **stage_metrics},
                )

        try:
            with Session(engine) as session:
                user_obj = session.get(User, owner_id)
            identity = (
                {
                    "processing_run_id": processing_run_id,
                    "dispatch_attempt_id": dispatch_attempt_id,
                }
                if processing_run_id is not None
                else {}
            )
            notify(
                "visual_breakdown_completed",
                (user_obj.email if user_obj else None) or str(owner_id),
                finalization.get("title"),
                meeting_id=meeting_id,
                extra={"segment_count": str(finalization.get("segment_count", 0))},
                **identity,
            )
        except Exception:
            logger.warning("visual breakdown notification failed", exc_info=True)
    elif finalization.get("warning_event"):
        analytics.capture(
            owner_id,
            "visual_breakdown_warning",
            {
                "meeting_id": meeting_id,
                "status": status,
                "warning_code": finalization.get("warning_code"),
            },
        )


def _cleanup_visual_result_artifacts(
    result,
    *,
    owner_id,
    meeting_id: int,
    visual_epoch: str | int,
    processing_run_id: int | None,
    dispatch_attempt_id: str | None,
    storage_client=None,
) -> None:
    """Delete only artifacts in the namespace returned by this rejected run."""
    if (
        not isinstance(owner_id, int)
        or isinstance(owner_id, bool)
        or owner_id <= 0
        or not isinstance(meeting_id, int)
        or isinstance(meeting_id, bool)
        or meeting_id <= 0
    ):
        return

    pipeline_identity_present = (
        processing_run_id is not None or dispatch_attempt_id is not None
    )
    if pipeline_identity_present and (
        not isinstance(processing_run_id, int)
        or isinstance(processing_run_id, bool)
        or processing_run_id <= 0
        or not isinstance(dispatch_attempt_id, str)
        or not dispatch_attempt_id
    ):
        return

    raw_key = getattr(result, "raw_output_s3_key", None)
    if not isinstance(raw_key, str):
        return

    owner = str(owner_id)
    meeting = str(meeting_id)
    epoch = str(visual_epoch)
    if not re.fullmatch(r"(?:[0-9]+|[0-9a-f]{32})", epoch):
        return
    epoch_prefix = f"users/{owner}/meetings/{meeting}/visual/epoch-{epoch}"

    if pipeline_identity_present:
        attempt_hash = sha256(str(dispatch_attempt_id).encode("utf-8")).hexdigest()
        run_id = str(processing_run_id)
        namespace = f"{epoch_prefix}/run-{run_id}/attempt-{attempt_hash}"
    else:
        namespace = raw_key.rsplit("/", 1)[0]
        if not re.fullmatch(re.escape(epoch_prefix) + r"/standalone-[0-9a-f]{32}", namespace):
            return

    if raw_key != f"{namespace}/raw_output.json":
        return

    keys = {raw_key}
    for segment in getattr(result, "segments", ()) or ():
        key = getattr(segment, "screenshot_s3_key", None)
        if isinstance(key, str) and re.fullmatch(
            re.escape(namespace) + r"/[0-9a-f]{32}\.jpg", key
        ):
            keys.add(key)
    artifact_storage = storage if storage_client is None else storage_client
    for key in keys:
        try:
            artifact_storage.delete_file(key)
        except Exception:
            logger.warning("rejected visual artifact cleanup failed", exc_info=True)


def _run_visual_breakdown(meeting_id: int, *, optional: bool) -> int:
    task = stage_optional_visual_breakdown if optional else stage_visual_breakdown
    processing_run_id, dispatch_attempt_id = _visual_processing_identity(
        task, allow_standalone=not optional
    )
    pipeline_mode = processing_run_id is not None
    visual_only_run = not optional and pipeline_mode
    storage_client = None

    logger.info(
        "%s started meeting_id=%s",
        "stage_optional_visual_breakdown" if optional else "stage_visual_breakdown",
        meeting_id,
    )
    run_epoch, should_process = meeting_service.begin_visual_breakdown(
        meeting_id,
        processing_run_id=processing_run_id,
        dispatch_attempt_id=dispatch_attempt_id,
    )
    if not run_epoch:
        if pipeline_mode:
            raise Ignore()
        return meeting_id
    if not should_process:
        if visual_only_run:
            raise Ignore()
        return meeting_id
    if not _acquire_visual_lease(meeting_id, run_epoch):
        if visual_only_run:
            raise Ignore()
        return meeting_id

    def update_progress(sub_status: str | None = None) -> None:
        if not meeting_service.update_visual_breakdown_progress(
            meeting_id,
            run_epoch,
            processing_run_id=processing_run_id,
            dispatch_attempt_id=dispatch_attempt_id,
            sub_status=sub_status,
        ):
            raise Ignore()

    def finalize(**kwargs) -> dict:
        result = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            processing_run_id=processing_run_id,
            dispatch_attempt_id=dispatch_attempt_id,
            **kwargs,
        )
        if pipeline_mode and not result.get("applied"):
            raise Ignore()
        return result

    def cleanup_result_artifacts(result) -> None:
        _cleanup_visual_result_artifacts(
            result,
            owner_id=owner_id,
            meeting_id=meeting_id,
            visual_epoch=run_epoch,
            processing_run_id=processing_run_id,
            dispatch_attempt_id=dispatch_attempt_id,
            storage_client=storage_client,
        )

    def finalize_result(result, **kwargs) -> dict:
        try:
            finalization = finalize(**kwargs)
        except Ignore:
            cleanup_result_artifacts(result)
            raise
        if not finalization.get("applied"):
            cleanup_result_artifacts(result)
        return finalization

    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            raise RuntimeError(f"Meeting {meeting_id} not found")
        owner_id = meeting.owner_id
        file_path = meeting.file_path
        source_type = meeting.source_type
        storage_provider = meeting.storage_provider
        stored_visual_params = dict(meeting.visual_breakdown_params or {})
        transcript = session.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting_id)
            .order_by(TranscriptSegment.start_time)
        ).all()
        transcript_payload = [
            {
                "speaker": item.speaker or "SPEAKER_00",
                "text": item.text,
                "start": item.start_time,
                "end": item.end_time,
            }
            for item in transcript
        ]

    if optional and not settings.VISION_ENABLED:
        finalization = finalize(
            outcome="skipped",
            reason="vision_disabled",
            warning_code="visual_disabled",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    if not file_path:
        finalization = finalize(
            outcome="skipped",
            reason="no_media_file",
            warning_code="no_video_file",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    if source_type == "youtube":
        finalization = finalize(
            outcome="skipped",
            reason="youtube_audio_only",
            warning_code="youtube_audio_only",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    storage_client = get_storage_for_provider(storage_provider)
    update_progress("analyzing_video")
    attempts = meeting_service.increment_visual_breakdown_attempt(
        meeting_id,
        run_epoch,
        processing_run_id=processing_run_id,
        dispatch_attempt_id=dispatch_attempt_id,
    )
    if pipeline_mode and attempts is None:
        raise Ignore()

    try:
        update_progress()
        result = DirectVisionService(storage_provider=storage_client).submit_and_wait(
            {
                "file_path": file_path,
                "owner_id": str(owner_id) if owner_id is not None else "unknown",
                "meeting_id": str(meeting_id),
                "visual_epoch": run_epoch,
                "processing_run_id": processing_run_id,
                "dispatch_attempt_id": dispatch_attempt_id,
                "source_type": source_type,
                "transcript": transcript_payload,
                "params": {
                    key: stored_visual_params[key]
                    for key in ("media_type", "mime_type", "content_type", "media_kind")
                    if key in stored_visual_params
                },
            },
            on_heartbeat=update_progress,
        )
    except Ignore:
        raise
    except Exception as error:
        if visual_only_run:
            raise RuntimeError(_sanitize_visual_error(error)) from error
        update_progress()
        logger.warning("visual worker failed meeting_id=%s error=%s", meeting_id, _sanitize_visual_error(error))
        finalization = finalize(
            outcome="fallback",
            reason="vision_worker_error",
            warning_code="vision_worker_error",
            result_params={"warning_code": "vision_worker_error"},
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    try:
        update_progress()
    except Ignore:
        cleanup_result_artifacts(result)
        raise

    if result.status != "completed":
        if visual_only_run:
            cleanup_result_artifacts(result)
            raise RuntimeError(
                _sanitize_visual_error(
                    getattr(result, "error", None) or "Visual analysis failed"
                )
            )
        finalization = finalize_result(
            result,
            outcome="fallback",
            reason="vision_worker_failed",
            warning_code="vision_worker_failed",
            result_params={"warning_code": "vision_worker_failed"},
        )
        _emit_visual_side_effects(finalization, result)
        return meeting_id

    if not result.segments:
        skip_reason = str(getattr(result, "params", {}).get("skip_reason") or "no_relevant_visual")
        finalization = finalize_result(
            result,
            outcome="skipped",
            reason=skip_reason,
            warning_code=skip_reason,
            result_params={"skip_reason": skip_reason},
        )
        _emit_visual_side_effects(finalization, result)
        return meeting_id

    worker_segments = [
        VisualSegment(
            meeting_id=meeting_id,
            sequence=segment.sequence,
            start_time=segment.start_time,
            end_time=segment.end_time,
            screenshot_s3_key=segment.screenshot_s3_key,
            caption=segment.caption,
            confidence=segment.confidence,
        )
        for segment in result.segments
    ]
    finalization = finalize_result(
        result,
        outcome="completed",
        result_params=_safe_visual_params(result),
        raw_output_s3_key=result.raw_output_s3_key,
        model=result.model,
        segments=worker_segments,
    )
    _emit_visual_side_effects(
        finalization,
        result,
        processing_run_id=processing_run_id,
        dispatch_attempt_id=dispatch_attempt_id,
    )
    logger.info(
        "visual breakdown done meeting_id=%s segments=%s",
        meeting_id,
        len(result.segments),
    )
    return meeting_id


@celery_app.task(name="stage_optional_visual_breakdown")
def stage_optional_visual_breakdown(meeting_id: int) -> int:
    """Run optional visual processing and always return the stable meeting ID."""
    return _run_visual_breakdown(meeting_id, optional=True)


@celery_app.task(name="stage_visual_breakdown")
def stage_visual_breakdown(meeting_id: int) -> int:
    """Run an explicit visual breakdown and return only the stable meeting ID."""
    return _run_visual_breakdown(meeting_id, optional=False)


@celery_app.task(name="finalize_recovered_meeting")
def finalize_recovered_meeting(meeting_id: int) -> int:
    """Finalize a meeting whose durable intelligence already completed."""
    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting:
        raise RuntimeError(f"Meeting {meeting_id} not found")
    if (
        meeting.structured_output_status != "completed"
        or meeting.structured_output is None
    ):
        raise RuntimeError("Recovery finalization requires completed structured output")
    run_id = _audit_run_id_from_task(finalize_recovered_meeting)
    attempt_id = _audit_attempt_id_from_task(finalize_recovered_meeting)
    if run_id is None or attempt_id is None:
        raise RuntimeError("Recovery finalization requires a current processing identity")
    if not meeting_service.mark_completed_for_processing_run(
        meeting_id, processing_run_id=run_id, dispatch_attempt_id=attempt_id
    ):
        raise RuntimeError("Recovery finalization was rejected for the current processing attempt")
    return meeting_id


_RECOVERY_CLAIM_TTL_SECONDS = 120


def _acquire_meeting_recovery_claim(meeting_id: int) -> bool:
    """Acquire a short Redis claim; DB row locking remains the source of truth."""
    key = f"meeting-recovery:{meeting_id}"
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL)
        acquired = client.set(
            key,
            "1",
            nx=True,
            ex=_RECOVERY_CLAIM_TTL_SECONDS,
        )
        try:
            client.close()
        except Exception:
            pass
        return bool(acquired)
    except Exception:
        logger.warning(
            "meeting recovery claim unavailable; relying on database row lock",
            exc_info=True,
        )
        return True


def _dispatch_recovery_stages(meeting_id: int, stages: list) -> None:
    """Dispatch a linked chain whose first stage owns the meeting ID."""
    if not stages:
        return

    run = meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="recovery")
    run_id = getattr(run, "id", None)
    attempt_id = getattr(run, "dispatch_attempt_id", None)
    if run_id is None or not attempt_id:
        return
    result = chain(*_build_pipeline_signatures(meeting_id, stages, run_id, attempt_id)).apply_async()
    meeting_processing_audit.set_root_task(
        run_id, getattr(result, "id", None), attempt_id=attempt_id
    )


def _dispatch_recovery_plan(meeting_id: int, plan: MeetingRecoveryPlan) -> None:
    """Apply one durable-output recovery plan after its row claim commits."""
    stage_by_name = {
        "stage_transliterate": stage_transliterate,
        "stage_optional_visual_breakdown": stage_optional_visual_breakdown,
        "stage_summarize": stage_summarize,
        "stage_extract_intelligence": stage_extract_intelligence,
        "finalize_recovered_meeting": finalize_recovered_meeting,
    }

    def dispatch_stages(mid: int, stage_names: tuple[str, ...]) -> None:
        _dispatch_recovery_stages(mid, [stage_by_name[name] for name in stage_names])

    dispatch_recovery_plan(
        meeting_id,
        plan,
        dispatch_full_pipeline=dispatch_pipeline,
        dispatch_youtube_pipeline=dispatch_youtube_pipeline,
        dispatch_stages=dispatch_stages,
        finalize=lambda mid: _dispatch_recovery_stages(
            mid, [finalize_recovered_meeting]
        ),
    )


@celery_app.task(name="recover_stale_meetings", ignore_result=True)
def recover_stale_meetings() -> int:
    """Recover queued/processing meetings orphaned by a host or worker restart."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=settings.MEETING_RECOVERY_GRACE_SECONDS)
    claimed: list[tuple[int, MeetingRecoveryPlan]] = []

    with Session(engine) as session:
        statement = (
            select(Meeting)
            .where(Meeting.status.in_(["queued", "processing"]))
            .where(
                func.coalesce(
                    Meeting.processing_heartbeat_at,
                    Meeting.created_at,
                )
                < cutoff
            )
            .order_by(Meeting.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )

        for meeting in session.exec(statement).all():
            if meeting.id is None:
                continue
            # Keep the helper defensive for mocked sessions and for databases
            # whose snapshot was taken just before the cutoff moved.
            plan = claim_recovery_plan(
                meeting,
                now=now,
                grace_seconds=settings.MEETING_RECOVERY_GRACE_SECONDS,
                acquire_claim=lambda: _acquire_meeting_recovery_claim(meeting.id),
            )
            if plan is None:
                continue
            if plan.reset_visual_epoch:
                meeting_service.reset_stale_visual_breakdown_in_session(session, meeting)

            # This committed refresh is the DB-side atomic claim. A second
            # Beat instance skips the locked row and later sees it as fresh.
            meeting.processing_heartbeat_at = now
            session.add(meeting)
            claimed.append((meeting.id, plan))

        if claimed:
            session.commit()

    recovered = 0
    for meeting_id, plan in claimed:
        try:
            _dispatch_recovery_plan(meeting_id, plan)
            recovered += 1
        except Exception:
            logger.exception(
                "Failed to dispatch recovery plan meeting_id=%s action=%s",
                meeting_id,
                plan.action,
            )
    return recovered


celery_app.conf.beat_schedule = {
    "sync-calendars-every-5-min": {
        "task": "sync_calendars",
        "schedule": 300.0,
    },
    "dispatch-bots-every-minute": {
        "task": "dispatch_meeting_bots",
        "schedule": 60.0,
    },
    "recover-stale-meetings-every-minute": {
        "task": "recover_stale_meetings",
        "schedule": 60.0,
    },
    "cleanup-abandoned-uploads-daily": {
        "task": "cleanup_abandoned_uploads",
        "schedule": 86400.0,
    },
}
celery_app.conf.timezone = "UTC"


# ── Embedding indexing lifecycle ─────────────────────────────────────────────

_EMBEDDING_RETRY_EXCEPTIONS = (RuntimeError, TimeoutError, ConnectionError)


def _capture_embedding_event(owner_id: int | None, event_name: str, properties: dict) -> None:
    """Best-effort operational telemetry without indexed text or credentials."""
    if owner_id is None:
        return
    try:
        analytics.capture(owner_id, event_name, properties)
    except Exception:
        logger.warning("embedding telemetry capture failed event=%s", event_name, exc_info=True)


def _get_group_introduction(group_id: int) -> str | None:
    with Session(engine) as session:
        return session.exec(
            select(Group.description).where(Group.id == group_id)
        ).first()


def _get_locked_meeting(session: Session, meeting_id: int) -> Meeting | None:
    """Read the assignment under the same row lock used by assignment writes."""
    statement = (
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
    )
    return session.exec(statement).first()


def _requeue_stale_meeting_index(
    meeting_id: int,
    snapshot: Meeting,
    current: Meeting,
) -> None:
    """Discard a stale snapshot and make the current assignment converge."""
    logger.warning(
        "discarding stale meeting embedding meeting_id=%s "
        "snapshot_owner_id=%s current_owner_id=%s snapshot_group_id=%s current_group_id=%s",
        meeting_id,
        snapshot.owner_id,
        current.owner_id,
        snapshot.group_id,
        current.group_id,
    )
    _capture_embedding_event(
        snapshot.owner_id,
        "embedding_index_stale",
        {"meeting_id": meeting_id, "group_id": current.group_id},
    )
    try:
        # Reindex, rather than only stage_embedding, also removes vectors from a
        # previous group when the current assignment is ungrouped or changed.
        reindex_meeting.delay(meeting_id)
    except Exception as exc:
        logger.exception(
            "fresh meeting reindex enqueue failed meeting_id=%s group_id=%s",
            meeting_id,
            current.group_id,
        )
        raise RuntimeError(
            f"Fresh reindex could not be queued for meeting {meeting_id}."
        ) from exc


@celery_app.task(
    name="stage_embedding",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def stage_embedding(meeting_id: int) -> int:
    """Index available meeting content, whether or not it has a group yet."""
    if not settings.INDEXING_ENABLED:
        logger.info("stage_embedding skipped disabled meeting_id=%s", meeting_id)
        return meeting_id

    run_id, attempt_id = _required_processing_identity(stage_embedding)
    with Session(engine) as session:
        meeting, _run = meeting_processing_audit._locked_current_run(
            session, run_id, meeting_id, attempt_id
        )
        visual_segments = (
            list(getattr(meeting, "visual_segments", []) or [])
            if meeting is not None
            else []
        )
    if meeting is None:
        raise Ignore()
    if not meeting.owner_id:
        logger.warning("stage_embedding skipped missing owner meeting_id=%s", meeting_id)
        return meeting_id

    logger.info(
        "stage_embedding started meeting_id=%s owner_id=%s group_id=%s",
        meeting_id,
        meeting.owner_id,
        meeting.group_id,
    )
    _capture_embedding_event(
        meeting.owner_id,
        "embedding_index_started",
        {"meeting_id": meeting_id, "group_id": meeting.group_id},
    )

    try:
        group_introduction = (
            _get_group_introduction(meeting.group_id)
            if meeting.group_id is not None
            else None
        )
        chunks = build_meeting_chunks(
            meeting_id=meeting.id,
            transcript_text=meeting.transcript_text,
            transliterated_text=meeting.transliterated_text,
            summary_text=meeting.summary_text,
            original_summary_text=meeting.original_summary_text,
            structured_output=meeting.structured_output,
            structured_output_status=meeting.structured_output_status,
            visual_segments=visual_segments,
            group_introduction=group_introduction,
        )
        points: list[EmbeddingPoint] = []
        if chunks:
            provider = get_embedding_provider()
            vectors = provider.embed(
                [chunk.embedding_text or chunk.text for chunk in chunks]
            )
            points = [
                EmbeddingPoint(
                    id=chunk.id,
                    vector=vector,
                    text=chunk.text,
                    owner_id=meeting.owner_id,
                    group_id=meeting.group_id,
                    meeting_id=meeting.id,
                    kind=chunk.kind,
                    chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count,
                    source_type=meeting.source_type or "upload",
                    model=settings.EMBEDDING_MODEL,
                )
                for chunk, vector in zip(chunks, vectors)
            ]

        # Revalidate and hold Meeting-then-Run locks through vector mutation.
        # Embedding-provider calls above run without database locks.
        stale_assignment: Meeting | None = None
        with Session(engine) as session:
            current, _run = meeting_processing_audit._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if current is None:
                raise Ignore()
            if (
                current.owner_id != meeting.owner_id
                or current.group_id != meeting.group_id
            ):
                stale_assignment = current
            else:
                vector_store = get_vector_store()
                delete_by_filter = getattr(vector_store, "delete_by_filter", None)
                if delete_by_filter is not None:
                    delete_by_filter(
                        owner_id=current.owner_id,
                        group_id=current.group_id,
                        meeting_id=current.id,
                    )
                if points:
                    vector_store.upsert_points(points)
        if stale_assignment is not None:
            _requeue_stale_meeting_index(meeting_id, meeting, stale_assignment)
            return meeting_id
    except Ignore:
        raise
    except Exception:
        logger.exception(
            "stage_embedding failed meeting_id=%s owner_id=%s group_id=%s",
            meeting_id,
            meeting.owner_id,
            meeting.group_id,
        )
        _capture_embedding_event(
            meeting.owner_id,
            "embedding_index_failed",
            {"meeting_id": meeting_id, "group_id": meeting.group_id},
        )
        raise

    logger.info("stage_embedding complete meeting_id=%s points=%s", meeting_id, len(points))
    _capture_embedding_event(
        meeting.owner_id,
        "embedding_index_completed",
        {"meeting_id": meeting_id, "group_id": meeting.group_id, "point_count": len(points)},
    )
    return meeting_id


@celery_app.task(
    name="delete_meeting_vectors",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def delete_meeting_vectors(meeting_id: int) -> int:
    """Delete all vectors tagged with a meeting id."""
    if not settings.INDEXING_ENABLED:
        return meeting_id
    owner_id = None
    group_id = None
    try:
        # Re-read owner and assignment under the assignment row lock before
        # deleting. Assignment writes use the same lock, preventing cleanup
        # from racing a reassignment commit.
        with Session(engine) as session:
            meeting = _get_locked_meeting(session, meeting_id)
            owner_id = getattr(meeting, "owner_id", None)
            group_id = getattr(meeting, "group_id", None)
            logger.info(
                "delete_meeting_vectors started meeting_id=%s owner_id=%s group_id=%s",
                meeting_id,
                owner_id,
                group_id,
            )
            if meeting and meeting.owner_id:
                get_vector_store().delete_by_filter(owner_id=meeting.owner_id, meeting_id=meeting_id)
            else:
                get_vector_store().delete_by_filter(meeting_id=meeting_id)
    except Exception:
        logger.exception("delete_meeting_vectors failed meeting_id=%s owner_id=%s", meeting_id, owner_id)
        _capture_embedding_event(owner_id, "embedding_delete_failed", {"meeting_id": meeting_id, "group_id": group_id})
        raise
    logger.info("delete_meeting_vectors complete meeting_id=%s", meeting_id)
    _capture_embedding_event(owner_id, "embedding_delete_completed", {"meeting_id": meeting_id, "group_id": group_id})
    return meeting_id


@celery_app.task(
    name="reindex_meeting",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def reindex_meeting(meeting_id: int) -> int:
    """Delete a meeting's vectors, then index its current group assignment."""
    if not settings.INDEXING_ENABLED:
        return meeting_id

    # Keep deletion and indexing in one task so an old assignment cleanup can
    # never be delivered after the new assignment's upsert.
    delete_meeting_vectors.run(meeting_id)
    meeting = meeting_service.get(Meeting, meeting_id)
    if meeting and meeting.group_id:
        stage_embedding.run(meeting_id)
    logger.info(
        "reindex_meeting complete meeting_id=%s grouped=%s",
        meeting_id,
        bool(meeting and meeting.group_id),
    )
    return meeting_id


@celery_app.task(
    name="delete_group_vectors",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def delete_group_vectors(group_id: int) -> int:
    """Delete all vectors tagged with a group id."""
    if not settings.INDEXING_ENABLED:
        return group_id
    with Session(engine) as session:
        from app.models import Group

        group = session.get(Group, group_id)
        owner_id = getattr(group, "owner_id", None)
    logger.info("delete_group_vectors started group_id=%s owner_id=%s", group_id, owner_id)
    try:
        get_vector_store().delete_by_filter(group_id=group_id)
    except Exception:
        logger.exception("delete_group_vectors failed group_id=%s owner_id=%s", group_id, owner_id)
        _capture_embedding_event(owner_id, "embedding_group_delete_failed", {"group_id": group_id})
        raise
    logger.info("delete_group_vectors complete group_id=%s", group_id)
    _capture_embedding_event(owner_id, "embedding_group_delete_completed", {"group_id": group_id})
    return group_id


@celery_app.task(
    name="reindex_group",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def reindex_group(group_id: int) -> int:
    """Re-run indexing for all meetings currently assigned to a group."""
    if not settings.INDEXING_ENABLED:
        return group_id
    with Session(engine) as session:
        from app.models import Group

        group = session.get(Group, group_id)
        owner_id = getattr(group, "owner_id", None)
        meeting_ids = session.exec(select(Meeting.id).where(Meeting.group_id == group_id)).all()
    logger.info(
        "reindex_group started group_id=%s owner_id=%s meeting_count=%s",
        group_id,
        owner_id,
        len(meeting_ids),
    )
    _capture_embedding_event(
        owner_id,
        "embedding_reindex_started",
        {"group_id": group_id, "meeting_count": len(meeting_ids)},
    )
    try:
        for meeting_id in meeting_ids:
            stage_embedding.delay(meeting_id)
    except Exception:
        logger.exception("reindex_group failed group_id=%s owner_id=%s", group_id, owner_id)
        _capture_embedding_event(owner_id, "embedding_reindex_failed", {"group_id": group_id})
        raise
    logger.info("reindex_group complete group_id=%s enqueued=%s", group_id, len(meeting_ids))
    _capture_embedding_event(
        owner_id,
        "embedding_reindex_completed",
        {"group_id": group_id, "meeting_count": len(meeting_ids)},
    )
    return group_id


# ── Pipeline dispatch helper ─────────────────────────────────────────────────

def dispatch_pipeline(meeting_id: int):
    """Build and dispatch the Celery chain for processing a meeting."""
    run = meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="pipeline")
    run_id = getattr(run, "id", None)
    attempt_id = getattr(run, "dispatch_attempt_id", None)
    if run_id is None or not attempt_id:
        return
    stages = [
        stage_download,
        stage_transcribe,
        stage_transliterate,
        stage_optional_visual_breakdown,
        stage_summarize,
        stage_extract_intelligence,
        stage_embedding,
    ]
    result = chain(*_build_pipeline_signatures(meeting_id, stages, run_id, attempt_id)).apply_async()
    meeting_processing_audit.set_root_task(
        run_id, getattr(result, "id", None), attempt_id=attempt_id
    )


# ── Stage 0 (YouTube): Download from YouTube ────────────────────────────────

@celery_app.task(name="stage_youtube_download")
def stage_youtube_download(meeting_id: int) -> int:
    """Download audio from YouTube, extract metadata, store in object storage.

    This replaces stage_download for YouTube-sourced meetings. After completion,
    the existing stage_transcribe → stage_summarize chain runs unchanged.
    """
    from app.services.youtube import (
        extract_metadata,
        download_audio,
        YouTubeError,
        DurationExceededError,
    )

    logger.info("stage_youtube_download started meeting_id=%s", meeting_id)
    run_id, attempt_id = _required_processing_identity(stage_youtube_download)
    if meeting_processing_audit.claim_youtube_download(
        run_id=run_id,
        attempt_id=attempt_id,
        meeting_id=meeting_id,
        task_id=_task_request_id(stage_youtube_download),
    ) is None:
        raise Ignore()

    attempt_work_dir: str | None = None
    handoff_path: str | None = None
    file_key: str | None = None
    object_write_started = False
    promoted = False
    storage_client = None
    try:
        if not _update_sub_status_for_attempt(
            meeting_id, run_id, attempt_id, "downloading_youtube"
        ):
            raise Ignore()
        meeting = meeting_service.get(Meeting, meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found.")
        if not meeting.source_url:
            raise ValueError("No YouTube URL associated with meeting.")
        if meeting.owner_id is None:
            raise ValueError("YouTube meeting has no owner.")
        storage_client = get_storage_for_provider(meeting.storage_provider)
        source_url = meeting.source_url
        owner_id = meeting.owner_id

        # Step 1: Extract metadata (title, duration, thumbnail, channel)
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()
        metadata = extract_metadata(source_url)
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()

        # Step 2: Validate duration (max 4 hours = 14400 seconds)
        if metadata["duration"] > 14400:
            raise DurationExceededError(
                "Video exceeds the maximum duration of 4 hours."
            )

        # Update metadata only while the exact run and attempt remain current.
        with Session(engine) as session:
            m, _run = meeting_processing_audit._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if m is None or m.owner_id != owner_id:
                raise Ignore()
            m.title = metadata["title"]
            m.youtube_title = metadata["title"]
            m.youtube_duration_seconds = metadata["duration"]
            m.youtube_thumbnail_url = metadata["thumbnail"]
            m.youtube_channel = metadata["channel"]
            session.add(m)
            session.commit()

        # Step 3: Download to a local path unique to this meeting attempt.
        video_id = metadata["id"]
        attempt_video_id, file_key = _youtube_attempt_artifacts(
            owner_id, meeting_id, run_id, attempt_id, video_id
        )
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()
        attempt_work_dir = _youtube_attempt_temp_dir(meeting_id, run_id, attempt_id)
        os.makedirs(attempt_work_dir, exist_ok=True)
        audio_path = download_audio(source_url, attempt_work_dir, attempt_video_id)

        # Step 4: Upload under an immutable key unique to this meeting attempt.
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()
        object_write_started = True
        with open(audio_path, "rb") as f:
            storage_client.upload_file(f.read(), file_key, "audio/mpeg")
        if not _update_sub_status_for_attempt(meeting_id, run_id, attempt_id, None):
            raise Ignore()

        # Match stage_transcribe's attempt path derived from the promoted key.
        handoff_path = _temp_path_for(
            meeting_id, file_key, run_id=run_id, attempt_id=attempt_id
        )
        os.replace(audio_path, handoff_path)
        if not meeting_processing_audit.promote_youtube_audio(
            run_id=run_id,
            attempt_id=attempt_id,
            meeting_id=meeting_id,
            owner_id=owner_id,
            file_key=file_key,
        ):
            raise Ignore()
        promoted = True

        logger.info(
            "stage_youtube_download complete meeting_id=%s video_id=%s duration=%s",
            meeting_id,
            video_id,
            metadata["duration"],
        )

        if owner_id:
            analytics.capture(
                owner_id,
                "youtube_download_completed",
                {
                    "meeting_id": meeting_id,
                    "duration_seconds": metadata["duration"],
                    "video_id": video_id,
                },
            )

    except Ignore:
        raise
    except YouTubeError:
        raise  # Let link_error handler catch it with the descriptive message
    except Ignore:
        raise
    except Exception:
        logger.exception("stage_youtube_download failed meeting_id=%s", meeting_id)
        raise
    finally:
        _cleanup_youtube_attempt_artifacts(
            work_dir=attempt_work_dir,
            handoff_path=handoff_path,
            file_key=file_key,
            object_write_started=object_write_started,
            promoted=promoted,
            meeting_id=meeting_id,
            run_id=run_id,
            attempt_id=attempt_id,
            storage_client=storage_client,
        )

    return meeting_id


def dispatch_youtube_pipeline(meeting_id: int):
    """Build and dispatch the Celery chain for YouTube ingestion.

    Uses stage_youtube_download instead of stage_download, then feeds into the
    same optional-visual → summary → transcript-intelligence chain.  The
    optional stage short-circuits YouTube audio without downloading media twice.
    """
    run = meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="youtube")
    run_id = getattr(run, "id", None)
    attempt_id = getattr(run, "dispatch_attempt_id", None)
    if run_id is None or not attempt_id:
        return
    stages = [
        stage_youtube_download,
        stage_transcribe,
        stage_transliterate,
        stage_optional_visual_breakdown,
        stage_summarize,
        stage_extract_intelligence,
        stage_embedding,
    ]
    result = chain(*_build_pipeline_signatures(meeting_id, stages, run_id, attempt_id)).apply_async()
    meeting_processing_audit.set_root_task(
        run_id, getattr(result, "id", None), attempt_id=attempt_id
    )
