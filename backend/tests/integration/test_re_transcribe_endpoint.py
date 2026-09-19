# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Tests for POST /api/v1/meetings/{id}/re-transcribe."""

from unittest.mock import patch
from sqlmodel import Session, select
from app.db.engine import engine
from app.models import MeetingProcessingEvent, MeetingProcessingRun
from app.models.base import Meeting, TranscriptSegment, User, VisualSegment


def _ensure_user(session: Session, user_id: int) -> None:
    """Create user if not already present (FK requirement)."""
    if not session.get(User, user_id):
        session.add(User(id=user_id, email=f"user{user_id}@test.com", supabase_id=f"supabase_{user_id}"))
        session.commit()


def _make_meeting(owner_id: int = 1, requested_language: str | None = "hindi") -> int:
    with Session(engine) as s:
        _ensure_user(s, owner_id)
        m = Meeting(
            title="t", owner_id=owner_id,
            requested_language=requested_language, status="completed",
            file_path="some/key",
        )
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def _delete_meeting(meeting_id: int) -> None:
    with Session(engine) as s:
        for event in s.exec(select(MeetingProcessingEvent).where(MeetingProcessingEvent.meeting_id == meeting_id)).all():
            s.delete(event)
        for run in s.exec(select(MeetingProcessingRun).where(MeetingProcessingRun.meeting_id == meeting_id)).all():
            s.delete(run)
        for seg in s.exec(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)).all():
            s.delete(seg)
        for seg in s.exec(select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)).all():
            s.delete(seg)
        m = s.get(Meeting, meeting_id)
        if m:
            s.delete(m)
        s.commit()


def test_re_transcribe_updates_language_and_dispatches_job(client):
    meeting_id = _make_meeting(requested_language="hindi")
    try:
        # Patch the dispatch helper at its actual import path.
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            resp = client.post(
                f"/api/v1/meetings/{meeting_id}/re-transcribe",
                json={"language": "urdu_arabic"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requested_language"] == "urdu_arabic"
        mock_dispatch.assert_called_once_with(meeting_id)

        # Confirm DB persisted
        with Session(engine) as s:
            assert s.get(Meeting, meeting_id).requested_language == "urdu_arabic"
    finally:
        _delete_meeting(meeting_id)


def test_re_transcribe_rejects_unknown_language(client):
    meeting_id = _make_meeting()
    try:
        resp = client.post(
            f"/api/v1/meetings/{meeting_id}/re-transcribe",
            json={"language": "klingon"},
        )
        assert resp.status_code == 400
    finally:
        _delete_meeting(meeting_id)


def test_re_transcribe_404_for_nonexistent_meeting(client):
    resp = client.post(
        "/api/v1/meetings/999999/re-transcribe",
        json={"language": "english"},
    )
    assert resp.status_code == 404


def test_re_transcribe_404_for_other_users_meeting(client):
    meeting_id = _make_meeting(owner_id=42)  # current_user is id=1
    try:
        resp = client.post(
            f"/api/v1/meetings/{meeting_id}/re-transcribe",
            json={"language": "english"},
        )
        assert resp.status_code in (403, 404)
    finally:
        _delete_meeting(meeting_id)


def test_re_transcribe_clears_transliterated_text_and_segments(client):
    with Session(engine) as s:
        _ensure_user(s, 1)
        m = Meeting(
            title="t", owner_id=1,
            requested_language="urdu_arabic", status="completed",
            file_path="k", transliterated_text="yeh urdu hai",
        )
        s.add(m)
        s.commit()
        s.refresh(m)
        meeting_id = m.id
        s.add(TranscriptSegment(
            meeting_id=meeting_id, start_time=0, end_time=1, text="a",
        ))
        s.commit()

    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job"):
            resp = client.post(
                f"/api/v1/meetings/{meeting_id}/re-transcribe",
                json={"language": "english"},
            )
        assert resp.status_code == 200
        with Session(engine) as s:
            reloaded = s.get(Meeting, meeting_id)
            assert reloaded.transliterated_text is None
            remaining = s.exec(
                select(TranscriptSegment).where(
                    TranscriptSegment.meeting_id == meeting_id,
                )
            ).all()
            assert remaining == []
    finally:
        _delete_meeting(meeting_id)


def test_reprocess_failed_meeting_queues_dispatches_and_clears_stale_pipeline_data(client):
    with Session(engine) as s:
        _ensure_user(s, 1)
        m = Meeting(
            title="failed meeting",
            owner_id=1,
            requested_language="hindi",
            status="failed",
            sub_status="504 Gateway Timeout",
            file_path="users/1/meetings/514/audio.mp3",
            transcript_text="stale transcript",
            summary_text="stale summary",
            original_summary_text="original summary",
            summary_edited=True,
            action_items_text="stale actions",
            structured_output={"old": True},
            structured_output_status="failed",
            transliterated_text="stale roman",
            visual_breakdown_status="failed",
            visual_breakdown_error="vision failed",
            visual_raw_output_s3_key="old/raw.json",
            visual_breakdown_model="old-model",
            visual_breakdown_params={"run_id": "old"},
        )
        s.add(m)
        s.commit()
        s.refresh(m)
        meeting_id = m.id
        s.add(TranscriptSegment(meeting_id=meeting_id, start_time=0, end_time=1, text="old"))
        s.add(VisualSegment(
            meeting_id=meeting_id,
            start_time=0,
            end_time=1,
            screenshot_s3_key="old/frame.jpg",
            caption="old frame",
            sequence=1,
            confidence=0.9,
        ))
        s.commit()

    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            resp = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["sub_status"] is None
        assert body["file_path"] == "users/1/meetings/514/audio.mp3"
        assert body["title"] == "failed meeting"
        assert body["requested_language"] == "hindi"
        assert body["transcript_text"] is None
        assert body["summary_text"] is None
        assert body["original_summary_text"] is None
        assert body["summary_edited"] is False
        assert body["action_items_text"] is None
        assert body["structured_output"] is None
        assert body["structured_output_status"] == "pending"
        assert body["transliterated_text"] is None
        assert body["visual_breakdown_status"] is None
        assert body["visual_breakdown_error"] is None
        assert body["visual_breakdown_completed_at"] is None
        assert body["segments"] == []
        mock_dispatch.assert_called_once_with(meeting_id)

        with Session(engine) as s:
            reloaded = s.get(Meeting, meeting_id)
            assert reloaded.status == "queued"
            assert reloaded.processing_heartbeat_at is not None
            assert reloaded.visual_raw_output_s3_key is None
            assert reloaded.visual_breakdown_model is None
            assert reloaded.visual_breakdown_params is None
            assert s.exec(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)).all() == []
            assert s.exec(select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)).all() == []
    finally:
        _delete_meeting(meeting_id)


def test_reprocess_rejects_active_and_completed_meetings(client):
    meeting_ids: list[int] = []
    try:
        for meeting_status in ("queued", "processing", "completed"):
            with Session(engine) as s:
                _ensure_user(s, 1)
                m = Meeting(title=f"{meeting_status} meeting", owner_id=1, status=meeting_status, file_path="some/key")
                s.add(m)
                s.commit()
                s.refresh(m)
                meeting_ids.append(m.id)

            with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
                resp = client.post(f"/api/v1/meetings/{meeting_ids[-1]}/reprocess")
            assert resp.status_code == 409
            mock_dispatch.assert_not_called()
    finally:
        for meeting_id in meeting_ids:
            _delete_meeting(meeting_id)


def test_reprocess_rejects_failed_meeting_without_file_path(client):
    with Session(engine) as s:
        _ensure_user(s, 1)
        m = Meeting(title="failed missing file", owner_id=1, status="failed", file_path=None)
        s.add(m)
        s.commit()
        s.refresh(m)
        meeting_id = m.id

    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            resp = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")
        assert resp.status_code == 400
        assert "stored media file" in resp.json()["detail"]
        mock_dispatch.assert_not_called()
    finally:
        _delete_meeting(meeting_id)


def test_reprocess_404_for_other_users_meeting(client):
    meeting_id = _make_meeting(owner_id=42)
    with Session(engine) as s:
        m = s.get(Meeting, meeting_id)
        m.status = "failed"
        s.add(m)
        s.commit()

    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            resp = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")
        assert resp.status_code == 404
        mock_dispatch.assert_not_called()
    finally:
        _delete_meeting(meeting_id)


def test_meeting_read_exposes_transliterated_text(client):
    with Session(engine) as s:
        _ensure_user(s, 1)
        m = Meeting(
            title="t", owner_id=1,
            transliterated_text="sample roman", file_path="k",
        )
        s.add(m)
        s.commit()
        s.refresh(m)
        meeting_id = m.id

    try:
        resp = client.get(f"/api/v1/meetings/{meeting_id}")
        assert resp.status_code == 200
        assert resp.json().get("transliterated_text") == "sample roman"
    finally:
        _delete_meeting(meeting_id)
