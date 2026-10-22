# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Best-effort durable audit trail for meeting processing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db.engine import engine
from app.models import Meeting
from app.models.processing_audit import (
    MeetingProcessingAuditRead,
    MeetingProcessingEvent,
    MeetingProcessingEventRead,
    MeetingProcessingRun,
    MeetingProcessingRunRead,
)
from app.services.meeting_recovery import (
    valid_resume_completed_stages,
)

logger = get_logger(__name__)

_MESSAGE_LIMIT = 1000
_METADATA_VALUE_LIMIT = 200
_METADATA_ITEM_LIMIT = 25
_URL_RE = re.compile(r"(?i)\b(?:https?|s3|gs)://[^\s)\]}\"']+")
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]+|api[_-]?key\s*[:=]\s*[^\s,;]+|token\s*[:=]\s*[^\s,;]+|password\s*[:=]\s*[^\s,;]+|secret\s*[:=]\s*[^\s,;]+)"
)
_CREDENTIAL_KEY_RE = re.compile(r"(?i)(token|secret|password|credential|api[_-]?key|authorization|cookie|presigned|url)")

RUN_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}
EVENT_STATUSES = {"started", "completed", "failed", "skipped", "cancelled"}
_ACTIVE_RUN_STATUSES = ("queued", "running")


def sanitize_message(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", " ")
    text = _URL_RE.sub("[redacted-url]", text)
    text = _CREDENTIAL_VALUE_RE.sub("[redacted-credential]", text)
    text = " ".join(text.split())
    if len(text) > _MESSAGE_LIMIT:
        return text[: _MESSAGE_LIMIT - 1] + "…"
    return text


def _sanitize_metadata_value(key: str, value: Any) -> Any | None:
    if _CREDENTIAL_KEY_RE.search(key):
        return "[redacted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = sanitize_message(value) or ""
        if len(text) > _METADATA_VALUE_LIMIT:
            return text[: _METADATA_VALUE_LIMIT - 1] + "…"
        return text
    return None


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    sanitized: dict[str, Any] = {}
    for key, value in list(metadata.items())[:_METADATA_ITEM_LIMIT]:
        if not isinstance(key, str) or not key:
            continue
        safe_value = _sanitize_metadata_value(key, value)
        if safe_value is not None:
            sanitized[key[:80]] = safe_value
    return sanitized or None


class MeetingProcessingAuditService:
    """Persist and read bounded, owner-scoped processing audit records."""

    @staticmethod
    def _locked_current_run(
        session: Session,
        run_id: int | None,
        meeting_id: int | None = None,
        attempt_id: str | None = None,
        *,
        allow_terminal: bool = False,
    ) -> tuple[Meeting | None, MeetingProcessingRun | None]:
        if run_id is None:
            return None, None
        if meeting_id is None:
            candidate = session.get(MeetingProcessingRun, run_id)
            if candidate is None:
                return None, None
            meeting_id = candidate.meeting_id
        meeting = session.exec(
            select(Meeting).where(Meeting.id == meeting_id).with_for_update()
        ).first()
        if meeting is None:
            return None, None
        run = session.exec(
            select(MeetingProcessingRun)
            .where(MeetingProcessingRun.id == run_id)
            .with_for_update()
        ).first()
        if (
            run is None
            or run.meeting_id != meeting.id
            or meeting.current_processing_run_id != run.id
            or (run.status not in _ACTIVE_RUN_STATUSES and not allow_terminal)
            or (attempt_id is not None and run.dispatch_attempt_id != attempt_id)
        ):
            return None, None
        return meeting, run

    def create_run_in_session(
        self, session: Session, meeting: Meeting, *, trigger: str = "pipeline"
    ) -> MeetingProcessingRun | None:
        """Create and install a new generation while the caller holds the meeting lock."""
        if meeting.id is None or meeting.owner_id is None:
            return None
        current = (
            session.get(MeetingProcessingRun, meeting.current_processing_run_id)
            if meeting.current_processing_run_id is not None
            else None
        )
        if current is not None and current.status in _ACTIVE_RUN_STATUSES:
            return None
        run = MeetingProcessingRun(
            meeting_id=meeting.id,
            owner_id=meeting.owner_id,
            trigger=sanitize_message(trigger)[:40] if trigger else "pipeline",
            status="queued",
            dispatch_attempt_id=uuid4().hex,
        )
        session.add(run)
        session.flush()
        meeting.current_processing_run_id = run.id
        session.add(meeting)
        return run

    def _get_or_create_pending_run(
        self,
        meeting_id: int,
        *,
        trigger: str,
        rotate_attempt: bool,
    ) -> MeetingProcessingRun | None:
        with Session(engine) as session:
            meeting = session.exec(
                select(Meeting).where(Meeting.id == meeting_id).with_for_update()
            ).first()
            if meeting is None:
                return None
            current = (
                session.get(MeetingProcessingRun, meeting.current_processing_run_id)
                if meeting.current_processing_run_id is not None
                else None
            )
            created = current is None or current.status not in _ACTIVE_RUN_STATUSES
            run = (
                self.create_run_in_session(session, meeting, trigger=trigger)
                if created
                else current
            )
            if run is None:
                return None
            if rotate_attempt:
                run.dispatch_attempt_id = uuid4().hex
                session.add(run)
            if created or rotate_attempt:
                session.commit()
                session.refresh(run)
            return run

    def create_run(self, meeting_id: int, *, trigger: str = "pipeline") -> MeetingProcessingRun | None:
        try:
            return self._get_or_create_pending_run(
                meeting_id, trigger=trigger, rotate_attempt=False
            )
        except Exception:
            logger.warning("processing audit create_run failed meeting_id=%s", meeting_id, exc_info=True)
            return None

    def create_resummarize_run(
        self, meeting_id: int
    ) -> tuple[MeetingProcessingRun, tuple[str, str | None, datetime | None]] | None:
        """Atomically admit one standalone summary run and claim meeting progress."""
        try:
            with Session(engine) as session:
                meeting = session.exec(
                    select(Meeting).where(Meeting.id == meeting_id).with_for_update()
                ).first()
                if meeting is None or meeting.status in {"processing", "queued"}:
                    return None
                previous_state = (
                    meeting.status,
                    meeting.sub_status,
                    meeting.processing_heartbeat_at,
                )
                run = self.create_run_in_session(
                    session, meeting, trigger="resummarize"
                )
                if run is None or run.id is None or not run.dispatch_attempt_id:
                    return None
                meeting.status = "processing"
                meeting.sub_status = "summarizing"
                meeting.processing_heartbeat_at = datetime.utcnow()
                session.add(meeting)
                session.commit()
                session.refresh(run)
                return run, previous_state
        except Exception:
            logger.warning(
                "processing audit resummarize admission failed meeting_id=%s",
                meeting_id,
                exc_info=True,
            )
            return None

    def create_visual_breakdown_run(
        self, meeting_id: int
    ) -> tuple[MeetingProcessingRun, str] | None:
        """Atomically admit standalone visual analysis without reopening a meeting."""
        try:
            with Session(engine) as session:
                meeting = session.exec(
                    select(Meeting).where(Meeting.id == meeting_id).with_for_update()
                ).first()
                if (
                    meeting is None
                    or meeting.status != "completed"
                    or meeting.visual_breakdown_status in {"queued", "processing"}
                ):
                    return None

                active_run = session.exec(
                    select(MeetingProcessingRun)
                    .where(MeetingProcessingRun.meeting_id == meeting_id)
                    .where(MeetingProcessingRun.status.in_(_ACTIVE_RUN_STATUSES))
                    .with_for_update()
                ).first()
                if active_run is not None:
                    return None

                run = self.create_run_in_session(
                    session, meeting, trigger="visual_breakdown"
                )
                if run is None or run.id is None or not run.dispatch_attempt_id:
                    return None

                run_epoch = uuid4().hex
                params = dict(meeting.visual_breakdown_params or {})
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
                session.refresh(run)
                return run, run_epoch
        except Exception:
            logger.warning(
                "processing audit visual breakdown admission failed meeting_id=%s",
                meeting_id,
                exc_info=True,
            )
            return None

    def get_pending_run(self, meeting_id: int) -> MeetingProcessingRun | None:
        try:
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                if meeting is None or meeting.current_processing_run_id is None:
                    return None
                run = session.get(MeetingProcessingRun, meeting.current_processing_run_id)
                return run if run and run.status in _ACTIVE_RUN_STATUSES else None
        except Exception:
            logger.warning("processing audit get_pending_run failed meeting_id=%s", meeting_id, exc_info=True)
            return None

    def get_or_create_pending_run(self, meeting_id: int, *, trigger: str = "pipeline") -> MeetingProcessingRun | None:
        try:
            return self._get_or_create_pending_run(
                meeting_id, trigger=trigger, rotate_attempt=True
            )
        except Exception:
            logger.warning("processing audit get_or_create_pending_run failed meeting_id=%s", meeting_id, exc_info=True)
            return None

    def create_reindex_run(self, meeting_id: int) -> MeetingProcessingRun | None:
        """Admit an audited reindex only when no processing pipeline owns the meeting."""
        try:
            with Session(engine) as session:
                meeting = session.exec(
                    select(Meeting).where(Meeting.id == meeting_id).with_for_update()
                ).first()
                if meeting is None:
                    return None

                current = (
                    session.get(MeetingProcessingRun, meeting.current_processing_run_id)
                    if meeting.current_processing_run_id is not None
                    else None
                )
                if current is not None and current.status in _ACTIVE_RUN_STATUSES:
                    # Active work already owns the meeting. Reusing an active
                    # reindex run could dispatch concurrent delete/upsert batches.
                    return None
                if meeting.status in {"queued", "processing"}:
                    return None

                run = self.create_run_in_session(session, meeting, trigger="reindex")
                if run is None or run.id is None or not run.dispatch_attempt_id:
                    return None

                meeting.status = "processing"
                meeting.sub_status = "indexing_embeddings"
                meeting.processing_heartbeat_at = datetime.utcnow()
                session.add(meeting)
                session.commit()
                session.refresh(run)
                return run
        except Exception:
            logger.warning(
                "processing audit reindex admission failed meeting_id=%s",
                meeting_id,
                exc_info=True,
            )
            return None

    def cancel_run(self, *, meeting_id: int, run_id: int, owner_id: int) -> bool:
        """Cancel only the owner's exact active generation in one locked transaction."""
        now = datetime.utcnow()
        try:
            with Session(engine) as session:
                meeting, run = self._locked_current_run(session, run_id, meeting_id)
                if meeting is None or run is None or meeting.owner_id != owner_id or run.owner_id != owner_id:
                    return False
                run.status = "cancelled"
                run.completed_at = now
                run.final_error = None
                if run.trigger == "visual_breakdown":
                    meeting.visual_breakdown_status = "cancelled"
                    meeting.visual_breakdown_error = None
                    params = dict(meeting.visual_breakdown_params or {})
                    params["outcome"] = "cancelled"
                    meeting.visual_breakdown_params = params
                else:
                    meeting.status = "cancelled"
                    meeting.sub_status = None
                    meeting.processing_heartbeat_at = None
                session.add(run)
                session.add(meeting)
                session.add(MeetingProcessingEvent(
                    run_id=run.id,
                    meeting_id=meeting.id,
                    dispatch_attempt_id=run.dispatch_attempt_id,
                    stage="run",
                    event_type="cancelled",
                    status="cancelled",
                    message="Processing cancelled by the meeting owner; committed work may remain.",
                    created_at=now,
                    completed_at=now,
                ))
                session.commit()
                return True
        except Exception:
            logger.warning("processing audit cancel_run failed meeting_id=%s run_id=%s", meeting_id, run_id, exc_info=True)
            raise

    def update_meeting_state_for_attempt(
        self,
        *,
        meeting_id: int,
        run_id: int,
        attempt_id: str,
        status: str,
        sub_status: str | None,
        heartbeat_at: datetime | None,
    ) -> bool:
        """Change meeting progress only while its exact run attempt is current."""
        if not attempt_id:
            return False
        with Session(engine) as session:
            meeting, _run = self._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if meeting is None:
                return False
            meeting.status = status
            meeting.sub_status = sub_status
            meeting.processing_heartbeat_at = heartbeat_at
            session.add(meeting)
            session.commit()
            return True

    def set_root_task(
        self,
        run_id: int | None,
        root_task_id: str | None,
        *,
        attempt_id: str | None = None,
    ) -> bool:
        if run_id is None or not root_task_id:
            return False
        try:
            with Session(engine) as session:
                _, run = self._locked_current_run(
                    session,
                    run_id,
                    attempt_id=attempt_id,
                    allow_terminal=True,
                )
                if run is None:
                    return False
                run.root_task_id = sanitize_message(root_task_id)[:255]
                session.add(run)
                session.commit()
                return True
        except Exception:
            logger.warning("processing audit set_root_task failed run_id=%s", run_id, exc_info=True)
            return False

    def stage_started(
        self,
        *,
        run_id: int | None,
        attempt_id: str | None = None,
        meeting_id: int,
        stage: str,
        task_id: str | None = None,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MeetingProcessingEvent | None:
        if run_id is None:
            return None
        try:
            now = datetime.utcnow()
            safe_task_id = sanitize_message(task_id)[:255] if task_id else None
            with Session(engine) as session:
                _, run = self._locked_current_run(session, run_id, meeting_id, attempt_id)
                if run is None:
                    return None
                existing = None
                if stage in {
                    "stage_transliterate",
                    "stage_extract_intelligence",
                    "stage_youtube_download",
                } and attempt_id is not None:
                    started_query = (
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == run_id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
                        .where(MeetingProcessingEvent.stage == stage)
                        .where(MeetingProcessingEvent.event_type == "started")
                    )
                    existing = session.exec(started_query).first()
                elif safe_task_id is not None:
                    started_query = (
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == run_id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.stage == stage)
                        .where(MeetingProcessingEvent.event_type == "started")
                        .where(MeetingProcessingEvent.task_id == safe_task_id)
                    )
                    if attempt_id is not None:
                        started_query = started_query.where(
                            MeetingProcessingEvent.dispatch_attempt_id == attempt_id
                        )
                    existing = session.exec(started_query).first()
                if (
                    stage in {
                        "stage_transliterate",
                        "stage_extract_intelligence",
                        "stage_youtube_download",
                    }
                    and existing is not None
                    and (existing.metadata_ or {}).get("provider_claimed") is True
                ):
                    return existing

                run.status = "running"
                run.started_at = run.started_at or now
                session.add(run)
                if existing is not None:
                    merged_metadata = dict(existing.metadata_ or {})
                    merged_metadata.update(metadata or {})
                    existing.metadata_ = sanitize_metadata(merged_metadata)
                    existing.message = sanitize_message(message) or existing.message
                    existing.task_id = existing.task_id or safe_task_id
                    if stage != "stage_transliterate":
                        existing.started_at = existing.started_at or now
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    return existing
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage=stage,
                    event_type="started",
                    status="started",
                    task_id=safe_task_id,
                    message=sanitize_message(message) or f"{stage} started",
                    metadata_=sanitize_metadata(metadata),
                    created_at=now,
                    started_at=now if stage != "stage_transliterate" else None,
                )
                session.add(event)
                session.commit()
                session.refresh(event)
                return event
        except Exception:
            logger.warning("processing audit stage_started failed run_id=%s stage=%s", run_id, stage, exc_info=True)
            return None

    def claim_transliteration(
        self,
        *,
        run_id: int,
        attempt_id: str,
        meeting_id: int,
        task_id: str | None,
    ) -> MeetingProcessingEvent | None:
        """Atomically admit transliteration once for the exact dispatch attempt."""
        if not attempt_id:
            return None
        now = datetime.utcnow()
        claim_query = (
            select(MeetingProcessingEvent)
            .where(MeetingProcessingEvent.run_id == run_id)
            .where(MeetingProcessingEvent.meeting_id == meeting_id)
            .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
            .where(MeetingProcessingEvent.stage == "stage_transliterate")
            .where(MeetingProcessingEvent.event_type == "started")
        )
        with Session(engine) as session:
            _, run = self._locked_current_run(session, run_id, meeting_id, attempt_id)
            if run is None:
                return None
            event = session.exec(claim_query).first()
            if event is not None and (event.metadata_ or {}).get("provider_claimed") is True:
                return None

            run.status = "running"
            run.started_at = run.started_at or now
            session.add(run)
            claim_metadata = dict(event.metadata_ or {}) if event is not None else {}
            claim_metadata.update({
                "celery_task_name": "stage_transliterate",
                "provider_claimed": True,
            })
            if event is None:
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage="stage_transliterate",
                    event_type="started",
                    status="started",
                    task_id=(sanitize_message(task_id)[:255] if task_id else None),
                    message="stage_transliterate started",
                    metadata_=sanitize_metadata(claim_metadata),
                    created_at=now,
                    started_at=now,
                )
            else:
                event.metadata_ = sanitize_metadata(claim_metadata)
                event.started_at = now
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.exec(claim_query).first()
                if winner is not None and (winner.metadata_ or {}).get("provider_claimed") is True:
                    return None
                raise
            session.refresh(event)
            return event

    def claim_summary(
        self,
        *,
        run_id: int,
        attempt_id: str,
        meeting_id: int,
        task_id: str | None,
    ) -> MeetingProcessingEvent | None:
        """Atomically admit summary generation once for the exact attempt."""
        if not attempt_id:
            return None
        now = datetime.utcnow()
        claim_query = (
            select(MeetingProcessingEvent)
            .where(MeetingProcessingEvent.run_id == run_id)
            .where(MeetingProcessingEvent.meeting_id == meeting_id)
            .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
            .where(MeetingProcessingEvent.stage == "stage_summarize")
            .where(MeetingProcessingEvent.event_type == "started")
        )
        with Session(engine) as session:
            _, run = self._locked_current_run(session, run_id, meeting_id, attempt_id)
            if run is None:
                return None
            event = session.exec(claim_query).first()
            if event is not None and (event.metadata_ or {}).get("provider_claimed") is True:
                return None

            run.status = "running"
            run.started_at = run.started_at or now
            session.add(run)
            claim_metadata = dict(event.metadata_ or {}) if event is not None else {}
            claim_metadata.update({
                "celery_task_name": "stage_summarize",
                "provider_claimed": True,
            })
            if event is None:
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage="stage_summarize",
                    event_type="started",
                    status="started",
                    task_id=(sanitize_message(task_id)[:255] if task_id else None),
                    message="stage_summarize started",
                    metadata_=sanitize_metadata(claim_metadata),
                    created_at=now,
                    started_at=now,
                )
            else:
                event.metadata_ = sanitize_metadata(claim_metadata)
                event.started_at = now
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.exec(claim_query).first()
                if winner is not None and (winner.metadata_ or {}).get("provider_claimed") is True:
                    return None
                raise
            session.refresh(event)
            return event

    def claim_intelligence(
        self,
        *,
        run_id: int,
        attempt_id: str,
        meeting_id: int,
        task_id: str | None,
        stage: str = "stage_extract_intelligence",
    ) -> MeetingProcessingEvent | None:
        """Atomically admit one split model stage for the exact attempt.

        The legacy intelligence task uses the default ``stage``; the split
        title/highlights/structured stages reuse this row-locked claim with
        their own stage name so a duplicate delivery is rejected before any
        provider I/O.
        """
        allowed_stages = (
            "stage_extract_intelligence",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
        )
        if stage not in allowed_stages:
            raise ValueError(f"Unsupported model stage claim: {stage}")
        if not attempt_id:
            return None
        now = datetime.utcnow()
        claim_query = (
            select(MeetingProcessingEvent)
            .where(MeetingProcessingEvent.run_id == run_id)
            .where(MeetingProcessingEvent.meeting_id == meeting_id)
            .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
            .where(MeetingProcessingEvent.stage == stage)
            .where(MeetingProcessingEvent.event_type == "started")
        )
        with Session(engine) as session:
            _, run = self._locked_current_run(session, run_id, meeting_id, attempt_id)
            if run is None:
                return None
            event = session.exec(claim_query).first()
            if event is not None and (event.metadata_ or {}).get("provider_claimed") is True:
                return None

            run.status = "running"
            run.started_at = run.started_at or now
            session.add(run)
            claim_metadata = dict(event.metadata_ or {}) if event is not None else {}
            claim_metadata.update({
                "celery_task_name": stage,
                "provider_claimed": True,
            })
            if event is None:
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage=stage,
                    event_type="started",
                    status="started",
                    task_id=(sanitize_message(task_id)[:255] if task_id else None),
                    message=f"{stage} started",
                    metadata_=sanitize_metadata(claim_metadata),
                    created_at=now,
                    started_at=now,
                )
            else:
                event.metadata_ = sanitize_metadata(claim_metadata)
                event.started_at = event.started_at or now
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.exec(claim_query).first()
                if winner is not None and (winner.metadata_ or {}).get("provider_claimed") is True:
                    return None
                raise
            session.refresh(event)
            return event

    def claim_youtube_download(
        self,
        *,
        run_id: int,
        attempt_id: str,
        meeting_id: int,
        task_id: str | None,
    ) -> MeetingProcessingEvent | None:
        """Admit YouTube download/upload once for the exact dispatch attempt."""
        if not attempt_id:
            return None
        now = datetime.utcnow()
        claim_query = (
            select(MeetingProcessingEvent)
            .where(MeetingProcessingEvent.run_id == run_id)
            .where(MeetingProcessingEvent.meeting_id == meeting_id)
            .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
            .where(MeetingProcessingEvent.stage == "stage_youtube_download")
            .where(MeetingProcessingEvent.event_type == "started")
        )
        with Session(engine) as session:
            _, run = self._locked_current_run(session, run_id, meeting_id, attempt_id)
            if run is None:
                return None
            event = session.exec(claim_query).first()
            if event is not None and (event.metadata_ or {}).get("provider_claimed") is True:
                return None

            run.status = "running"
            run.started_at = run.started_at or now
            session.add(run)
            claim_metadata = dict(event.metadata_ or {}) if event is not None else {}
            claim_metadata.update({
                "celery_task_name": "stage_youtube_download",
                "provider_claimed": True,
            })
            if event is None:
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage="stage_youtube_download",
                    event_type="started",
                    status="started",
                    task_id=(sanitize_message(task_id)[:255] if task_id else None),
                    message="stage_youtube_download started",
                    metadata_=sanitize_metadata(claim_metadata),
                    created_at=now,
                    started_at=now,
                )
            else:
                event.metadata_ = sanitize_metadata(claim_metadata)
                event.started_at = event.started_at or now
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.exec(claim_query).first()
                if winner is not None and (winner.metadata_ or {}).get("provider_claimed") is True:
                    return None
                raise
            session.refresh(event)
            return event

    def promote_youtube_audio(
        self,
        *,
        run_id: int,
        attempt_id: str,
        meeting_id: int,
        owner_id: int,
        file_key: str,
    ) -> bool:
        """Install successful YouTube media only for its exact active attempt."""
        if not attempt_id or not file_key:
            return False
        attempt_token = sha256(attempt_id.encode("utf-8")).hexdigest()
        expected_prefix = (
            f"users/{owner_id}/meetings/youtube/{meeting_id}/runs/{run_id}/"
            f"attempts/{attempt_token}/"
        )
        if not file_key.startswith(expected_prefix):
            return False
        claim_query = (
            select(MeetingProcessingEvent)
            .where(MeetingProcessingEvent.run_id == run_id)
            .where(MeetingProcessingEvent.meeting_id == meeting_id)
            .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
            .where(MeetingProcessingEvent.stage == "stage_youtube_download")
            .where(MeetingProcessingEvent.event_type == "started")
        )
        with Session(engine) as session:
            meeting, _run = self._locked_current_run(
                session, run_id, meeting_id, attempt_id
            )
            if meeting is None or meeting.owner_id != owner_id:
                return False
            claim = session.exec(claim_query).first()
            if claim is None or (claim.metadata_ or {}).get("provider_claimed") is not True:
                return False
            meeting.file_path = file_key
            claim_metadata = dict(claim.metadata_ or {})
            claim_metadata["media_promoted"] = True
            claim.metadata_ = sanitize_metadata(claim_metadata)
            session.add(meeting)
            session.add(claim)
            session.commit()
            return True

    def youtube_audio_was_promoted(
        self, *, run_id: int, attempt_id: str, meeting_id: int
    ) -> bool:
        """Keep an attempt object if promotion committed before a worker error."""
        claim = select(MeetingProcessingEvent).where(
            MeetingProcessingEvent.run_id == run_id,
            MeetingProcessingEvent.meeting_id == meeting_id,
            MeetingProcessingEvent.dispatch_attempt_id == attempt_id,
            MeetingProcessingEvent.stage == "stage_youtube_download",
            MeetingProcessingEvent.event_type == "started",
        )
        with Session(engine) as session:
            event = session.exec(claim).first()
            return bool(event and (event.metadata_ or {}).get("media_promoted") is True)

    def stage_completed(
        self,
        *,
        run_id: int | None,
        attempt_id: str | None = None,
        meeting_id: int,
        stage: str,
        task_id: str | None = None,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._stage_terminal(
            run_id=run_id,
            attempt_id=attempt_id,
            meeting_id=meeting_id,
            stage=stage,
            event_type="completed",
            status="completed",
            task_id=task_id,
            message=message or f"{stage} completed",
            error=None,
            metadata=metadata,
        )

    def stage_failed(
        self,
        *,
        run_id: int | None,
        attempt_id: str | None = None,
        meeting_id: int,
        stage: str,
        task_id: str | None = None,
        error: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._stage_terminal(
            run_id=run_id,
            attempt_id=attempt_id,
            meeting_id=meeting_id,
            stage=stage,
            event_type="failed",
            status="failed",
            task_id=task_id,
            message=None,
            error=error,
            metadata=metadata,
        )

    def _stage_terminal(
        self,
        *,
        run_id: int | None,
        attempt_id: str | None,
        meeting_id: int,
        stage: str,
        event_type: str,
        status: str,
        task_id: str | None,
        message: Any,
        error: Any,
        metadata: Mapping[str, Any] | None,
    ) -> None:
        if run_id is None:
            return
        try:
            now = datetime.utcnow()
            safe_task_id = sanitize_message(task_id)[:255] if task_id else None
            with Session(engine) as session:
                # A failure callback can mark the run failed before Celery's
                # task_failure signal records its stage event. Accept that late
                # event so the stage is closed instead of staying "in progress".
                _, run = self._locked_current_run(
                    session,
                    run_id,
                    meeting_id,
                    attempt_id,
                    allow_terminal=event_type == "failed",
                )
                if run is None:
                    return
                if event_type == "failed":
                    existing = session.exec(
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == run_id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.dispatch_attempt_id == attempt_id)
                        .where(MeetingProcessingEvent.stage == stage)
                        .where(MeetingProcessingEvent.event_type == "failed")
                        .where(MeetingProcessingEvent.task_id == safe_task_id)
                    ).first()
                    if existing is not None:
                        return
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    dispatch_attempt_id=attempt_id,
                    stage=stage,
                    event_type=event_type,
                    status=status,
                    task_id=safe_task_id,
                    message=sanitize_message(message),
                    error=sanitize_message(error),
                    metadata_=sanitize_metadata(metadata),
                    created_at=now,
                    completed_at=now,
                )
                session.add(event)
                session.commit()
        except Exception:
            logger.warning("processing audit terminal event failed run_id=%s stage=%s", run_id, stage, exc_info=True)

    def stage_completed_event_for_output(
        self,
        session: Session,
        *,
        run: MeetingProcessingRun | None,
        meeting_id: int,
        attempt_id: str,
        stage: str,
        task_id: str | None,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MeetingProcessingEvent | None:
        """Build a stage completed marker inside the caller's output transaction.

        Model output and its resume marker must commit together: the caller
        already holds the locked meeting/run and writes the saved output, and
        adding this marker to the same session makes a crash unable to leave
        durable output without its resume marker (which would repeat the
        durable model work on resume). The caller owns the commit; this helper
        only adds the bounded marker. Returns None (adding nothing) when no
        run can be addressed.
        """
        if run is None or getattr(run, "id", None) is None:
            return None
        now = datetime.utcnow()
        event = MeetingProcessingEvent(
            run_id=run.id,
            meeting_id=meeting_id,
            dispatch_attempt_id=attempt_id,
            stage=stage,
            event_type="completed",
            status="completed",
            task_id=sanitize_message(task_id)[:255] if task_id else None,
            message=sanitize_message(message) or f"{stage} completed",
            metadata_=sanitize_metadata(metadata),
            created_at=now,
            completed_at=now,
        )
        session.add(event)
        return event

    def complete_embedding_in_session(
        self,
        session: Session,
        *,
        run: MeetingProcessingRun,
        meeting_id: int,
        attempt_id: str,
        task_id: str | None,
        point_count: int,
    ) -> bool:
        """Complete an embedding run in the caller's locked meeting transaction."""
        if (
            run.id is None
            or run.meeting_id != meeting_id
            or run.dispatch_attempt_id != attempt_id
            or run.status not in _ACTIVE_RUN_STATUSES
        ):
            return False

        now = datetime.utcnow()
        safe_task_id = sanitize_message(task_id)[:255] if task_id else None
        run.status = "completed"
        run.started_at = run.started_at or now
        run.completed_at = now
        run.final_error = None
        session.add(run)
        session.add(
            MeetingProcessingEvent(
                run_id=run.id,
                meeting_id=meeting_id,
                dispatch_attempt_id=attempt_id,
                stage="stage_embedding",
                event_type="completed",
                status="completed",
                task_id=safe_task_id,
                message="Meeting vectors persisted",
                metadata_=sanitize_metadata({"point_count": point_count}),
                created_at=now,
                completed_at=now,
            )
        )
        session.add(
            MeetingProcessingEvent(
                run_id=run.id,
                meeting_id=meeting_id,
                dispatch_attempt_id=attempt_id,
                stage="run",
                event_type="completed",
                status="completed",
                task_id=safe_task_id,
                message="Processing completed after vector persistence",
                created_at=now,
                completed_at=now,
            )
        )
        return True

    def run_completed(
        self,
        run_id: int | None,
        *,
        attempt_id: str | None = None,
        message: Any = None,
    ) -> bool:
        return self._run_terminal(
            run_id, status="completed", final_error=None, message=message, attempt_id=attempt_id
        )

    def run_failed(
        self, run_id: int | None, *, attempt_id: str | None = None, error: Any = None
    ) -> bool:
        return self._run_terminal(
            run_id,
            status="failed",
            final_error=sanitize_message(error),
            message=None,
            attempt_id=attempt_id,
        )

    def failure_effects_are_eligible(
        self, *, meeting_id: int, run_id: int, attempt_id: str
    ) -> bool:
        """Admit failure effects only for the exact current failed attempt.

        The session (and its row locks) closes before the caller performs any
        network I/O. A later cancellation or retry may race an admitted effect.
        """
        if not attempt_id:
            return False
        with Session(engine) as session:
            meeting = session.exec(
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
            ).first()
            if meeting is None or meeting.current_processing_run_id != run_id:
                return False
            run = session.exec(
                select(MeetingProcessingRun)
                .where(MeetingProcessingRun.id == run_id)
                .with_for_update()
            ).first()
            return bool(
                run is not None
                and run.meeting_id == meeting_id
                and run.dispatch_attempt_id == attempt_id
                and run.status == "failed"
                and meeting.status == "failed"
            )

    async def success_effects_are_eligible(
        self, meeting_id: int, run_id: int, attempt_id: str
    ) -> bool:
        """Admit success effects only for the exact current processing attempt."""
        if not attempt_id:
            return False
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            run = session.get(MeetingProcessingRun, run_id)
            return bool(
                meeting is not None
                and meeting.current_processing_run_id == run_id
                and meeting.status in {"processing", "completed"}
                and run is not None
                and run.meeting_id == meeting_id
                and run.dispatch_attempt_id == attempt_id
                and run.status in {"running", "completed"}
            )

    def previous_resume_lineage(
        self,
        meeting_id: int,
        current_run_id: int | None = None,
        *,
        transcript_text: str | None = None,
    ) -> tuple[int | None, tuple[str, ...]]:
        """Return (immediately previous run id, still-valid completed stages).

        The selection takes whatever run immediately precedes the current one
        for this meeting, regardless of its status. That neighbor is then
        explicitly rejected when it is still active, cancelled, or owned by
        someone else: a cancelled or active neighbor is never crossed into
        older runs, so markers from any earlier run are never carried forward.
        Each completed split-stage marker must additionally belong to the
        previous run's own dispatch attempt (stale attempt events are
        rejected) and still match the current transcript fingerprint. This is
        the only lineage source for a resume.
        """
        try:
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                if meeting is None or meeting.owner_id is None:
                    return None, ()
                statement = (
                    select(MeetingProcessingRun)
                    .where(MeetingProcessingRun.meeting_id == meeting_id)
                    .order_by(
                        MeetingProcessingRun.created_at.desc(),
                        MeetingProcessingRun.id.desc(),
                    )
                    .limit(1)
                )
                if current_run_id is not None:
                    statement = statement.where(MeetingProcessingRun.id != current_run_id)
                previous = session.exec(statement).first()
                if (
                    previous is None
                    or previous.owner_id != meeting.owner_id
                    or previous.status in _ACTIVE_RUN_STATUSES
                    or previous.status == "cancelled"
                ):
                    return None, ()
                attempt_id = getattr(previous, "dispatch_attempt_id", None)
                events = [
                    event
                    for event in session.exec(
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == previous.id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.event_type == "completed")
                        .order_by(MeetingProcessingEvent.id.asc())
                    ).all()
                    if getattr(event, "dispatch_attempt_id", None) == attempt_id
                ]
                return previous.id, valid_resume_completed_stages(
                    events, transcript_text=transcript_text
                )
        except Exception:
            logger.warning(
                "processing audit previous_resume_lineage failed meeting_id=%s",
                meeting_id,
                exc_info=True,
            )
            return None, ()

    def carry_forward_completed_stages(
        self,
        *,
        meeting_id: int,
        source_run_id: int,
        stages: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Copy validated previous-run completed markers into the current run.

        Each copied marker is marked used-as-input (``reused``) and keeps the
        transcript fingerprint of the lineage run whose output it proves, so a
        later resume re-validates against the origin epoch, not the copy.
        """
        try:
            now = datetime.utcnow()
            with Session(engine) as session:
                meeting = session.exec(
                    select(Meeting)
                    .where(Meeting.id == meeting_id)
                    .with_for_update()
                ).first()
                if meeting is None or meeting.owner_id is None:
                    return ()
                run_id = meeting.current_processing_run_id
                if run_id is None:
                    return ()
                run = session.exec(
                    select(MeetingProcessingRun)
                    .where(MeetingProcessingRun.id == run_id)
                    .with_for_update()
                ).first()
                if (
                    run is None
                    or run.meeting_id != meeting_id
                    or run.owner_id != meeting.owner_id
                    or run.status not in _ACTIVE_RUN_STATUSES
                    or not run.dispatch_attempt_id
                ):
                    return ()
                source = session.get(MeetingProcessingRun, source_run_id)
                if (
                    source is None
                    or source.meeting_id != meeting_id
                    or source.owner_id != meeting.owner_id
                    or source.status in _ACTIVE_RUN_STATUSES
                    # A cancelled source run's markers prove nothing: the work
                    # was stopped, not finished.
                    or source.status == "cancelled"
                ):
                    return ()
                source_attempt_id = getattr(source, "dispatch_attempt_id", None)
                source_events = [
                    event
                    for event in session.exec(
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == source_run_id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.event_type == "completed")
                        .order_by(MeetingProcessingEvent.id.asc())
                    ).all()
                    # Only markers recorded under the source run's own dispatch
                    # attempt are valid carry sources.
                    if getattr(event, "dispatch_attempt_id", None) == source_attempt_id
                ]
                # The source marker must still describe output for the exact
                # transcript epoch the current run now holds.
                valid = set(
                    valid_resume_completed_stages(
                        source_events, transcript_text=meeting.transcript_text
                    )
                )
                by_stage = {
                    event.stage: event
                    for event in source_events
                    if event.stage in valid
                }
                existing = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.meeting_id == meeting_id)
                    .where(MeetingProcessingEvent.event_type == "completed")
                ).all()
                existing_stages = {event.stage for event in existing}
                carried: list[str] = []
                for stage in stages:
                    source_event = by_stage.get(stage)
                    if source_event is None or stage in existing_stages:
                        continue
                    origin = (
                        (source_event.metadata_ or {}).get("resume_origin_run_id")
                        or source_run_id
                    )
                    session.add(
                        MeetingProcessingEvent(
                            run_id=run.id,
                            meeting_id=meeting_id,
                            dispatch_attempt_id=run.dispatch_attempt_id,
                            stage=stage,
                            event_type="completed",
                            status="completed",
                            message=f"Kept {stage} output from processing run {source_run_id}",
                            metadata_=sanitize_metadata({
                                "reused": True,
                                "resume_origin_run_id": origin,
                                "transcript_fingerprint": (
                                    source_event.metadata_ or {}
                                ).get("transcript_fingerprint"),
                            }),
                            created_at=now,
                            completed_at=now,
                        )
                    )
                    carried.append(stage)
                session.commit()
                return tuple(carried)
        except Exception:
            logger.warning(
                "processing audit carry-forward failed meeting_id=%s source_run_id=%s",
                meeting_id,
                source_run_id,
                exc_info=True,
            )
            return ()

    def _run_terminal(
        self,
        run_id: int | None,
        *,
        status: str,
        final_error: str | None,
        message: Any,
        attempt_id: str | None,
    ) -> bool:
        if run_id is None:
            return False
        try:
            now = datetime.utcnow()
            with Session(engine) as session:
                _, run = self._locked_current_run(session, run_id, attempt_id=attempt_id)
                if run is None:
                    return False
                existing_terminal_event = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.meeting_id == run.meeting_id)
                    .where(MeetingProcessingEvent.stage == "run")
                    .where(MeetingProcessingEvent.event_type == status)
                ).first()

                if run.status == status and existing_terminal_event is not None:
                    if status == "failed" and final_error and not run.final_error:
                        run.final_error = final_error
                        session.add(run)
                        session.commit()
                    return True

                run.status = status
                run.started_at = run.started_at or now
                run.completed_at = run.completed_at or now
                if status == "failed":
                    run.final_error = run.final_error or final_error
                else:
                    run.final_error = final_error
                session.add(run)
                if status in ("completed", "failed") and existing_terminal_event is None:
                    session.add(MeetingProcessingEvent(
                        run_id=run.id,
                        meeting_id=run.meeting_id,
                        dispatch_attempt_id=attempt_id,
                        stage="run",
                        event_type=status,
                        status=status,
                        message=sanitize_message(message) if status == "completed" else None,
                        error=(run.final_error if status == "failed" else None),
                        created_at=now,
                        completed_at=now,
                    ))
                session.commit()
                return True
        except Exception:
            logger.warning("processing audit run terminal failed run_id=%s", run_id, exc_info=True)
            return False

    def list_for_meeting(self, *, meeting_id: int, owner_id: int) -> MeetingProcessingAuditRead:
        with Session(engine) as session:
            meeting = session.exec(
                select(Meeting).where(
                    Meeting.id == meeting_id,
                    Meeting.owner_id == owner_id,
                )
            ).first()
            if meeting is None:
                raise HTTPException(status_code=404, detail="Meeting not found")
            runs = session.exec(
                select(MeetingProcessingRun)
                .where(MeetingProcessingRun.meeting_id == meeting_id)
                .where(MeetingProcessingRun.owner_id == owner_id)
                .order_by(MeetingProcessingRun.created_at.desc(), MeetingProcessingRun.id.desc())
            ).all()
            run_reads: list[MeetingProcessingRunRead] = []
            for run in runs:
                events = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.meeting_id == meeting_id)
                    .order_by(MeetingProcessingEvent.created_at.asc(), MeetingProcessingEvent.id.asc())
                ).all()
                run_reads.append(
                    MeetingProcessingRunRead(
                        id=run.id,
                        meeting_id=run.meeting_id,
                        owner_id=run.owner_id,
                        trigger=run.trigger,
                        status=run.status,
                        dispatch_attempt_id=run.dispatch_attempt_id,
                        root_task_id=run.root_task_id,
                        created_at=run.created_at,
                        started_at=run.started_at,
                        completed_at=run.completed_at,
                        final_error=run.final_error,
                        events=[
                            MeetingProcessingEventRead(
                                id=event.id,
                                run_id=event.run_id,
                                meeting_id=event.meeting_id,
                                stage=event.stage,
                                event_type=event.event_type,
                                status=event.status,
                                task_id=event.task_id,
                                message=event.message,
                                error=event.error,
                                metadata=event.metadata_,
                                created_at=event.created_at,
                                started_at=event.started_at,
                                completed_at=event.completed_at,
                            )
                            for event in events
                        ],
                    )
                )
            return MeetingProcessingAuditRead(
                meeting_id=meeting_id,
                current_run_id=meeting.current_processing_run_id,
                runs=run_reads,
            )


meeting_processing_audit = MeetingProcessingAuditService()
