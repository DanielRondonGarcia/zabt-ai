# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import logging
import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy import delete, func
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload
from app.models import (
    Group,
    GroupMemberRole,
    GroupMembership,
    Meeting,
    MeetingCreate,
    MeetingProcessingRun,
    TranscriptSegment,
    TranscriptionType,
    VisualSegment,
)
from app.core.config import settings
from app.db.engine import engine
from app.services.base import BaseService
from app.services.meeting_processing_audit import (
    meeting_processing_audit,
    sanitize_message,
)
from app.services.meeting_recovery import summary_is_usable
from app.services.transcript_fingerprint import transcript_fingerprint

logger = logging.getLogger(__name__)


VISUAL_BREAKDOWN_ACTIVE_STATUSES = {"queued", "processing"}
VISUAL_BREAKDOWN_TERMINAL_STATUSES = {"completed", "skipped", "fallback"}
VISUAL_BREAKDOWN_CLOSED_STATUSES = VISUAL_BREAKDOWN_TERMINAL_STATUSES | {
    "cancelled",
    "failed",
}

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

    def create_meeting(
        self,
        meeting_in: MeetingCreate,
        owner_id: int,
        storage_provider: str = "minio",
    ) -> Meeting:
        meeting = Meeting.from_orm(meeting_in)
        meeting.owner_id = owner_id
        meeting.storage_provider = storage_provider
        return self.save(meeting)

    def get_meeting(self, meeting_id: int) -> Optional[Meeting]:
        with Session(engine) as session:
            statement = select(Meeting).where(Meeting.id == meeting_id).options(selectinload(Meeting.segments))
            return session.exec(statement).first()

    def get_meeting_for_owner(
        self, meeting_id: int, owner_id: int
    ) -> Optional[Meeting]:
        """Authorize by both ids before loading the full meeting and segments."""

        with Session(engine) as session:
            authorized = session.exec(
                select(Meeting.id)
                .where(
                    Meeting.id == meeting_id,
                    Meeting.owner_id == owner_id,
                )
                .limit(1)
            ).first()
            if authorized is None:
                return None

            statement = (
                select(Meeting)
                .where(
                    Meeting.id == meeting_id,
                    Meeting.owner_id == owner_id,
                )
                .options(selectinload(Meeting.segments))
            )
            return session.exec(statement).first()

    def get_meeting_for_access(
        self, meeting_id: int, user_id: int
    ) -> Optional[Meeting]:
        """Authorize before loading full meeting content for an owner or member."""

        with Session(engine) as session:
            metadata = session.exec(
                select(Meeting.owner_id, Meeting.group_id).where(
                    Meeting.id == meeting_id
                )
            ).first()
            if metadata is None:
                return None

            owner_id, group_id = metadata
            if owner_id != user_id:
                if group_id is None:
                    return None
                self._require_group_access_in_session(session, group_id, user_id)

            statement = (
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .options(selectinload(Meeting.segments))
            )
            return session.exec(statement).first()

    @staticmethod
    def _require_group_access_in_session(
        session: Session, group_id: int, user_id: int
    ) -> None:
        group = session.get(Group, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
        if group.owner_id == user_id:
            return
        membership = session.exec(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user_id,
            )
        ).first()
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")

    @classmethod
    def _require_editor_in_session(
        cls, session: Session, meeting: Meeting, user_id: int
    ) -> None:
        if meeting.owner_id == user_id:
            return
        if meeting.group_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor access is required.")

        cls.require_group_editor_in_session(session, meeting.group_id, user_id)

    @classmethod
    def require_group_editor_in_session(
        cls, session: Session, group_id: int, user_id: int
    ) -> None:
        group = session.get(Group, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
        if group.owner_id == user_id:
            return

        membership = session.exec(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user_id,
            )
        ).first()
        role = membership.role.value if isinstance(membership and membership.role, GroupMemberRole) else str(
            membership.role if membership is not None else ""
        )
        if membership is None or role != GroupMemberRole.EDITOR.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor access is required.")

    def require_editor_in_session(
        self, session: Session, meeting_id: int, user_id: int
    ) -> Meeting:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
        self._require_editor_in_session(session, meeting, user_id)
        return meeting

    def require_editor_for_user(self, meeting_id: int, user_id: int) -> Meeting:
        """Revalidate editor access against current membership state."""

        with Session(engine) as session:
            return self.require_editor_in_session(session, meeting_id, user_id)

    def get_meetings(
        self,
        owner_id: int,
        skip: int = 0,
        limit: int = 100,
        group_id: int | None = None,
    ) -> List[Meeting]:
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
                Meeting.group_id,
                Meeting.created_at,
                Meeting.status,
                Meeting.sub_status,
                Meeting.template_id,
                Meeting.template_name,
                Meeting.transcription_type,
                Meeting.visual_breakdown_params,
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
            statement = select(*light_cols)
            if group_id is None:
                statement = statement.where(Meeting.owner_id == owner_id)
            else:
                statement = statement.where(Meeting.group_id == group_id)
            statement = (
                statement.order_by(Meeting.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return session.exec(statement).all()

    def get_group_meetings(self, group_id: int, owner_id: int, limit: int = 100):
        """Return bounded meeting metadata for an accessible group."""

        with Session(engine) as session:
            statement = (
                select(
                    Meeting.id,
                    Meeting.title,
                    Meeting.description,
                    Meeting.duration_seconds,
                    Meeting.owner_id,
                    Meeting.group_id,
                    Meeting.created_at,
                    Meeting.status,
                    Meeting.sub_status,
                    func.substr(Meeting.summary_text, 1, 300).label("summary_text"),
                )
                .where(Meeting.group_id == group_id)
                .order_by(Meeting.created_at.desc(), Meeting.id.desc())
                .limit(limit)
            )
            return list(session.exec(statement).all())

    list_for_group = get_group_meetings

    def update_status(self, meeting_id: int, status: str) -> Optional[Meeting]:
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        if status == "completed":
            raise ValueError("Meeting completion is reserved for the embedding stage.")
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

        self._publish_sub_status(meeting_id, sub_status)
        return meeting

    def update_sub_status_for_user(
        self,
        meeting_id: int,
        user_id: int,
        sub_status: str,
        status: str = "processing",
    ) -> Optional[Meeting]:
        """Update processing state only after an in-transaction editor check."""

        with Session(engine) as session:
            meeting = self.require_editor_in_session(session, meeting_id, user_id)
            meeting.status = status
            meeting.sub_status = sub_status
            if status in {"queued", "processing"}:
                meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
            session.refresh(meeting)

        if status == "processing":
            self.touch_processing_heartbeat(meeting_id)
        self._publish_sub_status(meeting_id, sub_status)
        return meeting

    @staticmethod
    def _publish_sub_status(meeting_id: int, sub_status: str) -> None:
        # Fire-and-forget Redis Pub/Sub (non-blocking, best-effort)
        try:
            import redis
            r = redis.from_url(settings.REDIS_URL)
            r.publish(f"meeting:{meeting_id}:status", sub_status)
            r.close()
        except Exception as e:
            logger.warning("Failed to publish to Redis for meeting %s: %s", meeting_id, e)

        return None

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

    def _locked_intelligence_meeting(
        self,
        session: Session,
        meeting_id: int,
        *,
        processing_run_id: int,
        dispatch_attempt_id: str,
        owner_id: int,
    ) -> Meeting | None:
        meeting, run = meeting_processing_audit._locked_current_run(
            session, processing_run_id, meeting_id, dispatch_attempt_id
        )
        if (
            meeting is None
            or run is None
            or meeting.owner_id != owner_id
            or run.owner_id != owner_id
        ):
            return None
        return meeting

    def begin_intelligence_for_attempt(
        self,
        meeting_id: int,
        *,
        processing_run_id: int,
        dispatch_attempt_id: str,
        owner_id: int,
    ) -> bool:
        """Set intelligence status only for the exact current owner and attempt."""
        with Session(engine) as session:
            meeting = self._locked_intelligence_meeting(
                session,
                meeting_id,
                processing_run_id=processing_run_id,
                dispatch_attempt_id=dispatch_attempt_id,
                owner_id=owner_id,
            )
            if meeting is None:
                return False
            meeting.status = "processing"
            meeting.structured_output_status = "processing"
            if meeting.status != "completed":
                meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
            return True

    def validate_intelligence_for_attempt(
        self,
        meeting_id: int,
        *,
        processing_run_id: int,
        dispatch_attempt_id: str,
        owner_id: int,
    ) -> bool:
        """Confirm the exact owner/current-attempt identity before provider I/O."""
        with Session(engine) as session:
            meeting = self._locked_intelligence_meeting(
                session,
                meeting_id,
                processing_run_id=processing_run_id,
                dispatch_attempt_id=dispatch_attempt_id,
                owner_id=owner_id,
            )
            return meeting is not None

    def save_inferred_title_for_attempt(
        self,
        meeting_id: int,
        run_id: int,
        attempt_id: str,
        inferred_title: str | None,
        *,
        owner_id: int,
        audit_task_id: str | None = None,
    ) -> Optional[Meeting]:
        """Persist an inferred title without touching summary or other fields.

        A late title stage must never reset a durable summary. The existing
        file-extension rule applies exactly as in ``save_summary_for_attempt``:
        only an upload-file-style title is replaced, so user-authored titles
        always win. With ``audit_task_id`` the stage's completed resume marker
        is added to this same transaction, so a crash cannot leave a saved
        title without its marker and repeat the model call on resume.
        """
        with Session(engine) as session:
            meeting = self._locked_intelligence_meeting(
                session,
                meeting_id,
                processing_run_id=run_id,
                dispatch_attempt_id=attempt_id,
                owner_id=owner_id,
            )
            if meeting is None:
                return None
            if inferred_title and meeting.title:
                file_extensions = (
                    ".m4a", ".mp3", ".wav", ".mp4", ".mov", ".webm", ".ogg", ".flac"
                )
                if any(meeting.title.lower().endswith(ext) for ext in file_extensions):
                    meeting.title = inferred_title
            if audit_task_id:
                title_applied = bool(inferred_title) and meeting.title == inferred_title
                run = meeting_processing_audit._locked_current_run(
                    session, run_id, meeting_id, attempt_id
                )[1]
                meeting_processing_audit.stage_completed_event_for_output(
                    session,
                    run=run,
                    meeting_id=meeting_id,
                    attempt_id=attempt_id,
                    stage="stage_infer_title",
                    task_id=audit_task_id,
                    message=(
                        "Title updated from the meeting summary"
                        if title_applied
                        else "Existing meeting title kept; inferred title not applied"
                    ),
                    metadata={
                        "transcript_fingerprint": transcript_fingerprint(
                            meeting.transcript_text
                        )
                    },
                )
            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            return meeting

    def complete_intelligence_for_attempt(
        self,
        meeting_id: int,
        structured_output: dict,
        *,
        processing_run_id: int,
        dispatch_attempt_id: str,
        owner_id: int,
        audit_task_id: str | None = None,
    ) -> bool:
        """Persist intelligence output and complete the Meeting atomically."""
        with Session(engine) as session:
            meeting = self._locked_intelligence_meeting(
                session,
                meeting_id,
                processing_run_id=processing_run_id,
                dispatch_attempt_id=dispatch_attempt_id,
                owner_id=owner_id,
            )
            if meeting is None or not meeting.transcript_text:
                return False
            meeting.structured_output = structured_output
            meeting.structured_output_status = "completed"
            meeting.status = "processing"
            meeting.sub_status = "indexing_embeddings"
            meeting.processing_heartbeat_at = datetime.utcnow()
            if audit_task_id:
                meeting_type = structured_output.get("meeting_type", "generic")
                run = meeting_processing_audit._locked_current_run(
                    session, processing_run_id, meeting_id, dispatch_attempt_id
                )[1]
                meeting_processing_audit.stage_completed_event_for_output(
                    session,
                    run=run,
                    meeting_id=meeting_id,
                    attempt_id=dispatch_attempt_id,
                    stage="stage_extract_structured_output",
                    task_id=audit_task_id,
                    message=f"Saved structured output for a {meeting_type} meeting",
                    metadata={
                        "transcript_fingerprint": transcript_fingerprint(
                            meeting.transcript_text
                        )
                    },
                )
            session.add(meeting)
            session.commit()
            return True

    def complete_without_intelligence_for_attempt(
        self,
        meeting_id: int,
        *,
        processing_run_id: int,
        dispatch_attempt_id: str,
        owner_id: int,
    ) -> bool:
        """Preserve empty-transcript completion, fenced by the exact attempt."""
        with Session(engine) as session:
            meeting = self._locked_intelligence_meeting(
                session,
                meeting_id,
                processing_run_id=processing_run_id,
                dispatch_attempt_id=dispatch_attempt_id,
                owner_id=owner_id,
            )
            if meeting is None or meeting.transcript_text:
                return False
            meeting.status = "processing"
            meeting.sub_status = "indexing_embeddings"
            meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
            return True

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

    def save_summary_for_attempt(
        self,
        meeting_id: int,
        run_id: int,
        attempt_id: str,
        summary_text: str | None,
        *,
        template_id: int | None = None,
        template_name: str | None = None,
        inferred_title: str | None = None,
        audit_task_id: str | None = None,
    ) -> Optional[Meeting]:
        """Commit summary fields only for the exact active processing attempt.

        With ``audit_task_id`` the summary's completed resume marker is added
        to this same transaction, so a crash cannot leave a saved summary
        without its marker and repeat the summary model call on resume.
        """
        with Session(engine) as session:
            meeting, _run = meeting_processing_audit._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if meeting is None:
                return None
            meeting.status = "processing"
            meeting.sub_status = "summarizing"
            meeting.processing_heartbeat_at = datetime.utcnow()
            meeting.summary_text = summary_text
            if template_id is not None:
                meeting.template_id = template_id
            if template_name is not None:
                meeting.template_name = template_name
            if inferred_title and meeting.title:
                file_extensions = (
                    ".m4a", ".mp3", ".wav", ".mp4", ".mov", ".webm", ".ogg", ".flac"
                )
                if any(meeting.title.lower().endswith(ext) for ext in file_extensions):
                    meeting.title = inferred_title
            if audit_task_id:
                meeting_processing_audit.stage_completed_event_for_output(
                    session,
                    run=_run,
                    meeting_id=meeting_id,
                    attempt_id=attempt_id,
                    stage="stage_summarize",
                    task_id=audit_task_id,
                    message="Meeting summary saved",
                    metadata={
                        "transcript_fingerprint": transcript_fingerprint(
                            meeting.transcript_text
                        )
                    },
                )
            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            return meeting

    def queue_visual_breakdown(
        self, meeting_id: int, user_id: int | None = None
    ) -> tuple[str, bool]:
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
            if user_id is not None:
                self._require_editor_in_session(session, meeting, user_id)
            if meeting.status == "cancelled":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cancelled meetings cannot queue visual breakdown",
                )

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

    @staticmethod
    def _lock_visual_meeting_in_session(
        session: Session,
        meeting_id: int,
        processing_run_id: int | None,
        dispatch_attempt_id: str | None,
    ) -> Meeting | None:
        if (processing_run_id is None) != (dispatch_attempt_id is None):
            return None
        if processing_run_id is None:
            return session.exec(
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            ).first()

        meeting, run = meeting_processing_audit._locked_current_run(
            session, processing_run_id, meeting_id, dispatch_attempt_id
        )
        if meeting is None or run is None:
            return None
        if run.trigger == "visual_breakdown":
            return meeting if meeting.status == "completed" else None
        if meeting.status not in {"queued", "processing"}:
            return None
        return meeting

    def begin_visual_breakdown(
        self,
        meeting_id: int,
        *,
        processing_run_id: int | None = None,
        dispatch_attempt_id: str | None = None,
    ) -> tuple[str, bool]:
        """Return the current epoch or atomically start the first automatic run."""
        with Session(engine) as session:
            meeting = self._lock_visual_meeting_in_session(
                session, meeting_id, processing_run_id, dispatch_attempt_id
            )
            if meeting is None:
                if processing_run_id is not None or dispatch_attempt_id is not None:
                    return "", False
                raise RuntimeError(f"Meeting {meeting_id} not found")
            if (
                processing_run_id is None
                and dispatch_attempt_id is None
                and meeting.status == "cancelled"
            ):
                return "", False

            params = dict(meeting.visual_breakdown_params or {})
            current_epoch = params.get("run_epoch")
            if (
                current_epoch
                and meeting.visual_breakdown_status in VISUAL_BREAKDOWN_CLOSED_STATUSES
            ):
                return str(current_epoch), False

            if current_epoch and meeting.visual_breakdown_status == "processing":
                return str(current_epoch), False

            if current_epoch and meeting.visual_breakdown_status == "queued":
                meeting.visual_breakdown_status = "processing"
                meeting.visual_breakdown_error = None
                if processing_run_id is not None and meeting.status != "completed":
                    meeting.processing_heartbeat_at = datetime.utcnow()
                session.add(meeting)
                session.commit()
                if processing_run_id is None:
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
            if processing_run_id is not None and meeting.status != "completed":
                meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
            if processing_run_id is None:
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

    def increment_visual_breakdown_attempt(
        self,
        meeting_id: int,
        run_epoch: str,
        *,
        processing_run_id: int | None = None,
        dispatch_attempt_id: str | None = None,
    ) -> int | None:
        """Increment the attempt counter for the active epoch under a row lock."""
        identity_supplied = processing_run_id is not None or dispatch_attempt_id is not None
        with Session(engine) as session:
            meeting = self._lock_visual_meeting_in_session(
                session, meeting_id, processing_run_id, dispatch_attempt_id
            )
            if meeting is None:
                if identity_supplied:
                    return None
                raise RuntimeError(f"Meeting {meeting_id} not found")
            if not identity_supplied and meeting.status == "cancelled":
                return None
            params = dict(meeting.visual_breakdown_params or {})
            if params.get("run_epoch") != run_epoch:
                return None if identity_supplied else int(params.get("attempts") or 0)
            if identity_supplied and meeting.visual_breakdown_status != "processing":
                return None
            attempts = int(params.get("attempts") or 0) + 1
            params["attempts"] = attempts
            meeting.visual_breakdown_params = params
            session.add(meeting)
            session.commit()
            return attempts

    def update_visual_breakdown_progress(
        self,
        meeting_id: int,
        run_epoch: str,
        *,
        processing_run_id: int | None = None,
        dispatch_attempt_id: str | None = None,
        sub_status: str | None = None,
    ) -> bool:
        """Update status/heartbeat only for the current visual epoch and attempt."""
        with Session(engine) as session:
            meeting = self._lock_visual_meeting_in_session(
                session, meeting_id, processing_run_id, dispatch_attempt_id
            )
            identity_supplied = processing_run_id is not None or dispatch_attempt_id is not None
            if meeting is None or (not identity_supplied and meeting.status == "cancelled"):
                return False
            params = dict(meeting.visual_breakdown_params or {})
            if (
                params.get("run_epoch") != run_epoch
                or meeting.visual_breakdown_status != "processing"
            ):
                return False
            publish_sub_status = sub_status is not None and meeting.status != "completed"
            if publish_sub_status:
                meeting.status = "processing"
                meeting.sub_status = sub_status
            if meeting.status != "completed":
                meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
        if publish_sub_status:
            self._publish_sub_status(meeting_id, sub_status)
        return True

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
        processing_run_id: int | None = None,
        dispatch_attempt_id: str | None = None,
    ) -> dict:
        """Atomically converge a visual outcome and its segment replacement.

        The returned side-effect flags are claimed in the same transaction. A
        duplicate delivery therefore cannot emit a second telemetry event or
        notification after the first terminal commit.
        """
        from app.services.visual_segments import VisualSegmentService

        if outcome not in VISUAL_BREAKDOWN_TERMINAL_STATUSES:
            raise ValueError(f"Unsupported visual outcome: {outcome}")
        identity_supplied = processing_run_id is not None or dispatch_attempt_id is not None

        with Session(engine) as session:
            meeting = self._lock_visual_meeting_in_session(
                session, meeting_id, processing_run_id, dispatch_attempt_id
            )
            if meeting is None:
                if identity_supplied:
                    return {"applied": False, "meeting_id": meeting_id}
                raise RuntimeError(f"Meeting {meeting_id} not found")
            if not identity_supplied and meeting.status == "cancelled":
                return {"applied": False, "meeting_id": meeting_id}

            params = dict(meeting.visual_breakdown_params or {})
            if (
                params.get("run_epoch") != run_epoch
                or (identity_supplied and meeting.visual_breakdown_status != "processing")
            ):
                return {"applied": False, "meeting_id": meeting_id}

            side_effects = dict(params.get("side_effects") or {})
            if params.get("outcome") in VISUAL_BREAKDOWN_CLOSED_STATUSES:
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
            if identity_supplied and meeting.status != "completed":
                meeting.processing_heartbeat_at = datetime.utcnow()
            session.add(meeting)
            session.commit()

            if not identity_supplied:
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

    def mark_failed(
        self,
        meeting_id: int,
        error_message: str,
        *,
        processing_run_id: int | None = None,
        dispatch_attempt_id: str | None = None,
        preserve_existing_summary: bool = False,
    ) -> Optional[Meeting]:
        """Fail the current attempt; visual-only failures stay off meeting status."""
        with Session(engine) as session:
            meeting = session.exec(
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            ).first()
            if meeting is None:
                return None

            is_visual_breakdown_run = False
            if processing_run_id is not None:
                if dispatch_attempt_id is None:
                    return None
                run = session.exec(
                    select(MeetingProcessingRun)
                    .where(MeetingProcessingRun.id == processing_run_id)
                    .with_for_update()
                ).first()
                if (
                    run is None
                    or run.meeting_id != meeting.id
                    or meeting.current_processing_run_id != run.id
                    or run.dispatch_attempt_id != dispatch_attempt_id
                ):
                    return None
                is_visual_breakdown_run = run.trigger == "visual_breakdown"
                if is_visual_breakdown_run:
                    if (
                        run.status not in {"queued", "running", "failed"}
                        or meeting.status != "completed"
                    ):
                        return None
                elif (
                    run.status not in {"queued", "running", "failed"}
                    or meeting.status not in {"queued", "processing"}
                ):
                    return None

            if is_visual_breakdown_run:
                meeting.visual_breakdown_status = "failed"
                meeting.visual_breakdown_error = sanitize_message(error_message)
                params = dict(meeting.visual_breakdown_params or {})
                params["outcome"] = "failed"
                meeting.visual_breakdown_params = params
            else:
                meeting.status = "failed"
                meeting.sub_status = None
                # Only clobber the stored summary when it is missing or a
                # failure marker/placeholder; reusing summary_is_usable keeps
                # the durable success summary on request.
                if not preserve_existing_summary or not summary_is_usable(
                    meeting.summary_text
                ):
                    meeting.summary_text = f"[Error: {error_message}]"

            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            return meeting

    def add_segment(self, meeting_id: int, start: float, end: float, text: str) -> TranscriptSegment:
        segment = TranscriptSegment(
            meeting_id=meeting_id,
            start_time=start,
            end_time=end,
            text=text
        )
        return self.save(segment)

    def add_segment_for_user(
        self, meeting_id: int, user_id: int, start: float, end: float, text: str
    ) -> TranscriptSegment:
        """Append a realtime segment only while the caller still has editor access."""

        with Session(engine) as session:
            self.require_editor_in_session(session, meeting_id, user_id)
            segment = TranscriptSegment(
                meeting_id=meeting_id,
                start_time=start,
                end_time=end,
                text=text,
            )
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

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
        saved = self.save(meeting)
        self._enqueue_embedding(saved)
        return saved

    def update_summary_for_user(
        self, meeting_id: int, user_id: int, summary_text: str
    ) -> Optional[Meeting]:
        """Persist a summary only after an in-transaction editor check."""

        with Session(engine) as session:
            meeting = self.require_editor_in_session(session, meeting_id, user_id)
            if meeting.original_summary_text is None and meeting.summary_text is not None:
                meeting.original_summary_text = meeting.summary_text
            meeting.summary_text = summary_text
            meeting.summary_edited = True
            session.add(meeting)
            session.commit()
            session.refresh(meeting)

        self._enqueue_embedding(meeting)
        return meeting

    def restore_summary(self, meeting_id: int) -> Optional[Meeting]:
        """Restore the original AI-generated summary."""
        meeting = self.get(Meeting, meeting_id)
        if not meeting:
            return None
        if meeting.original_summary_text is None:
            return None
        meeting.summary_text = meeting.original_summary_text
        meeting.summary_edited = False
        saved = self.save(meeting)
        self._enqueue_embedding(saved)
        return saved

    def restore_summary_for_user(
        self, meeting_id: int, user_id: int
    ) -> Optional[Meeting]:
        """Restore a summary only after an in-transaction editor check."""

        with Session(engine) as session:
            meeting = self.require_editor_in_session(session, meeting_id, user_id)
            if meeting.original_summary_text is None:
                return None
            meeting.summary_text = meeting.original_summary_text
            meeting.summary_edited = False
            session.add(meeting)
            session.commit()
            session.refresh(meeting)

        self._enqueue_embedding(meeting)
        return meeting

    @staticmethod
    def _enqueue_embedding(meeting: Meeting) -> None:
        if meeting.id is None:
            return
        try:
            from app.worker import reindex_meeting

            reindex_meeting.delay(meeting.id)
        except Exception:
            logger.warning(
                "Failed to enqueue meeting reindex meeting_id=%s",
                meeting.id,
                exc_info=True,
            )

    def create_from_youtube(
        self,
        url: str,
        owner_id: int,
        storage_provider: str = "minio",
    ) -> Meeting:
        """Create a meeting record for YouTube ingestion.

        Starts at 'queued' status (skips 'pending_upload' since there's no file upload step).
        The caller snapshots the selected storage provider before dispatching work.
        """
        meeting = Meeting(
            title="YouTube Video",
            source_type="youtube",
            source_url=url,
            status="queued",
            owner_id=owner_id,
            storage_provider=storage_provider,
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
            if field in {"transcript_text", "transliterated_text", "summary_text", "structured_output", "structured_output_status"}:
                self._enqueue_embedding(meeting)
            return meeting

    def update_field_for_user(
        self, meeting_id: int, user_id: int, field: str, value
    ) -> Optional[Meeting]:
        """Update a user-facing meeting field after an in-transaction editor check."""

        with Session(engine) as session:
            meeting = self.require_editor_in_session(session, meeting_id, user_id)
            setattr(meeting, field, value)
            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            return meeting

    def delete_meeting(self, meeting_id: int) -> bool:
        try:
            from app.worker import delete_meeting_vectors

            delete_meeting_vectors.delay(meeting_id)
        except Exception:
            pass
        return self.delete(Meeting, meeting_id)

meeting_service = MeetingService()
