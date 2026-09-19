# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import os
import re
import urllib.request
from datetime import datetime, timedelta
from math import ceil

from celery import Celery, chain
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
    Meeting,
    TranscriptSegment,
    TranscriptionType,
    User,
    VisualSegment,
)
from app.services.meeting import meeting_service
from app.services.storage import storage
from app.services.transcription import (
    BatchTranscriptionRequest,
    TimestampMode,
    build_config,
    get_provider,
)
from app.services.transcription.contracts import ProviderName
from app.services.transcription.source import AudioSourceResolver
from app.services.styles import style_service
from app.services.ai_agent import summarize_transcript
from app.services.template import template_service
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
    provider.send(event)

# ── Shared temp directory for passing file paths between stages ──────────────
# Must be a volume shared across all worker replicas — /tmp is per-container.
TEMP_DIR = os.environ.get("TEMP_DIR", "/media/tmp")
os.makedirs(TEMP_DIR, exist_ok=True)


def _configured_transcription_provider() -> str:
    value = getattr(settings, "TRANSCRIPTION_PROVIDER", None) or getattr(settings, "TRANSCRIPTION_BACKEND", None)
    return value.value if hasattr(value, "value") else str(value or ProviderName.GPU_LOCAL.value)


def _temp_path_for(meeting_id: int, source_key: str | None = None) -> str:
    """Return a stable temp path while preserving the source file extension."""
    suffix = os.path.splitext(source_key or "")[1] or ".audio"
    return os.path.join(TEMP_DIR, f"zabt_meeting_{meeting_id}{suffix}")


_AUDITED_STAGE_TASKS = {
    "stage_download",
    "stage_youtube_download",
    "stage_transcribe",
    "stage_transliterate",
    "stage_optional_visual_breakdown",
    "stage_summarize",
    "stage_extract_intelligence",
    "stage_embedding",
}


def _audit_headers(run_id: int | None, meeting_id: int, stage: str) -> dict[str, str]:
    headers = {"meeting_id": str(meeting_id), "stage": stage}
    if run_id is not None:
        headers["meeting_processing_run_id"] = str(run_id)
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


def _meeting_id_from_task_args(args) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _build_pipeline_signatures(meeting_id: int, stages: list, run_id: int | None):
    signatures = []
    for index, stage in enumerate(stages):
        stage_name = stage.name
        signature = stage.s(meeting_id) if index == 0 else stage.s()
        link_errors = [] if stage_name == "stage_embedding" else [on_stage_failure.s()]
        signatures.append(
            signature.set(
                headers=_audit_headers(run_id, meeting_id, stage_name),
                link_error=link_errors,
            )
        )
    return signatures


@task_prerun.connect
def _record_audit_stage_started(task_id=None, task=None, args=None, **kwargs):
    stage = getattr(task, "name", None)
    if stage not in _AUDITED_STAGE_TASKS:
        return
    meeting_id = _meeting_id_from_task_args(args)
    if meeting_id is None:
        return
    run_id = _audit_run_id_from_task(task)
    meeting_processing_audit.stage_started(
        run_id=run_id,
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
    task_id = _task_request_id(task)
    meeting_processing_audit.stage_completed(
        run_id=run_id,
        meeting_id=meeting_id,
        stage=stage,
        task_id=task_id,
        metadata={"celery_state": "SUCCESS"},
    )
    if stage == "stage_embedding":
        meeting_processing_audit.run_completed(run_id, message="Processing pipeline completed")


@task_failure.connect
def _record_audit_stage_failed(task_id=None, exception=None, args=None, sender=None, **kwargs):
    stage = getattr(sender, "name", None)
    if stage not in _AUDITED_STAGE_TASKS:
        return
    meeting_id = _meeting_id_from_task_args(args)
    if meeting_id is None:
        return
    run_id = _audit_run_id_from_task(sender)
    meeting_processing_audit.stage_failed(
        run_id=run_id,
        meeting_id=meeting_id,
        stage=stage,
        task_id=task_id,
        error=exception,
        metadata={"celery_task_name": stage},
    )
    meeting_processing_audit.run_failed(run_id, error=exception)


# ── Error callback (linked to each stage via link_error) ─────────────────────

@celery_app.task(name="on_stage_failure")
def on_stage_failure(request, exc, traceback):
    """Error callback for any pipeline stage. Marks meeting as failed."""
    # The first argument of the original task is always meeting_id
    meeting_id = request.args[0] if request.args else None
    if meeting_id is None:
        logger.warning("on_stage_failure: could not determine meeting_id request_args=%s", str(request.args))
        return

    logger.info("on_stage_failure triggered meeting_id=%s exc=%s", meeting_id, str(exc))
    headers = getattr(request, "headers", None) or {}
    run_id = None
    if isinstance(headers, dict):
        try:
            header_run_id = headers.get("meeting_processing_run_id")
            run_id = int(header_run_id) if header_run_id else None
        except (TypeError, ValueError):
            run_id = None
    # task_failure is the authority for per-stage failure events. The linked
    # error callback still preserves existing meeting failure behavior and
    # idempotently ensures the run is terminal when headers are available.
    meeting_processing_audit.run_failed(run_id, error=exc)
    meeting_service.mark_failed(meeting_id, str(exc))

    failed_meeting = None

    # Track failure in PostHog
    try:
        failed_meeting = meeting_service.get(Meeting, meeting_id)
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
        failed_meeting = failed_meeting or meeting_service.get(Meeting, meeting_id)
        if failed_meeting and failed_meeting.owner_id:
            with Session(engine) as session:
                user = session.get(User, failed_meeting.owner_id)
            if user and user.email:
                email_service.send_failure_email(user.email, failed_meeting, str(exc))
    except Exception:
        logger.exception("on_stage_failure email lookup failed meeting_id=%s", meeting_id)

    # Clean up temp files for both the legacy suffix and the source suffix.
    temp_paths = {
        _temp_path_for(meeting_id),
        _temp_path_for(meeting_id, getattr(failed_meeting, "file_path", None)),
    }
    for temp_path in temp_paths:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ── Stage 1: Download ────────────────────────────────────────────────────────

@celery_app.task(name="stage_download")
def stage_download(meeting_id: int) -> int:
    """Download audio from storage. Skips download for RunPod (it fetches via presigned URL)."""
    logger.info("stage_download started meeting_id=%s", meeting_id)

    try:
        meeting = meeting_service.get(Meeting, meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found.")

        meeting_service.update_sub_status(meeting_id, "downloading")

        if not meeting.file_path:
            raise ValueError("No S3 file path associated with meeting.")

        # RunPod fetches audio directly via presigned URL — skip local download
        if _configured_transcription_provider() == ProviderName.RUNPOD.value:
            logger.info("stage_download skipped (RunPod mode) meeting_id=%s", meeting_id)
        else:
            download_url = storage.get_presigned_download_url(meeting.file_path)
            temp_audio_path = _temp_path_for(meeting_id, meeting.file_path)
            urllib.request.urlretrieve(download_url, temp_audio_path)

        logger.info("stage_download complete meeting_id=%s owner_id=%s", meeting_id, meeting.owner_id)
    except Exception:
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


@celery_app.task(name="stage_transcribe")
def stage_transcribe(meeting_id: int) -> int:
    """Run the transcription provider pipeline (transcribe + align + diarize)."""
    logger.info("stage_transcribe started meeting_id=%s", meeting_id)

    is_runpod = _configured_transcription_provider() == ProviderName.RUNPOD.value
    temp_audio_path = _temp_path_for(meeting_id)

    def on_transcribe_progress(stage: str):
        """Callback from provider — updates sub_status for each internal stage."""
        # Use the base stage key for sub_status (strip progress details like percentages)
        sub = stage.split(" (")[0]
        meeting_service.update_sub_status(meeting_id, sub)

    def on_transcription_heartbeat() -> None:
        """Refresh liveness without allowing metadata failures to abort work."""
        meeting_service.touch_processing_heartbeat(meeting_id)

    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            if not meeting:
                raise ValueError(f"Meeting {meeting_id} not found.")
            temp_audio_path = _temp_path_for(meeting_id, meeting.file_path)
            if not is_runpod and not os.path.exists(temp_audio_path):
                raise FileNotFoundError(f"Temp audio file not found: {temp_audio_path}")

            # Look up user tier for provider routing
            user: User | None = session.get(User, meeting.owner_id) if meeting.owner_id else None
            user_tier = user.tier if user else None

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
        config.transcription_type = meeting.transcription_type or TranscriptionType.GENERAL

        meeting_service.update_sub_status(meeting_id, "transcribing")

        with get_provider(user_tier=user_tier) as provider:
            source = AudioSourceResolver.resolve_for_provider(
                temp_audio_path,
                getattr(provider, "provider_name", _configured_transcription_provider()),
                storage_key=meeting.file_path,
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
            )
            result = provider.transcribe(
                request,
                on_status_change=on_transcribe_progress,
                on_heartbeat=on_transcription_heartbeat,
            )

        # Write segments to DB
        with Session(engine) as session:
            # Delete old segments if this is a retried job
            old_segments = session.exec(
                select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
            ).all()
            for old in old_segments:
                session.delete(old)

            # Write new segments from TranscriptionResult
            for seg in result.segments:
                words_dicts = [
                    {
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "speaker": w.speaker_label,
                    }
                    for w in seg.words
                ]
                db_segment = TranscriptSegment(
                    meeting_id=meeting_id,
                    start_time=seg.start,
                    end_time=seg.end,
                    text=seg.text,
                    speaker=seg.speaker or "SPEAKER_UNKNOWN",
                    words=words_dicts,
                )
                session.add(db_segment)

            meeting_obj = session.get(Meeting, meeting_id)
            if meeting_obj:
                meeting_obj.transcript_text = result.text
                if result.audio_duration_seconds is not None:
                    meeting_obj.duration_seconds = int(result.audio_duration_seconds)
            session.commit()

            # Update minutes used this month
            if user_tier is not None:
                user_obj = session.get(User, meeting.owner_id) if meeting.owner_id else None
                if user_obj and result.audio_duration_seconds is not None:
                    user_obj.minutes_used_this_month += ceil(result.audio_duration_seconds / 60)
                    session.add(user_obj)
                    session.commit()

    except Exception as provider_exc:
        logger.exception("stage_transcribe failed meeting_id=%s", meeting_id)
        raise provider_exc

    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

    def _duration_tier(seconds: float | None) -> str:
        if seconds is None:
            return "unknown"
        if seconds < 600:
            return "short"
        if seconds <= 3600:
            return "medium"
        return "long"

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
            notify(
                "transcription_completed",
                (user_obj.email if user_obj else None) or str(meeting_for_analytics.owner_id),
                meeting_for_analytics.title,
                meeting_id=meeting_id,
                extra={
                    "Duration": (
                        f"{duration_minutes} min"
                        if duration_minutes is not None
                        else "unknown"
                    )
                },
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

    logger.info("stage_transcribe complete meeting_id=%s", meeting_id)
    return meeting_id


# ── Stage 2b: Transliterate ──────────────────────────────────────────────────

@celery_app.task(name="stage_transliterate")
def stage_transliterate(meeting_id: int) -> int:
    """If meeting.requested_language has a transliteration target, populate
    meeting.transliterated_text with the alternate-script version."""
    from app.models.base import Meeting
    from app.services.languages import catalog as lang_catalog
    from app.services.languages.transliteration import transliterate

    logger.info("stage_transliterate started meeting_id=%s", meeting_id)

    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.requested_language is None:
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

        try:
            roman = transliterate(
                text=meeting.transcript_text,
                source_code=source_code,
                target_code=target_code,
            )
        except Exception as exc:
            logger.exception("Transliteration failed for meeting %s", meeting_id)
            try:
                if meeting.owner_id:
                    analytics.capture(
                        meeting.owner_id,
                        "transliteration_failed",
                        {
                            "meeting_id": meeting_id,
                            "source_code": source_code,
                            "target_code": target_code,
                            "error": str(exc)[:200],
                        },
                    )
            except Exception:
                logger.warning(
                    "Failed to capture transliteration_failed meeting_id=%s", meeting_id
                )
            return meeting_id

        meeting.transliterated_text = roman
        session.add(meeting)
        session.commit()

        try:
            if meeting.owner_id:
                analytics.capture(
                    meeting.owner_id,
                    "transliteration_completed",
                    {
                        "meeting_id": meeting_id,
                        "source_code": source_code,
                        "target_code": target_code,
                        "text_length": len(roman),
                    },
                )
        except Exception:
            logger.warning(
                "Failed to capture transliteration_completed meeting_id=%s", meeting_id
            )

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

    meeting_service.update_sub_status(meeting_id, "building_context")

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

    meeting_service.update_sub_status(meeting_id, "summarizing")

    summary_text = None
    active_template = None

    if meeting.transcript_text:
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

        template_body = active_template.body if active_template else None
        summary_text = summarize_transcript(
            meeting.transcript_text,
            style_examples=style_examples,
            template_body=template_body,
            template_id=str(active_template.id) if active_template else None,
            upload_date=meeting.created_at.strftime("%B %d, %Y") if meeting.created_at else None,
            context=context_result,
            output_language=meeting.requested_language,
        )

    # Infer a meaningful title from the summary via LLM
    inferred_title = None
    if summary_text:
        from app.services.ai_agent import infer_title
        inferred_title = infer_title(
            summary_text,
            output_language=meeting.requested_language,
        )

    meeting_service.save_summary(
        meeting_id,
        summary_text,
        template_id=active_template.id if active_template else None,
        template_name=active_template.name if active_template else None,
    )

    if inferred_title and meeting.title:
        file_extensions = ('.m4a', '.mp3', '.wav', '.mp4', '.mov', '.webm', '.ogg', '.flac')
        if any(meeting.title.lower().endswith(ext) for ext in file_extensions):
            meeting_service.update_field(meeting_id, "title", inferred_title)
    if meeting.owner_id:
        def _word_count_tier(n: int) -> str:
            if n < 200:
                return "short"
            if n <= 500:
                return "medium"
            return "long"

        analytics.capture(
            meeting.owner_id,
            "summary_generated",
            {
                "meeting_id": meeting_id,
                "template_id": active_template.id if active_template else None,
                "word_count_tier": _word_count_tier(len(summary_text.split()) if summary_text else 0),
            },
        )

    # Send summary email and notification (fire-and-forget — never raises)
    if meeting.owner_id:
        try:
            from app.services.email import email_service
            with Session(engine) as session:
                user = session.get(User, meeting.owner_id)
            if user and user.email:
                email_service.send_summary_email(user.email, meeting)
                notify("summary_generated", user.email, meeting.title, meeting_id=meeting_id)
        except Exception:
            logger.exception("stage_summarize email lookup failed meeting_id=%s", meeting_id)

    logger.info("stage_summarize complete meeting_id=%s owner_id=%s", meeting_id, meeting.owner_id)
    return meeting_id


# ── Stage 4: Extract Meeting Intelligence ───────────────────────────────────

@celery_app.task(name="stage_extract_intelligence")
def stage_extract_intelligence(meeting_id: int) -> int:
    """Extract transcript-only intelligence and finalize the meeting."""
    logger.info("stage_extract_intelligence started meeting_id=%s", meeting_id)
    meeting_service.touch_processing_heartbeat(meeting_id)

    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting or not meeting.transcript_text:
        logger.warning("stage_extract_intelligence: no transcript for meeting_id=%s", meeting_id)
        if meeting:
            meeting_service.mark_completed(meeting_id)
        return meeting_id

    from app.services.meeting_intelligence import intelligence_service

    meeting_type = meeting.meeting_type or "generic"

    # Update status to processing
    with Session(engine) as session:
        db_meeting = session.get(Meeting, meeting_id)
        if db_meeting:
            db_meeting.structured_output_status = "processing"
            session.add(db_meeting)
            session.commit()
            meeting_service.touch_processing_heartbeat(meeting_id)

    try:
        # Call 1: Extract highlights (action items, key questions, chapters)
        highlights_data = intelligence_service.extract_highlights(meeting.transcript_text)
        intelligence_service.save_highlights(meeting_id, highlights_data)

        # Call 2: Extract structured output (meeting-type-specific)
        structured_output = intelligence_service.extract_structured_output(
            meeting.transcript_text, meeting_type
        )

        # Save structured output and mark as completed
        with Session(engine) as session:
            db_meeting = session.get(Meeting, meeting_id)
            if db_meeting:
                db_meeting.structured_output = structured_output
                db_meeting.structured_output_status = "completed"
                session.add(db_meeting)
                session.commit()

        logger.info("stage_extract_intelligence complete meeting_id=%s type=%s", meeting_id, meeting_type)

    except Exception:
        logger.exception("stage_extract_intelligence failed meeting_id=%s", meeting_id)
        with Session(engine) as session:
            db_meeting = session.get(Meeting, meeting_id)
            if db_meeting:
                db_meeting.structured_output_status = "failed"
                session.add(db_meeting)
                session.commit()

    # Intelligence may have failed, but the extraction attempt is complete and
    # the summary remains usable.  Do not finalize from stage_summarize: this is
    # the only stage that owns the completed transition.
    meeting_service.mark_completed(meeting_id)

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
    """Abort S3 multipart uploads that were never completed (older than 24h)."""
    from app.services.storage import storage

    try:
        pending = storage.list_pending_multipart_uploads(older_than_hours=24)
        for entry in pending:
            storage.abort_multipart_upload(
                object_key=entry["Key"],
                upload_id=entry["UploadId"],
            )
            logger.info(
                "Aborted abandoned upload: key=%s upload_id=%s initiated=%s",
                entry["Key"], entry["UploadId"], entry["Initiated"],
            )
    except Exception:
        logger.warning("cleanup_abandoned_uploads failed", exc_info=True)


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


def _emit_visual_side_effects(finalization: dict, result=None) -> None:
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
            notify(
                "visual_breakdown_completed",
                (user_obj.email if user_obj else None) or str(owner_id),
                finalization.get("title"),
                meeting_id=meeting_id,
                extra={"segment_count": str(finalization.get("segment_count", 0))},
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


def _run_visual_breakdown(meeting_id: int, *, optional: bool) -> int:
    logger.info(
        "%s started meeting_id=%s",
        "stage_optional_visual_breakdown" if optional else "stage_visual_breakdown",
        meeting_id,
    )
    run_epoch, should_process = meeting_service.begin_visual_breakdown(meeting_id)
    if not should_process or not _acquire_visual_lease(meeting_id, run_epoch):
        return meeting_id

    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            raise RuntimeError(f"Meeting {meeting_id} not found")
        owner_id = meeting.owner_id
        file_path = meeting.file_path
        source_type = meeting.source_type
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
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="skipped",
            reason="vision_disabled",
            warning_code="visual_disabled",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    if not file_path:
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="skipped",
            reason="no_media_file",
            warning_code="no_video_file",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    if source_type == "youtube":
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="skipped",
            reason="youtube_audio_only",
            warning_code="youtube_audio_only",
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    meeting_service.update_sub_status(meeting_id, "analyzing_video")
    meeting_service.increment_visual_breakdown_attempt(meeting_id, run_epoch)
    try:
        result = DirectVisionService().submit_and_wait(
            {
                "file_path": file_path,
                "owner_id": str(owner_id) if owner_id is not None else "unknown",
                "meeting_id": str(meeting_id),
                "source_type": source_type,
                "transcript": transcript_payload,
                "params": {
                    key: stored_visual_params[key]
                    for key in ("media_type", "mime_type", "content_type", "media_kind")
                    if key in stored_visual_params
                },
            },
            on_heartbeat=lambda: meeting_service.touch_processing_heartbeat(meeting_id),
        )
    except Exception as error:
        logger.warning("visual worker failed meeting_id=%s error=%s", meeting_id, _sanitize_visual_error(error))
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="fallback",
            reason="vision_worker_error",
            warning_code="vision_worker_error",
            result_params={"warning_code": "vision_worker_error"},
        )
        _emit_visual_side_effects(finalization)
        return meeting_id

    if result.status != "completed":
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="fallback",
            reason="vision_worker_failed",
            warning_code="vision_worker_failed",
            result_params={"warning_code": "vision_worker_failed"},
        )
        _emit_visual_side_effects(finalization, result)
        return meeting_id

    if not result.segments:
        skip_reason = str(getattr(result, "params", {}).get("skip_reason") or "no_relevant_visual")
        finalization = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
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
    finalization = meeting_service.finalize_visual_breakdown(
        meeting_id,
        run_epoch,
        outcome="completed",
        result_params=_safe_visual_params(result),
        raw_output_s3_key=result.raw_output_s3_key,
        model=result.model,
        segments=worker_segments,
    )
    _emit_visual_side_effects(finalization, result)
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
        meeting.structured_output_status == "completed"
        and meeting.structured_output is not None
    ):
        meeting_service.mark_completed(meeting_id)
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
    result = chain(*_build_pipeline_signatures(meeting_id, stages, getattr(run, "id", None))).apply_async()
    meeting_processing_audit.set_root_task(getattr(run, "id", None), getattr(result, "id", None))


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
        finalize=meeting_service.mark_completed,
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


@celery_app.task(
    name="stage_embedding",
    autoretry_for=_EMBEDDING_RETRY_EXCEPTIONS,
    max_retries=3,
    retry_backoff=True,
)
def stage_embedding(meeting_id: int) -> int:
    """Index grouped meeting content into the active vector collection."""
    if not settings.INDEXING_ENABLED:
        logger.info("stage_embedding skipped disabled meeting_id=%s", meeting_id)
        return meeting_id

    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting:
        logger.warning("stage_embedding skipped missing meeting_id=%s", meeting_id)
        return meeting_id
    if not meeting.group_id:
        logger.info("stage_embedding skipped ungrouped meeting_id=%s", meeting_id)
        return meeting_id
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
        chunks = build_meeting_chunks(
            meeting_id=meeting.id,
            transcript_text=meeting.transcript_text,
            transliterated_text=meeting.transliterated_text,
            summary_text=meeting.summary_text,
            original_summary_text=meeting.original_summary_text,
            structured_output=meeting.structured_output,
            structured_output_status=meeting.structured_output_status,
        )
        if not chunks:
            logger.info("stage_embedding skipped no content meeting_id=%s", meeting_id)
            _capture_embedding_event(
                meeting.owner_id,
                "embedding_index_completed",
                {"meeting_id": meeting_id, "group_id": meeting.group_id, "point_count": 0},
            )
            return meeting_id

        provider = get_embedding_provider()
        vectors = provider.embed([chunk.text for chunk in chunks])
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
        get_vector_store().upsert_points(points)
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
    meeting = meeting_service.get(Meeting, meeting_id)
    owner_id = getattr(meeting, "owner_id", None)
    group_id = getattr(meeting, "group_id", None)
    logger.info(
        "delete_meeting_vectors started meeting_id=%s owner_id=%s group_id=%s",
        meeting_id,
        owner_id,
        group_id,
    )
    try:
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
        get_vector_store().delete_by_filter(owner_id=owner_id, group_id=group_id)
    except Exception:
        logger.exception("delete_group_vectors failed group_id=%s owner_id=%s", group_id, owner_id)
        _capture_embedding_event(owner_id, "embedding_group_delete_failed", {"group_id": group_id})
        raise
    logger.info("delete_group_vectors complete group_id=%s", group_id)
    _capture_embedding_event(owner_id, "embedding_group_delete_completed", {"group_id": group_id})
    return group_id


@celery_app.task(name="reindex_group")
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
    stages = [
        stage_download,
        stage_transcribe,
        stage_transliterate,
        stage_optional_visual_breakdown,
        stage_summarize,
        stage_extract_intelligence,
        stage_embedding,
    ]
    result = chain(*_build_pipeline_signatures(meeting_id, stages, getattr(run, "id", None))).apply_async()
    meeting_processing_audit.set_root_task(getattr(run, "id", None), getattr(result, "id", None))


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

    meeting = meeting_service.get(Meeting, meeting_id)
    if not meeting:
        raise ValueError(f"Meeting {meeting_id} not found.")

    if not meeting.source_url:
        raise ValueError("No YouTube URL associated with meeting.")

    meeting_service.update_sub_status(meeting_id, "downloading_youtube")

    try:
        # Step 1: Extract metadata (title, duration, thumbnail, channel)
        metadata = extract_metadata(meeting.source_url)

        # Step 2: Validate duration (max 4 hours = 14400 seconds)
        if metadata["duration"] > 14400:
            raise DurationExceededError(
                "Video exceeds the maximum duration of 4 hours."
            )

        # Step 3: Update meeting with YouTube metadata
        with Session(engine) as session:
            m = session.get(Meeting, meeting_id)
            if m:
                m.title = metadata["title"]
                m.youtube_title = metadata["title"]
                m.youtube_duration_seconds = metadata["duration"]
                m.youtube_thumbnail_url = metadata["thumbnail"]
                m.youtube_channel = metadata["channel"]
                session.add(m)
                session.commit()

        # Step 4: Download audio to temp directory
        video_id = metadata["id"]
        audio_path = download_audio(meeting.source_url, TEMP_DIR, video_id)

        # Step 5: Upload audio to object storage
        file_key = f"users/{meeting.owner_id}/meetings/yt_{video_id}.mp3"
        with open(audio_path, "rb") as f:
            storage.upload_file(f.read(), file_key, "audio/mpeg")

        # Step 6: Update meeting file_path and set temp path for transcription
        with Session(engine) as session:
            m = session.get(Meeting, meeting_id)
            if m:
                m.file_path = file_key
                session.add(m)
                session.commit()

        # Move/rename to the conventional temp path for stage_transcribe
        conventional_path = _temp_path_for(meeting_id, file_key)
        os.rename(audio_path, conventional_path)

        logger.info(
            "stage_youtube_download complete meeting_id=%s video_id=%s duration=%s",
            meeting_id,
            video_id,
            metadata["duration"],
        )

        if meeting.owner_id:
            analytics.capture(
                meeting.owner_id,
                "youtube_download_completed",
                {
                    "meeting_id": meeting_id,
                    "duration_seconds": metadata["duration"],
                    "video_id": video_id,
                },
            )

    except YouTubeError:
        raise  # Let link_error handler catch it with the descriptive message
    except Exception:
        logger.exception("stage_youtube_download failed meeting_id=%s", meeting_id)
        raise

    return meeting_id


def dispatch_youtube_pipeline(meeting_id: int):
    """Build and dispatch the Celery chain for YouTube ingestion.

    Uses stage_youtube_download instead of stage_download, then feeds into the
    same optional-visual → summary → transcript-intelligence chain.  The
    optional stage short-circuits YouTube audio without downloading media twice.
    """
    run = meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="youtube")
    stages = [
        stage_youtube_download,
        stage_transcribe,
        stage_transliterate,
        stage_optional_visual_breakdown,
        stage_summarize,
        stage_extract_intelligence,
        stage_embedding,
    ]
    result = chain(*_build_pipeline_signatures(meeting_id, stages, getattr(run, "id", None))).apply_async()
    meeting_processing_audit.set_root_task(getattr(run, "id", None), getattr(result, "id", None))
