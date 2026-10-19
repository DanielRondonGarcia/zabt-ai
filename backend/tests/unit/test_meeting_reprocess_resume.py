# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public API coverage for resuming a failed meeting instead of restarting it."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.engine import engine
from app.main import app
from app.models import MeetingProcessingEvent, MeetingProcessingRun
from app.models.base import Meeting, TranscriptSegment


def _failed_meeting(**overrides) -> int:
    values = {
        "title": "failed resume",
        "owner_id": 1,
        "status": "failed",
        "file_path": "users/1/meetings/video.mp4",
        "transcript_text": "durable transcript",
        "summary_text": "[Error: provider failed]",
        "visual_breakdown_status": "completed",
        "visual_breakdown_params": {"content_type": "video/mp4"},
    }
    values.update(overrides)
    with Session(engine) as session:
        meeting = Meeting(**values)
        session.add(meeting)
        session.commit()
        session.refresh(meeting)
        meeting_id = meeting.id
        session.add(
            TranscriptSegment(
                meeting_id=meeting_id, start_time=0, end_time=1, text="keep segment"
            )
        )
        session.commit()
        return meeting_id


def _cleanup(meeting_id: int) -> None:
    with Session(engine) as session:
        for event in session.exec(
            select(MeetingProcessingEvent).where(
                MeetingProcessingEvent.meeting_id == meeting_id
            )
        ).all():
            session.delete(event)
        for run in session.exec(
            select(MeetingProcessingRun).where(
                MeetingProcessingRun.meeting_id == meeting_id
            )
        ).all():
            session.delete(run)
        for segment in session.exec(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
        ).all():
            session.delete(segment)
        meeting = session.get(Meeting, meeting_id)
        if meeting is not None:
            session.delete(meeting)
        session.commit()


def test_reprocess_resumes_from_durable_outputs_by_default():
    meeting_id = _failed_meeting()
    client = TestClient(app)
    try:
        with patch("app.worker.dispatch_reprocess_resume") as resume, patch(
            "app.api.v1.endpoints.meetings.dispatch_transcription_job"
        ) as full:
            response = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")

        assert response.status_code == 200, response.text
        resume.assert_called_once_with(meeting_id)
        full.assert_not_called()
        assert response.json()["status"] == "queued"

        with Session(engine) as session:
            reloaded = session.get(Meeting, meeting_id)
            assert reloaded.transcript_text == "durable transcript"
            assert reloaded.summary_text == "[Error: provider failed]"
            assert session.exec(
                select(TranscriptSegment).where(
                    TranscriptSegment.meeting_id == meeting_id
                )
            ).all()
    finally:
        client.close()
        _cleanup(meeting_id)


def test_reprocess_restart_clears_outputs_and_keeps_the_original_flow():
    meeting_id = _failed_meeting()
    client = TestClient(app)
    try:
        with patch("app.worker.dispatch_reprocess_resume") as resume, patch(
            "app.api.v1.endpoints.meetings.dispatch_transcription_job"
        ) as full:
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/reprocess",
                json={"mode": "restart"},
            )

        assert response.status_code == 200, response.text
        full.assert_called_once_with(meeting_id)
        resume.assert_not_called()

        with Session(engine) as session:
            reloaded = session.get(Meeting, meeting_id)
            assert reloaded.transcript_text is None
            assert reloaded.summary_text is None
            assert session.exec(
                select(TranscriptSegment).where(
                    TranscriptSegment.meeting_id == meeting_id
                )
            ).all() == []
    finally:
        client.close()
        _cleanup(meeting_id)
