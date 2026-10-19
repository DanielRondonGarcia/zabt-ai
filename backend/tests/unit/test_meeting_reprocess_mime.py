# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public API coverage for source MIME across failed-meeting reprocessing."""

from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.db.engine import engine
from app.models import MeetingProcessingEvent, MeetingProcessingRun
from app.models.base import Meeting


@pytest.mark.parametrize(
    ("params", "persisted", "media_type"),
    [
        ({"content_type": "video/mp4", "run_epoch": "stale"}, {"content_type": "video/mp4"}, "video"),
        ({"run_epoch": "stale"}, None, None),
    ],
)
def test_reprocess_preserves_source_mime_but_clears_stale_visual_state(
    client, params, persisted, media_type
):
    # tests/conftest.py provisions user 1. The endpoint requires a failed owner meeting.
    with Session(engine) as session:
        meeting = Meeting(
            title="failed video",
            owner_id=1,
            status="failed",
            file_path="users/1/meetings/video.mp4",
            visual_breakdown_status="failed",
            visual_breakdown_params=params,
        )
        session.add(meeting)
        session.commit()
        session.refresh(meeting)
        meeting_id = meeting.id

    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as dispatch:
            response = client.post(f"/api/v1/meetings/{meeting_id}/reprocess")
        assert response.status_code == 200, response.text
        assert response.json()["media_type"] == media_type
        dispatch.assert_called_once_with(meeting_id)

        with Session(engine) as session:
            reloaded = session.get(Meeting, meeting_id)
            assert reloaded is not None
            assert reloaded.visual_breakdown_params == persisted
            assert reloaded.visual_breakdown_status is None
        detail = client.get(f"/api/v1/meetings/{meeting_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["media_type"] == media_type
    finally:
        with Session(engine) as session:
            for event in session.exec(
                select(MeetingProcessingEvent).where(MeetingProcessingEvent.meeting_id == meeting_id)
            ).all():
                session.delete(event)
            for run in session.exec(
                select(MeetingProcessingRun).where(MeetingProcessingRun.meeting_id == meeting_id)
            ).all():
                session.delete(run)
            record = session.get(Meeting, meeting_id)
            if record is not None:
                session.delete(record)
            session.commit()
