# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Tests for stage_visual_breakdown Celery task orchestration."""
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.db.engine import engine
from app.models import Meeting, User, VisualSegment
from app.services.visual_breakdown.types import VisualSegmentResponse, VisionWorkerResult
from app.worker import (
    _acquire_visual_lease,
    stage_extract_intelligence,
    stage_optional_visual_breakdown,
    stage_summarize,
    stage_visual_breakdown,
)


@pytest.fixture
def meeting_with_file(db: Session):
    """Create a throw-away meeting (user already created by create_test_user fixture)
    with a valid file_path, and yield meeting_id. Cleans up after test."""
    m = Meeting(owner_id=1, title="VB test mtg", file_path="users/1/meetings/X/audio.mp4")
    db.add(m)
    db.commit()
    db.refresh(m)
    mid = m.id
    yield mid
    with Session(engine) as session:
        obj = session.get(Meeting, mid)
        if obj is not None:
            session.delete(obj)
            session.commit()


def _completed_worker_result() -> VisionWorkerResult:
    return VisionWorkerResult(
        status="completed",
        segments=[
            VisualSegmentResponse(
                id="w-1", sequence=0, start_time=0.0, end_time=5.0,
                screenshot_s3_key="users/1/meetings/X/visual/w-1.jpg",
                caption="Login page", confidence=0.9,
            ),
            VisualSegmentResponse(
                id="w-2", sequence=1, start_time=5.0, end_time=10.0,
                screenshot_s3_key="users/1/meetings/X/visual/w-2.jpg",
                caption="Dashboard", confidence=0.85,
            ),
        ],
        raw_output_s3_key="users/1/meetings/X/visual/raw_output.json",
        model="gpt-4o-mini",
        params={"fps": 2},
        stage_metrics={
            "extract_frames": {"duration_ms": 1000, "frame_count": 40},
            "compute_signals": {"duration_ms": 500, "candidate_count": 6},
        },
    )


def test_happy_path_completes_meeting_and_persists_segments(meeting_with_file):
    meeting_id = meeting_with_file
    worker_result = _completed_worker_result()

    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture") as mock_capture,
        patch("app.worker.notify") as mock_notify,
    ):
        mock_client = MagicMock()
        mock_client.submit_and_wait.return_value = worker_result
        mock_cls.return_value = mock_client

        out = stage_visual_breakdown(meeting_id)

    assert out == meeting_id
    mock_cls.assert_called_once_with()
    request = mock_cls.return_value.submit_and_wait.call_args.args[0]
    assert request["file_path"] == "users/1/meetings/X/audio.mp4"
    assert "video_url" not in request

    # Meeting fields updated
    with Session(engine) as session:
        m = session.get(Meeting, meeting_id)
        assert m.visual_breakdown_status == "completed"
        assert m.visual_breakdown_completed_at is not None
        assert m.visual_raw_output_s3_key == "users/1/meetings/X/visual/raw_output.json"
        assert m.visual_breakdown_model == "gpt-4o-mini"
        assert m.visual_breakdown_run_count == 1

    # Segments persisted
    from sqlmodel import select as sql_select
    with Session(engine) as session:
        segs = list(session.exec(
            sql_select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
        ))
        assert len(segs) == 2
        assert {s.caption for s in segs} == {"Login page", "Dashboard"}

    # Analytics: 1 completion event + 2 per-stage events
    event_names = [c.args[1] for c in mock_capture.call_args_list]
    assert "visual_breakdown_completed" in event_names
    assert event_names.count("visual_breakdown_stage_completed") == 2

    # Notify called once with the right event type
    mock_notify.assert_called_once()
    assert mock_notify.call_args.args[0] == "visual_breakdown_completed"


def test_legacy_presigned_visual_helpers_are_removed():
    assert not hasattr(__import__("app.worker", fromlist=["_fresh_visual_url"]), "_fresh_visual_url")


def test_worker_does_not_construct_the_legacy_external_vision_client(meeting_with_file):
    with (
        patch("app.worker.DirectVisionService") as direct_service,
        patch("app.services.visual_breakdown.vision_client.VisionClient") as legacy_client,
        patch("app.services.analytics.capture"),
        patch("app.services.notifications.notify"),
    ):
        direct_service.return_value.submit_and_wait.return_value = VisionWorkerResult(
            status="completed",
            segments=[],
            model="gpt-4o-mini",
            params={"skip_reason": "no_relevant_visual"},
            stage_metrics={},
        )
        assert stage_visual_breakdown(meeting_with_file) == meeting_with_file

    direct_service.assert_called_once_with()
    legacy_client.assert_not_called()


def test_worker_failure_falls_back_without_failing_meeting(meeting_with_file):
    meeting_id = meeting_with_file
    failed_result = VisionWorkerResult(
        status="failed", segments=[], model="gpt-4o-mini", params={},
        stage_metrics={}, error="ffmpeg blew up", failed_stage="extract_frames",
    )

    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture") as mock_capture,
        patch("app.services.notifications.notify"),
    ):
        mock_cls.return_value.submit_and_wait.return_value = failed_result

        out = stage_visual_breakdown(meeting_id)

    assert out == meeting_id

    with Session(engine) as session:
        m = session.get(Meeting, meeting_id)
        assert m.visual_breakdown_status == "fallback"
        assert m.visual_breakdown_error == "vision_worker_failed"

    event_names = [c.args[1] for c in mock_capture.call_args_list]
    assert "visual_breakdown_warning" in event_names
    assert "visual_breakdown_completed" not in event_names


def test_client_exception_falls_back_without_reraising(meeting_with_file):
    meeting_id = meeting_with_file

    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.services.notifications.notify"),
    ):
        mock_cls.return_value.submit_and_wait.side_effect = RuntimeError("connection refused")

        out = stage_visual_breakdown(meeting_id)

    assert out == meeting_id

    with Session(engine) as session:
        m = session.get(Meeting, meeting_id)
        assert m.visual_breakdown_status == "fallback"
        assert m.visual_breakdown_error == "vision_worker_error"


def test_missing_file_path_skips_without_calling_worker(meeting_with_file, db: Session):
    meeting_id = meeting_with_file
    # Clear file_path before test
    m = db.get(Meeting, meeting_id)
    m.file_path = None
    db.add(m)
    db.commit()

    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.services.notifications.notify"),
    ):
        out = stage_visual_breakdown(meeting_id)

    assert out == meeting_id
    # Worker was never called
    mock_cls.assert_not_called()

    with Session(engine) as session:
        m = session.get(Meeting, meeting_id)
        assert m.visual_breakdown_status == "skipped"
        assert m.visual_breakdown_error == "no_video_file"


def test_optional_stage_disabled_skips_with_stable_id(meeting_with_file):
    with (
        patch("app.worker.settings.VISION_ENABLED", False),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        out = stage_optional_visual_breakdown(meeting_with_file)

    assert out == meeting_with_file
    mock_cls.assert_not_called()
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        assert meeting.visual_breakdown_status == "skipped"
        assert meeting.visual_breakdown_error == "visual_disabled"


def test_visual_lease_rejects_an_epoch_owned_by_another_worker():
    client = MagicMock()
    client.set.return_value = None
    with patch("redis.from_url", return_value=client):
        assert _acquire_visual_lease(42, "epoch") is False
    client.set.assert_called_once()


def test_duplicate_delivery_converges_to_one_run_and_stable_meeting_id(meeting_with_file):
    """Duplicate/retry delivery converges on one logical visual run."""
    worker_result = _completed_worker_result()
    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture") as mock_capture,
        patch("app.worker.notify") as mock_notify,
    ):
        mock_cls.return_value.submit_and_wait.return_value = worker_result
        first = stage_visual_breakdown(meeting_with_file)
        second = stage_visual_breakdown(meeting_with_file)

    assert first == second == meeting_with_file
    assert mock_cls.return_value.submit_and_wait.call_count == 1
    event_names = [call.args[1] for call in mock_capture.call_args_list]
    assert event_names.count("visual_breakdown_completed") == 1
    assert event_names.count("visual_breakdown_warning") == 0
    assert event_names.count("visual_breakdown_stage_completed") == 2
    mock_notify.assert_called_once()


def test_missing_meeting_is_fatal():
    """A missing meeting must not be converted into a successful skip."""
    with pytest.raises(RuntimeError, match="Meeting"):
        stage_visual_breakdown(-999999)


def test_summary_preserves_legacy_transcript_when_segments_are_absent(meeting_with_file, db: Session):
    meeting = db.get(Meeting, meeting_with_file)
    meeting.transcript_text = "Legacy transcript text"
    db.add(meeting)
    db.commit()

    with (
        patch("app.worker.style_service.get_style_examples", return_value=[]),
        patch("app.worker.template_service.get_active_default", return_value=None),
        patch("app.worker.summarize_transcript", return_value="Generated summary") as summarize,
        patch("app.services.ai_agent.infer_title", return_value=None),
        patch("app.worker.analytics.capture"),
        patch("app.worker.notify"),
        patch("app.services.email.email_service.send_summary_email"),
    ):
        out = stage_summarize(meeting_with_file)

    assert out == meeting_with_file
    assert summarize.call_args.kwargs["context"] is None


def test_summary_waits_for_transcript_intelligence_before_completion(meeting_with_file, db: Session):
    meeting = db.get(Meeting, meeting_with_file)
    meeting.transcript_text = "Transcript for intelligence extraction"
    db.add(meeting)
    db.commit()

    with (
        patch("app.worker.style_service.get_style_examples", return_value=[]),
        patch("app.worker.template_service.get_active_default", return_value=None),
        patch("app.worker.summarize_transcript", return_value="Generated summary"),
        patch("app.services.ai_agent.infer_title", return_value=None),
        patch("app.worker.analytics.capture"),
        patch("app.worker.notify"),
        patch("app.services.email.email_service.send_summary_email"),
    ):
        stage_summarize(meeting_with_file)

    with Session(engine) as session:
        summarized = session.get(Meeting, meeting_with_file)
        assert summarized.status == "processing"
        assert summarized.sub_status == "summarizing"

    intelligence = MagicMock()
    intelligence.extract_highlights.return_value = []
    intelligence.extract_structured_output.return_value = {"topics": []}
    with patch("app.services.meeting_intelligence.intelligence_service", intelligence):
        stage_extract_intelligence(meeting_with_file)

    with Session(engine) as session:
        completed = session.get(Meeting, meeting_with_file)
        assert completed.status == "completed"
        assert completed.sub_status is None
        assert completed.structured_output_status == "completed"
