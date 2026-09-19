# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Best-effort durable audit trail for meeting processing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

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

logger = get_logger(__name__)

_MESSAGE_LIMIT = 1000
_METADATA_VALUE_LIMIT = 200
_METADATA_ITEM_LIMIT = 25
_URL_RE = re.compile(r"(?i)\b(?:https?|s3|gs)://[^\s)\]}\"']+")
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]+|api[_-]?key\s*[:=]\s*[^\s,;]+|token\s*[:=]\s*[^\s,;]+|password\s*[:=]\s*[^\s,;]+|secret\s*[:=]\s*[^\s,;]+)"
)
_CREDENTIAL_KEY_RE = re.compile(r"(?i)(token|secret|password|credential|api[_-]?key|authorization|cookie|presigned|url)")

RUN_STATUSES = {"queued", "running", "completed", "failed"}
EVENT_STATUSES = {"started", "completed", "failed", "skipped"}


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

    def create_run(self, meeting_id: int, *, trigger: str = "pipeline") -> MeetingProcessingRun | None:
        try:
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                if meeting is None or meeting.owner_id is None:
                    return None
                run = MeetingProcessingRun(
                    meeting_id=meeting_id,
                    owner_id=meeting.owner_id,
                    trigger=sanitize_message(trigger)[:40] if trigger else "pipeline",
                    status="queued",
                )
                session.add(run)
                session.commit()
                session.refresh(run)
                return run
        except Exception:
            logger.warning("processing audit create_run failed meeting_id=%s", meeting_id, exc_info=True)
            return None

    def get_pending_run(self, meeting_id: int) -> MeetingProcessingRun | None:
        try:
            with Session(engine) as session:
                return session.exec(
                    select(MeetingProcessingRun)
                    .where(MeetingProcessingRun.meeting_id == meeting_id)
                    .where(MeetingProcessingRun.status.in_(["queued", "running"]))
                    .order_by(MeetingProcessingRun.created_at.desc(), MeetingProcessingRun.id.desc())
                ).first()
        except Exception:
            logger.warning("processing audit get_pending_run failed meeting_id=%s", meeting_id, exc_info=True)
            return None

    def get_or_create_pending_run(self, meeting_id: int, *, trigger: str = "pipeline") -> MeetingProcessingRun | None:
        return self.get_pending_run(meeting_id) or self.create_run(meeting_id, trigger=trigger)

    def set_root_task(self, run_id: int | None, root_task_id: str | None) -> None:
        if run_id is None or not root_task_id:
            return
        try:
            with Session(engine) as session:
                run = session.get(MeetingProcessingRun, run_id)
                if run is None:
                    return
                run.root_task_id = sanitize_message(root_task_id)[:255]
                session.add(run)
                session.commit()
        except Exception:
            logger.warning("processing audit set_root_task failed run_id=%s", run_id, exc_info=True)

    def stage_started(
        self,
        *,
        run_id: int | None,
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
            with Session(engine) as session:
                run = session.get(MeetingProcessingRun, run_id)
                if run is None:
                    return None
                if run.status == "queued":
                    run.status = "running"
                    run.started_at = run.started_at or now
                    session.add(run)
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
                    stage=stage,
                    event_type="started",
                    status="started",
                    task_id=(sanitize_message(task_id)[:255] if task_id else None),
                    message=sanitize_message(message) or f"{stage} started",
                    metadata_=sanitize_metadata(metadata),
                    created_at=now,
                    started_at=now,
                )
                session.add(event)
                session.commit()
                session.refresh(event)
                return event
        except Exception:
            logger.warning("processing audit stage_started failed run_id=%s stage=%s", run_id, stage, exc_info=True)
            return None

    def stage_completed(
        self,
        *,
        run_id: int | None,
        meeting_id: int,
        stage: str,
        task_id: str | None = None,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._stage_terminal(
            run_id=run_id,
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
        meeting_id: int,
        stage: str,
        task_id: str | None = None,
        error: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._stage_terminal(
            run_id=run_id,
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
                if event_type == "failed":
                    existing = session.exec(
                        select(MeetingProcessingEvent)
                        .where(MeetingProcessingEvent.run_id == run_id)
                        .where(MeetingProcessingEvent.meeting_id == meeting_id)
                        .where(MeetingProcessingEvent.stage == stage)
                        .where(MeetingProcessingEvent.event_type == "failed")
                        .where(MeetingProcessingEvent.task_id == safe_task_id)
                    ).first()
                    if existing is not None:
                        return
                event = MeetingProcessingEvent(
                    run_id=run_id,
                    meeting_id=meeting_id,
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

    def run_completed(self, run_id: int | None, *, message: Any = None) -> None:
        self._run_terminal(run_id, status="completed", final_error=None, message=message)

    def run_failed(self, run_id: int | None, *, error: Any = None) -> None:
        self._run_terminal(run_id, status="failed", final_error=sanitize_message(error), message=None)

    def _run_terminal(self, run_id: int | None, *, status: str, final_error: str | None, message: Any) -> None:
        if run_id is None:
            return
        try:
            now = datetime.utcnow()
            with Session(engine) as session:
                run = session.get(MeetingProcessingRun, run_id)
                if run is None:
                    return
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
                    return

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
                        stage="run",
                        event_type=status,
                        status=status,
                        message=sanitize_message(message) if status == "completed" else None,
                        error=(run.final_error if status == "failed" else None),
                        created_at=now,
                        completed_at=now,
                    ))
                session.commit()
        except Exception:
            logger.warning("processing audit run terminal failed run_id=%s", run_id, exc_info=True)

    def list_for_meeting(self, *, meeting_id: int, owner_id: int) -> MeetingProcessingAuditRead:
        with Session(engine) as session:
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
            return MeetingProcessingAuditRead(meeting_id=meeting_id, runs=run_reads)


meeting_processing_audit = MeetingProcessingAuditService()
