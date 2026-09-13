# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import logging
import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import delete, func
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload
from app.models import (
    Meeting,
    MeetingCreate,
    TranscriptSegment,
    TranscriptionType,
    VisualSegment,
)
from app.core.config import settings
from app.db.engine import engine
from app.services.base import BaseService

logger = logging.getLogger(__name__)


VISUAL_BREAKDOWN_ACTIVE_STATUSES = {"queued", "processing"}
VISUAL_BREAKDOWN_TERMINAL_STATUSES = {"completed", "skipped", "fallback"}

class MeetingService(BaseService):
    def on_before_action(self, action: str, **kwargs):
        """
        Specialized audit hook for MeetingService.
        """
        print(f"AUDIT [MEETING] [BEFORE]: User is performing '{action}' on Meeting.")

    def on_after_action(self, action: str, result: any, **kwargs):
        """
        Specialized audit hook for MeetingService.
        """
        print(f"AUDIT [MEETING] [AFTER]: Finished '{action}' on Meeting with result type: {type(result)}")

    def create_meeting(self, meeting_in: MeetingCreate, owner_id: int) -> Meeting:
        meeting = Meeting.from_orm(meeting_in)
        meeting.owner_id = owner_id
        return self.save(meeting)

    def get_meeting(self, meeting_id: int) -> Optional[Meeting]:
        with Session(engine) as session:
            statement = select(Meeting).where(Meeting.id == meeting_id).options(selectinload(Meeting.segments))
            return session.exec(statement).first()

    def get_meetings(self, owner_id: int, skip: int = 0, limit: int = 100) -> List[Meeting]:
        """List meetings without heavy text columns. summary_text is truncated to 300 chars."""
        with Session(engine) as session:
            # Select lightweight columns + truncated summary
            light_cols = [
                Meeting.id,
                Meeting.title,
                Meeting.description,
                Meeting.file_path,
                Meeting.duration_seconds,
                Meeting.owner_id,
                Meeting.created_at,
                Meeting.status,
                Meeting.sub_status,
                Meeting.template_id,
                Meeting.template_name,
                Meeting.transcription_type,
                Meeting.source_type,
                Meeting.source_url,
                Meeting.youtube_title,
                Meeting.youtube_duration_seconds,
                Meeting.youtube_thumbnail_url,
                Meeting.youtube_channel,
                Meeting.summary_edited,
                Meeting.visual_breakdown_status,
                func.left(Meeting.summary_text, 300).label("summary_text"),
            ]
            statement = (
                select(*light_cols)
                .where(Meeting.owner_id == owner_id)
                .order_by(Meeting.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return session.exec(statement).all()

    def update_status(self, meeting_id: int, status: str) -> Optional[Meeting]:
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.status = status
        if status in {"queued", "processing"}:
            meeting.processing_heartbeat_at = datetime.utcnow()
        return self.save(meeting)

    def update_transcription_type(self, meeting_id: int, transcription_type: TranscriptionType) -> Optional[Meeting]:
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.transcription_type = transcription_type
        return self.save(meeting)

    def update_meeting_type(self, meeting_id: int, meeting_type: str) -> Optional[Meeting]:
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.meeting_type = meeting_type
        return self.save(meeting)

    def update_sub_status(
        self,
        meeting_id: int,
        sub_status: str,
        status: str = "processing",
    ) -> Optional[Meeting]:
        """Update the granular processing sub-stage and optionally the top-level status.

        Also publishes a fire-and-forget event to Redis Pub/Sub for future SSE support.
        """
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.status = status
        meeting.sub_status = sub_status
        meeting = self.save(meeting)

        if status == "processing":
            self.touch_processing_heartbeat(meeting_id)

        # Fire-and-forget Redis Pub/Sub (non-blocking, best-effort)
        try:
            import redis
            r = redis.from_url(settings.REDIS_URL)
            r.publish(f"meeting:{meeting_id}:status", sub_status)
            r.close()
        except Exception as e:
            logger.warning("Failed to publish to Redis for meeting %s: %s", meeting_id, e)

        return meeting

    def touch_processing_heartbeat(
        self, meeting_id: int, *, heartbeat_at: datetime | None = None
    ) -> bool:
        """Best-effort heartbeat update for an active meeting stage.

        Heartbeats are liveness metadata, not pipeline output. A database or
        telemetry failure while refreshing one must never turn a successful
        stage into a failed meeting.
        """
        try:
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                if meeting is None or meeting.status not in {"queued", "processing"}:
                    return False
                meeting.processing_heartbeat_at = heartbeat_at or datetime.utcnow()
                session.add(meeting)
                session.commit()
            return True
        except Exception:
            logger.warning(
                "Failed to refresh processing heartbeat meeting_id=%s",
                meeting_id,
                exc_info=True,
            )
            return False

    def mark_completed(
        self,
        meeting_id: int,
        summary_text: str | None = None,
        action_items_text: str | None = None,
        template_id: int | None = None,
        template_name: str | None = None,
    ) -> Optional[Meeting]:
        """Mark a meeting as completed, clearing sub_status and setting final outputs."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.status = "completed"
        meeting.sub_status = None
        if summary_text is not None:
            meeting.summary_text = summary_text
        if action_items_text is not None:
            meeting.action_items_text = action_items_text
        if template_id is not None:
            meeting.template_id = template_id
        if template_name is not None:
            meeting.template_name = template_name
        return self.save(meeting)

    def save_summary(
        self,
        meeting_id: int,
        summary_text: str | None,
        *,
        template_id: int | None = None,
        template_name: str | None = None,
    ) -> Optional[Meeting]:
        """Persist a summary without completing the intelligence stage."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.status = "processing"
        meeting.sub_status = "summarizing"
        meeting.summary_text = summary_text
        if template_id is not None:
            meeting.template_id = template_id
        if template_name is not None:
            meeting.template_name = template_name
        return self.save(meeting)

    def queue_visual_breakdown(self, meeting_id: int) -> tuple[str, bool]:
        """Create a new visual run epoch while holding the meeting row lock.

        The epoch is kept in the existing JSONB parameters column instead of a
        new migration.  A caller can safely retry the enqueue operation: an
        already active run is returned unchanged and a completed run receives a
        fresh epoch for an explicit re-run.
        """
        with Session(engine) as session:
            statement = (
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            )
            meeting = session.exec(statement).first()
            if meeting is None:
                raise RuntimeError(f"Meeting {meeting_id} not found")

            params = dict(meeting.visual_breakdown_params or {})
            current_epoch = params.get("run_epoch")
            if meeting.visual_breakdown_status in VISUAL_BREAKDOWN_ACTIVE_STATUSES and current_epoch:
                return str(current_epoch), False

            run_epoch = uuid.uuid4().hex
            params.update(
                {
                    "run_epoch": run_epoch,
                    "idempotency_key": f"visual-breakdown:{meeting_id}:{run_epoch}",
                    "outcome": None,
                    "warning_code": None,
                    "attempts": 0,
                    "side_effects": {},
                }
            )
            meeting.visual_breakdown_status = "queued"
            meeting.visual_breakdown_error = None
            meeting.visual_breakdown_completed_at = None
            meeting.visual_breakdown_params = params
            session.add(meeting)
            session.commit()
            self.touch_processing_heartbeat(meeting_id)
            return run_epoch, True

    def begin_visual_breakdown(self, meeting_id: int) -> tuple[str, bool]:
        """Return the current epoch or atomically start the first automatic run."""
        with Session(engine) as session:
            statement = (
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            )
            meeting = session.exec(statement).first()
            if meeting is None:
                raise RuntimeError(f"Meeting {meeting_id} not found")

            params = dict(meeting.visual_breakdown_params or {})
            current_epoch = params.get("run_epoch")
            if current_epoch and meeting.visual_breakdown_status in VISUAL_BREAKDOWN_TERMINAL_STATUSES:
                return str(current_epoch), False

            if current_epoch and meeting.visual_breakdown_status == "processing":
                return str(current_epoch), False

            if current_epoch and meeting.visual_breakdown_status == "queued":
                meeting.visual_breakdown_status = "processing"
                meeting.visual_breakdown_error = None
                session.add(meeting)
                session.commit()
                self.touch_processing_heartbeat(meeting_id)
                return str(current_epoch), True

            run_epoch = uuid.uuid4().hex
            params.update(
                {
                    "run_epoch": run_epoch,
                    "idempotency_key": f"visual-breakdown:{meeting_id}:{run_epoch}",
                    "outcome": None,
                    "warning_code": None,
                    "attempts": 0,
                    "side_effects": {},
                }
            )
            meeting.visual_breakdown_status = "processing"
            meeting.visual_breakdown_error = None
            meeting.visual_breakdown_completed_at = None
            meeting.visual_breakdown_params = params
            session.add(meeting)
            session.commit()
            self.touch_processing_heartbeat(meeting_id)
            return run_epoch, True

    def reset_stale_visual_breakdown_in_session(
        self, session: Session, meeting: Meeting
    ) -> str | None:
        """Fence a stale visual epoch and queue a fresh one under a row lock.

        Recovery calls this while the meeting row is already locked with
        ``FOR UPDATE SKIP LOCKED``. Replacing ``run_epoch`` makes late results
        from the orphaned worker harmless: ``finalize_visual_breakdown`` will
        reject them atomically.
        """
        if meeting.visual_breakdown_status != "processing":
            return None

        run_epoch = uuid.uuid4().hex
        params = dict(meeting.visual_breakdown_params or {})
        params.update(
            {
                "run_epoch": run_epoch,
                "idempotency_key": f"visual-breakdown:{meeting.id}:{run_epoch}",
                "outcome": None,
                "warning_code": None,
                "attempts": 0,
                "side_effects": {},
            }
        )
        meeting.visual_breakdown_status = "queued"
        meeting.visual_breakdown_error = None
        meeting.visual_breakdown_completed_at = None
        meeting.visual_breakdown_params = params
        meeting.processing_heartbeat_at = datetime.utcnow()
        session.exec(
            delete(VisualSegment).where(VisualSegment.meeting_id == meeting.id)
        )
        session.add(meeting)
        return run_epoch

    def increment_visual_breakdown_attempt(self, meeting_id: int, run_epoch: str) -> int:
        """Increment the attempt counter for the active epoch under a row lock."""
        with Session(engine) as session:
            statement = (
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            )
            meeting = session.exec(statement).first()
            if meeting is None:
                raise RuntimeError(f"Meeting {meeting_id} not found")
            params = dict(meeting.visual_breakdown_params or {})
            if params.get("run_epoch") != run_epoch:
                return int(params.get("attempts") or 0)
            attempts = int(params.get("attempts") or 0) + 1
            params["attempts"] = attempts
            meeting.visual_breakdown_params = params
            session.add(meeting)
            session.commit()
            return attempts

    def finalize_visual_breakdown(
        self,
        meeting_id: int,
        run_epoch: str,
        *,
        outcome: str,
        reason: str | None = None,
        warning_code: str | None = None,
        result_params: dict | None = None,
        raw_output_s3_key: str | None = None,
        model: str | None = None,
        segments: list | None = None,
        run_count: bool = True,
    ) -> dict:
        """Atomically converge a visual outcome and its segment replacement.

        The returned side-effect flags are claimed in the same transaction. A
        duplicate delivery therefore cannot emit a second telemetry event or
        notification after the first terminal commit.
        """
        from app.services.visual_segments import VisualSegmentService

        if outcome not in VISUAL_BREAKDOWN_TERMINAL_STATUSES:
            raise ValueError(f"Unsupported visual outcome: {outcome}")

        with Session(engine) as session:
            statement = (
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            )
            meeting = session.exec(statement).first()
            if meeting is None:
                raise RuntimeError(f"Meeting {meeting_id} not found")

            params = dict(meeting.visual_breakdown_params or {})
            if params.get("run_epoch") != run_epoch:
                return {"applied": False, "meeting_id": meeting_id}

            side_effects = dict(params.get("side_effects") or {})
            if params.get("outcome") in VISUAL_BREAKDOWN_TERMINAL_STATUSES:
                return {"applied": False, "meeting_id": meeting_id}

            segment_list = list(segments or [])
            VisualSegmentService.replace_for_meeting_in_session(
                session, meeting_id, segment_list
            )

            merged_params = dict(params)
            merged_params.update(result_params or {})
            merged_params.update(
                {
                    "run_epoch": run_epoch,
                    "idempotency_key": f"visual-breakdown:{meeting_id}:{run_epoch}",
                    "outcome": outcome,
                    "warning_code": warning_code,
                    "reason": reason,
                    "attempts": int(params.get("attempts") or 0),
                }
            )
            completion_event = outcome == "completed" and not side_effects.get(
                "completion_event"
            )
            warning_event = (
                outcome in {"skipped", "fallback"}
                and bool(warning_code)
                and not side_effects.get("warning_event")
            )
            notification_event = completion_event
            if completion_event:
                side_effects["completion_event"] = True
            if warning_event:
                side_effects["warning_event"] = True
            if notification_event:
                side_effects["notification_event"] = True
            merged_params["side_effects"] = side_effects

            meeting.visual_breakdown_status = outcome
            meeting.visual_breakdown_error = warning_code or reason
            meeting.visual_breakdown_completed_at = datetime.utcnow()
            if outcome == "completed":
                meeting.visual_raw_output_s3_key = raw_output_s3_key
                meeting.visual_breakdown_model = model
            meeting.visual_breakdown_params = merged_params
            if run_count:
                meeting.visual_breakdown_run_count = (meeting.visual_breakdown_run_count or 0) + 1
            session.add(meeting)
            session.commit()

            self.touch_processing_heartbeat(meeting_id)

            return {
                "applied": True,
                "meeting_id": meeting_id,
                "owner_id": meeting.owner_id,
                "title": meeting.title,
                "status": outcome,
                "warning_code": warning_code,
                "segment_count": len(segment_list),
                "completion_event": completion_event,
                "warning_event": warning_event,
                "notification_event": notification_event,
                "attempts": int(merged_params.get("attempts") or 0),
            }

    def mark_failed(self, meeting_id: int, error_message: str) -> Optional[Meeting]:
        """Mark a meeting as failed with an error message in summary_text."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        meeting.status = "failed"
        meeting.sub_status = None
        meeting.summary_text = f"[Error: {error_message}]"
        return self.save(meeting)

    def add_segment(self, meeting_id: int, start: float, end: float, text: str) -> TranscriptSegment:
        segment = TranscriptSegment(
            meeting_id=meeting_id,
            start_time=start,
            end_time=end,
            text=text
        )
        return self.save(segment)

    def initiate_processing(self, file_key: str) -> Optional[Meeting]:
        """
        Called by webhook handler. Looks up meeting by file_path,
        validates status is pending_upload, transitions to 'queued',
        and dispatches the event-driven Celery pipeline chain.
        Returns None if no match or already processed.
        """
        with Session(engine) as session:
            statement = select(Meeting).where(
                Meeting.file_path == file_key,
                Meeting.status == "pending_upload"
            )
            meeting = session.exec(statement).first()

        if not meeting:
            return None

        meeting.status = "queued"
        meeting.processing_heartbeat_at = datetime.utcnow()
        meeting = self.save(meeting)

        from app.worker import dispatch_pipeline
        dispatch_pipeline(meeting.id)

        return meeting

    def update_summary(self, meeting_id: int, summary_text: str) -> Optional[Meeting]:
        """Update the summary text. On first edit, preserve the original AI summary."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        if meeting.original_summary_text is None and meeting.summary_text is not None:
            meeting.original_summary_text = meeting.summary_text
        meeting.summary_text = summary_text
        meeting.summary_edited = True
        return self.save(meeting)

    def restore_summary(self, meeting_id: int) -> Optional[Meeting]:
        """Restore the original AI-generated summary."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        if meeting.original_summary_text is None:
            return None
        meeting.summary_text = meeting.original_summary_text
        meeting.summary_edited = False
        return self.save(meeting)

    def create_from_youtube(self, url: str, owner_id: int) -> Meeting:
        """Create a meeting record for YouTube ingestion.

        Starts at 'queued' status (skips 'pending_upload' since there's no file upload step).
        """
        meeting = Meeting(
            title="YouTube Video",
            source_type="youtube",
            source_url=url,
            status="queued",
            owner_id=owner_id,
            processing_heartbeat_at=datetime.utcnow(),
        )
        return self.save(meeting)

    def count_active_youtube(self, owner_id: int) -> int:
        """Count active YouTube ingestions for a user (queued or processing)."""
        with Session(engine) as session:
            statement = select(Meeting).where(
                Meeting.owner_id == owner_id,
                Meeting.source_type == "youtube",
                Meeting.status.in_(["queued", "processing"]),
            )
            return len(session.exec(statement).all())

    def update_field(self, meeting_id: int, field: str, value) -> Optional[Meeting]:
        """Update a single field on a meeting."""
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            if not meeting:
                return None
            setattr(meeting, field, value)
            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            return meeting

    def delete_meeting(self, meeting_id: int) -> bool:
        return self.delete(Meeting, meeting_id)

meeting_service = MeetingService()
