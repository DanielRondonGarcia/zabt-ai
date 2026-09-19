# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Integration tests for owner-scoped meeting processing audit endpoint."""

from datetime import datetime, timedelta
from unittest.mock import patch

from sqlmodel import SQLModel, Session, select

from app.db.engine import engine
from app.models import Meeting, MeetingProcessingEvent, MeetingProcessingRun, TranscriptSegment, User, VisualSegment
from app.services.meeting_processing_audit import meeting_processing_audit


def setup_module() -> None:
    SQLModel.metadata.create_all(engine)


def _ensure_user(session: Session, user_id: int) -> None:
    if not session.get(User, user_id):
        session.add(User(id=user_id, email=f"audit{user_id}@test.com", supabase_id=f"audit_{user_id}"))
        session.commit()


def _make_meeting(owner_id: int = 1, status: str = "completed") -> int:
    with Session(engine) as session:
        _ensure_user(session, owner_id)
        meeting = Meeting(
            title="audit endpoint",
            owner_id=owner_id,
            status=status,
            file_path="users/1/meetings/audio.mp3",
        )
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
        for seg in session.exec(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)).all():
            session.delete(seg)
        for seg in session.exec(select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)).all():
            session.delete(seg)
        meeting = session.get(Meeting, meeting_id)
        if meeting:
            session.delete(meeting)
        session.commit()


def test_reprocess_creates_reprocess_run_and_audit_get_is_ordered(client) -> None:
    meeting_id = _make_meeting(status="failed")
    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            response = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")
        assert response.status_code == 200, response.text
        mock_dispatch.assert_called_once_with(meeting_id)

        first_run = meeting_processing_audit.get_pending_run(meeting_id)
        assert first_run is not None
        assert first_run.trigger == "reprocess"

        meeting_processing_audit.stage_started(
            run_id=first_run.id,
            meeting_id=meeting_id,
            stage="stage_download",
            task_id="download-task",
            message="download started",
        )
        meeting_processing_audit.stage_completed(
            run_id=first_run.id,
            meeting_id=meeting_id,
            stage="stage_download",
            task_id="download-task",
            message="download complete",
        )
        first_created = first_run.created_at
        with Session(engine) as session:
            run = session.get(MeetingProcessingRun, first_run.id)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            session.add(run)
            session.commit()

        second_run = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert second_run is not None
        with Session(engine) as session:
            older = session.get(MeetingProcessingRun, first_run.id)
            older.created_at = first_created - timedelta(minutes=5)
            session.add(older)
            session.commit()

        audit_response = client.get(f"/api/v1/meetings/{meeting_id}/processing-audit")
        assert audit_response.status_code == 200, audit_response.text
        body = audit_response.json()
        assert body["meeting_id"] == meeting_id
        assert [run["id"] for run in body["runs"]][:2] == [second_run.id, first_run.id]
        first_body = next(run for run in body["runs"] if run["id"] == first_run.id)
        assert [event["event_type"] for event in first_body["events"]] == ["started", "completed"]
    finally:
        _delete_meeting(meeting_id)


def test_audit_get_404_for_other_owner_and_missing_meeting(client) -> None:
    other_meeting_id = _make_meeting(owner_id=42)
    try:
        other_response = client.get(f"/api/v1/meetings/{other_meeting_id}/processing-audit")
        assert other_response.status_code == 404

        missing_response = client.get("/api/v1/meetings/999999999/processing-audit")
        assert missing_response.status_code == 404
    finally:
        _delete_meeting(other_meeting_id)


def test_audit_get_returns_sanitized_errors_without_raw_url_or_credentials(client) -> None:
    meeting_id = _make_meeting(status="failed")
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert run is not None
        meeting_processing_audit.stage_failed(
            run_id=run.id,
            meeting_id=meeting_id,
            stage="stage_transcribe",
            task_id="task-id",
            error="provider failed at https://example.com/audio.wav api_key=sk-secret password=hunter2",
            metadata={"presigned_url": "https://minio.local/private", "safe_count": 1},
        )
        meeting_processing_audit.run_failed(run.id, error="Bearer token123 failed for https://example.com")

        response = client.get(f"/api/v1/meetings/{meeting_id}/processing-audit")
        assert response.status_code == 200, response.text
        text = response.text
        assert "https://example.com" not in text
        assert "https://minio.local" not in text
        assert "sk-secret" not in text
        assert "hunter2" not in text
        assert "token123" not in text
        assert "[redacted-url]" in text
        assert "[redacted-credential]" in text
        assert "[redacted]" in text
    finally:
        _delete_meeting(meeting_id)
