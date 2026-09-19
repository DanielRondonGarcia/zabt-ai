# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused tests for meeting processing audit persistence and sanitization."""

from unittest.mock import patch

from sqlmodel import SQLModel, Session, select

from app.db.engine import engine
from app.models import Meeting, MeetingProcessingEvent, MeetingProcessingRun, User
from app.services.meeting_processing_audit import meeting_processing_audit, sanitize_message, sanitize_metadata
from app.worker import (
    _build_pipeline_signatures,
    _record_audit_stage_failed,
    _record_audit_stage_succeeded,
    on_stage_failure,
    stage_download,
    stage_embedding,
)


class _FakeRequest:
    def __init__(self, *, task_id: str, args: tuple[int], run_id: int):
        self.id = task_id
        self.task = "stage_embedding"
        self.args = args
        self.headers = {"meeting_processing_run_id": str(run_id)}


class _FakeTask:
    def __init__(self, *, name: str, request: _FakeRequest):
        self.name = name
        self.request = request


def setup_module() -> None:
    SQLModel.metadata.create_all(engine)


def _ensure_user(session: Session, user_id: int) -> None:
    if not session.get(User, user_id):
        session.add(User(id=user_id, email=f"audit{user_id}@test.com", supabase_id=f"audit_{user_id}"))
        session.commit()


def _make_meeting(owner_id: int = 1) -> int:
    with Session(engine) as session:
        _ensure_user(session, owner_id)
        meeting = Meeting(title="audit", owner_id=owner_id, status="queued", file_path="users/1/audio.mp3")
        session.add(meeting)
        session.commit()
        session.refresh(meeting)
        return meeting.id


def _delete_meeting(meeting_id: int) -> None:
    with Session(engine) as session:
        for event in session.exec(select(MeetingProcessingEvent).where(MeetingProcessingEvent.meeting_id == meeting_id)).all():
            session.delete(event)
        for run in session.exec(select(MeetingProcessingRun).where(MeetingProcessingRun.meeting_id == meeting_id)).all():
            session.delete(run)
        meeting = session.get(Meeting, meeting_id)
        if meeting:
            session.delete(meeting)
        session.commit()


def test_sanitizer_bounds_urls_credentials_and_metadata() -> None:
    unsafe = "Bearer abc123 https://example.com/file?token=secret password=hunter2 " + ("x" * 1200)
    safe = sanitize_message(unsafe)

    assert safe is not None
    assert len(safe) <= 1000
    assert "https://example.com" not in safe
    assert "hunter2" not in safe
    assert "Bearer abc123" not in safe
    assert "[redacted-url]" in safe
    assert "[redacted-credential]" in safe

    metadata = sanitize_metadata({
        "duration": 12.5,
        "download_url": "https://minio.local/private.wav",
        "api_key": "sk-secret",
        "nested": {"raw": "not allowed"},
    })
    assert metadata == {
        "duration": 12.5,
        "download_url": "[redacted]",
        "api_key": "[redacted]",
    }


def test_records_run_stage_lifecycle_with_safe_values() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert run is not None
        meeting_processing_audit.set_root_task(run.id, "root-task-123")
        event = meeting_processing_audit.stage_started(
            run_id=run.id,
            meeting_id=meeting_id,
            stage="stage_download",
            task_id="task-1",
            message="downloading from https://example.com/audio.mp3",
            metadata={"provider": "minio", "token": "secret"},
        )
        assert event is not None
        meeting_processing_audit.stage_completed(
            run_id=run.id,
            meeting_id=meeting_id,
            stage="stage_download",
            task_id="task-1",
            message="done",
        )
        meeting_processing_audit.run_completed(run.id, message="complete")

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert len(audit.runs) == 1
        saved_run = audit.runs[0]
        assert saved_run.trigger == "reprocess"
        assert saved_run.status == "completed"
        assert saved_run.root_task_id == "root-task-123"
        assert [event.status for event in saved_run.events] == ["started", "completed", "completed"]
        assert "https://example.com" not in (saved_run.events[0].message or "")
        assert saved_run.events[0].metadata == {"provider": "minio", "token": "[redacted]"}
    finally:
        _delete_meeting(meeting_id)


def test_pipeline_signatures_attach_headers_without_embedding_link_error() -> None:
    signatures = _build_pipeline_signatures(123, [stage_download, stage_embedding], 456)

    assert signatures[0].options["headers"]["meeting_processing_run_id"] == "456"
    assert signatures[0].options["headers"]["meeting_id"] == "123"
    assert signatures[0].options["headers"]["stage"] == "stage_download"
    assert signatures[0].options["link_error"]
    assert signatures[1].options["headers"]["stage"] == "stage_embedding"
    assert signatures[1].options["link_error"] == []


def test_success_signal_uses_sender_request_and_completes_embedding_run() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None
        sender = _FakeTask(
            name="stage_embedding",
            request=_FakeRequest(task_id="embedding-task", args=(meeting_id,), run_id=run.id),
        )

        _record_audit_stage_succeeded(sender=sender, result=meeting_id)

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        saved_run = audit.runs[0]
        assert saved_run.status == "completed"
        assert saved_run.events[0].stage == "stage_embedding"
        assert saved_run.events[0].status == "completed"
        assert saved_run.events[0].task_id == "embedding-task"
        assert saved_run.events[-1].stage == "run"
        assert saved_run.events[-1].status == "completed"
    finally:
        _delete_meeting(meeting_id)


def test_failure_signal_is_authoritative_for_embedding_run() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None
        sender = _FakeTask(
            name="stage_embedding",
            request=_FakeRequest(task_id="embedding-task", args=(meeting_id,), run_id=run.id),
        )

        _record_audit_stage_failed(
            task_id="embedding-task",
            exception=RuntimeError("embedding failed"),
            args=(meeting_id,),
            sender=sender,
        )

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        saved_run = audit.runs[0]
        assert saved_run.status == "failed"
        assert saved_run.final_error == "embedding failed"
        assert [event.status for event in saved_run.events] == ["failed", "failed"]
        assert all(event.status != "completed" for event in saved_run.events)
    finally:
        _delete_meeting(meeting_id)


def test_on_stage_failure_only_ensures_run_failed_without_duplicate_stage_or_run_events() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None
        request = _FakeRequest(task_id="embedding-task", args=(meeting_id,), run_id=run.id)
        sender = _FakeTask(name="stage_embedding", request=request)

        _record_audit_stage_failed(
            task_id="embedding-task",
            exception=RuntimeError("embedding failed"),
            args=(meeting_id,),
            sender=sender,
        )
        with patch("app.worker.meeting_service.mark_failed") as mark_failed:
            on_stage_failure(request, RuntimeError("embedding failed"), None)
        mark_failed.assert_called_once_with(meeting_id, "embedding failed")

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        saved_run = audit.runs[0]
        stage_failures = [event for event in saved_run.events if event.stage == "stage_embedding" and event.status == "failed"]
        run_failures = [event for event in saved_run.events if event.stage == "run" and event.status == "failed"]
        assert len(stage_failures) == 1
        assert len(run_failures) == 1
        assert saved_run.status == "failed"
    finally:
        _delete_meeting(meeting_id)
