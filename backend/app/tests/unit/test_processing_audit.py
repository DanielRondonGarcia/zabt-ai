# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused tests for meeting processing audit persistence and sanitization."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

from celery.exceptions import Ignore
import pytest
from sqlmodel import SQLModel, Session, delete, select

from app.db.engine import engine
from app.models import (
    Meeting,
    MeetingProcessingEvent,
    MeetingProcessingRun,
    TranscriptSegment,
    User,
    VisualSegment,
)
from app.models.meeting_intelligence import MeetingHighlight
from app.services.meeting_processing_audit import meeting_processing_audit, sanitize_message, sanitize_metadata
from app.worker import (
    _build_pipeline_signatures,
    _dispatch_recovery_plan,
    _dispatch_recovery_stages,
    _record_audit_stage_failed,
    _record_audit_stage_succeeded,
    dispatch_pipeline,
    dispatch_youtube_pipeline,
    finalize_recovered_meeting,
    on_stage_failure,
    send_notification,
    _mark_transcription_skipped_for_no_audio,
    _save_transcription_for_attempt,
    _temp_path_for,
    stage_extract_intelligence,
    stage_download,
    stage_embedding,
    stage_summarize,
    stage_transcribe,
    stage_transliterate,
)
from app.services.meeting import meeting_service
from app.services.notifications import notify


class _FakeRequest:
    def __init__(
        self,
        *,
        task_id: str,
        args: tuple[int],
        run_id: int,
        attempt_id: str | None = None,
    ):
        self.id = task_id
        self.task = "stage_embedding"
        self.args = args
        self.headers = {"meeting_processing_run_id": str(run_id)}
        if attempt_id is not None:
            self.headers["dispatch_attempt_id"] = attempt_id


class _FakeTask:
    def __init__(self, *, name: str, request: _FakeRequest):
        self.name = name
        self.request = request


def _assert_failure_callback_has_no_side_effects(request, error) -> None:
    with (
        patch("app.worker.analytics.capture") as capture,
        patch("app.services.email.email_service.send_failure_email") as email,
        patch("app.worker.os.path.exists", return_value=True),
        patch("app.worker.os.remove") as remove,
    ):
        on_stage_failure(request, error, None)
    capture.assert_not_called()
    email.assert_not_called()
    remove.assert_not_called()


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


def _seed_transcription_outputs(meeting_id: int) -> None:
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.transcript_text = "keep transcript"
        meeting.summary_text = "keep summary"
        meeting.structured_output = {"keep": True}
        meeting.structured_output_status = "completed"
        meeting.duration_seconds = 77
        session.add(meeting)
        session.add(TranscriptSegment(
            meeting_id=meeting_id, start_time=0, end_time=2, text="keep segment"
        ))
        session.add(MeetingHighlight(
            meeting_id=meeting_id,
            highlight_type="topic",
            content="keep highlight",
            timestamp_start=0,
        ))
        session.commit()


def _seed_completed_visual_meeting(meeting_id: int) -> None:
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.status = "completed"
        meeting.summary_text = "keep summary"
        meeting.transcript_text = "keep transcript"
        meeting.file_path = "users/1/meetings/video.mp4"
        meeting.duration_seconds = 120
        meeting.visual_breakdown_status = "completed"
        meeting.visual_breakdown_params = {
            "run_epoch": "previous-epoch",
            "attempts": 3,
            "outcome": "completed",
            "content_type": "video/mp4",
        }
        meeting.visual_raw_output_s3_key = "users/1/meetings/visual/previous.json"
        session.add(meeting)
        session.add(
            VisualSegment(
                meeting_id=meeting_id,
                start_time=1.0,
                end_time=2.0,
                screenshot_s3_key="users/1/meetings/visual/previous.png",
                caption="Previous visual segment",
                sequence=0,
                confidence=0.9,
            )
        )
        session.commit()


def _seed_highlight_input(meeting_id: int) -> None:
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.transcript_text = "highlight source"
        session.add(meeting)
        session.add(MeetingHighlight(
            meeting_id=meeting_id,
            highlight_type="topic",
            content="previous highlight",
            timestamp_start=1,
        ))
        session.commit()


def _delete_highlight_test_meeting(meeting_id: int) -> None:
    with Session(engine) as session:
        session.exec(delete(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id))
        session.commit()
    _delete_meeting(meeting_id)


def _seed_transliteration_input(meeting_id: int) -> None:
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.requested_language = "target"
        meeting.transcript_text = "source text"
        meeting.transliterated_text = "previous output"
        meeting.status = "processing"
        meeting.sub_status = "before-transliteration"
        session.add(meeting)
        session.commit()


def _seed_summary_input(meeting_id: int) -> None:
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.title = "recording.mp3"
        meeting.transcript_text = "summary source text"
        meeting.summary_text = "previous summary"
        meeting.status = "processing"
        meeting.sub_status = "before-summary"
        session.add(meeting)
        session.commit()


def _delete_meeting(meeting_id: int) -> None:
    with Session(engine) as session:
        session.exec(delete(VisualSegment).where(VisualSegment.meeting_id == meeting_id))
        for event in session.exec(select(MeetingProcessingEvent).where(MeetingProcessingEvent.meeting_id == meeting_id)).all():
            session.delete(event)
        for run in session.exec(select(MeetingProcessingRun).where(MeetingProcessingRun.meeting_id == meeting_id)).all():
            session.delete(run)
        meeting = session.get(Meeting, meeting_id)
        if meeting:
            session.delete(meeting)
        session.commit()


def test_visual_breakdown_run_admission_preserves_completed_meeting_and_prior_outputs() -> None:
    meeting_id = _make_meeting()
    try:
        _seed_completed_visual_meeting(meeting_id)

        admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_id)

        assert admitted is not None
        run, run_epoch = admitted
        assert run.trigger == "visual_breakdown"
        assert run.status == "queued"
        assert run.dispatch_attempt_id
        assert len(run.dispatch_attempt_id) == 32
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            segments = session.exec(
                select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
            ).all()
            assert meeting.status == "completed"
            assert meeting.current_processing_run_id == run.id
            assert meeting.summary_text == "keep summary"
            assert meeting.transcript_text == "keep transcript"
            assert meeting.file_path == "users/1/meetings/video.mp4"
            assert meeting.duration_seconds == 120
            assert meeting.visual_breakdown_status == "queued"
            assert meeting.visual_breakdown_params["run_epoch"] == run_epoch
            assert meeting.visual_breakdown_params["attempts"] == 0
            assert meeting.visual_breakdown_params["outcome"] is None
            assert meeting.visual_breakdown_params["content_type"] == "video/mp4"
            assert meeting.visual_raw_output_s3_key == "users/1/meetings/visual/previous.json"
            assert [(segment.caption, segment.screenshot_s3_key) for segment in segments] == [
                ("Previous visual segment", "users/1/meetings/visual/previous.png")
            ]
    finally:
        _delete_meeting(meeting_id)


@pytest.mark.parametrize(
    "blocker", ["incomplete_meeting", "active_run", "active_visual"]
)
def test_visual_breakdown_run_admission_requires_completed_meeting_without_active_run(
    blocker: str,
) -> None:
    meeting_id = _make_meeting()
    try:
        _seed_completed_visual_meeting(meeting_id)
        if blocker == "active_run":
            existing_run = meeting_processing_audit.create_run(
                meeting_id, trigger="pipeline"
            )
            assert existing_run is not None
        elif blocker == "active_visual":
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                meeting.visual_breakdown_status = "processing"
                session.add(meeting)
                session.commit()
        else:
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                meeting.status = "processing"
                session.add(meeting)
                session.commit()

        assert meeting_processing_audit.create_visual_breakdown_run(meeting_id) is None
    finally:
        _delete_meeting(meeting_id)


def test_visual_run_cancellation_preserves_completed_meeting_and_prior_outputs() -> None:
    meeting_id = _make_meeting()
    try:
        _seed_completed_visual_meeting(meeting_id)
        admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_id)
        assert admitted is not None
        run, run_epoch = admitted

        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=run.id, owner_id=1
        )
        late_result = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="completed",
            segments=[],
            raw_output_s3_key="users/1/meetings/visual/late.json",
        )
        assert late_result["applied"] is False

        audit = meeting_processing_audit.list_for_meeting(
            meeting_id=meeting_id, owner_id=1
        )
        assert audit.runs[0].status == "cancelled"
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            segments = session.exec(
                select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
            ).all()
            assert meeting.status == "completed"
            assert meeting.summary_text == "keep summary"
            assert meeting.transcript_text == "keep transcript"
            assert meeting.file_path == "users/1/meetings/video.mp4"
            assert meeting.duration_seconds == 120
            assert meeting.visual_breakdown_status == "cancelled"
            assert meeting.visual_breakdown_params["content_type"] == "video/mp4"
            assert meeting.visual_raw_output_s3_key == "users/1/meetings/visual/previous.json"
            assert [(segment.caption, segment.screenshot_s3_key) for segment in segments] == [
                ("Previous visual segment", "users/1/meetings/visual/previous.png")
            ]
    finally:
        _delete_meeting(meeting_id)


def test_visual_run_failure_preserves_completed_meeting_and_prior_outputs() -> None:
    meeting_id = _make_meeting()
    try:
        _seed_completed_visual_meeting(meeting_id)
        admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_id)
        assert admitted is not None
        run, run_epoch = admitted

        failed_meeting = meeting_service.mark_failed(
            meeting_id,
            "visual provider failed",
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
        )
        assert failed_meeting is not None
        assert meeting_processing_audit.run_failed(
            run.id,
            attempt_id=run.dispatch_attempt_id,
            error="visual provider failed",
        )
        late_result = meeting_service.finalize_visual_breakdown(
            meeting_id,
            run_epoch,
            outcome="completed",
            segments=[],
            raw_output_s3_key="users/1/meetings/visual/late.json",
        )
        assert late_result["applied"] is False

        audit = meeting_processing_audit.list_for_meeting(
            meeting_id=meeting_id, owner_id=1
        )
        assert audit.runs[0].status == "failed"
        assert audit.runs[0].final_error == "visual provider failed"
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            segments = session.exec(
                select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
            ).all()
            assert meeting.status == "completed"
            assert meeting.summary_text == "keep summary"
            assert meeting.transcript_text == "keep transcript"
            assert meeting.file_path == "users/1/meetings/video.mp4"
            assert meeting.duration_seconds == 120
            assert meeting.visual_breakdown_status == "failed"
            assert meeting.visual_breakdown_params["content_type"] == "video/mp4"
            assert meeting.visual_breakdown_error == "visual provider failed"
            assert meeting.visual_raw_output_s3_key == "users/1/meetings/visual/previous.json"
            assert [(segment.caption, segment.screenshot_s3_key) for segment in segments] == [
                ("Previous visual segment", "users/1/meetings/visual/previous.png")
            ]
    finally:
        _delete_meeting(meeting_id)


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


def test_success_effects_are_eligible_for_current_running_and_completed_attempt() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            meeting.status = "processing"
            saved_run.status = "running"
            session.add(meeting)
            session.add(saved_run)
            session.commit()

        assert asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, run.id, run.dispatch_attempt_id
            )
        )

        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            meeting.status = "completed"
            saved_run.status = "completed"
            session.add(meeting)
            session.add(saved_run)
            session.commit()

        assert asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, run.id, run.dispatch_attempt_id
            )
        )
    finally:
        _delete_meeting(meeting_id)


@pytest.mark.parametrize("invalid_state", ["cancelled", "superseded"])
def test_success_effects_reject_cancelled_or_superseded_attempt(invalid_state: str) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            if invalid_state == "cancelled":
                meeting.status = "cancelled"
                saved_run.status = "cancelled"
            else:
                saved_run.status = "failed"
            session.add(meeting)
            session.add(saved_run)
            session.commit()

        if invalid_state == "superseded":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.id != run.id

        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, run.id, run.dispatch_attempt_id
            )
        )
    finally:
        _delete_meeting(meeting_id)


def test_success_effects_reject_missing_records_attempt_and_meeting_run_mismatch() -> None:
    meeting_id = _make_meeting()
    other_meeting_id = _make_meeting(owner_id=2)
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        other_run = meeting_processing_audit.get_or_create_pending_run(other_meeting_id)
        assert run is not None and run.dispatch_attempt_id
        assert other_run is not None and other_run.dispatch_attempt_id
        with Session(engine) as session:
            for current_meeting_id, current_run_id in (
                (meeting_id, run.id),
                (other_meeting_id, other_run.id),
            ):
                meeting = session.get(Meeting, current_meeting_id)
                saved_run = session.get(MeetingProcessingRun, current_run_id)
                meeting.status = "processing"
                saved_run.status = "running"
                session.add(meeting)
                session.add(saved_run)
            session.commit()

        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                -meeting_id, run.id, run.dispatch_attempt_id
            )
        )
        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, -run.id, run.dispatch_attempt_id
            )
        )
        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, run.id, "different-attempt"
            )
        )
        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, run.id, ""
            )
        )
        assert not asyncio.run(
            meeting_processing_audit.success_effects_are_eligible(
                meeting_id, other_run.id, other_run.dispatch_attempt_id
            )
        )
    finally:
        _delete_meeting(other_meeting_id)
        _delete_meeting(meeting_id)


def test_transcription_notification_carries_exact_attempt_identity(tmp_path) -> None:
    from contextlib import nullcontext

    from app.services.transcription.types import TranscriptionConfig

    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id

        class FakeProvider:
            provider_name = "runpod"

            def transcribe(self, _request, **_callbacks):
                return SimpleNamespace(
                    text="transcript",
                    segments=[],
                    audio_duration_seconds=60,
                    language="en",
                )

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch(
                "app.worker._audit_attempt_id_from_task",
                return_value=run.dispatch_attempt_id,
            ),
            patch("app.worker._configured_transcription_provider", return_value="runpod"),
            patch("app.worker.TEMP_DIR", str(tmp_path)),
            patch("app.worker.meeting_service._publish_sub_status"),
            patch("app.worker._update_sub_status_for_attempt", return_value=True),
            patch("app.worker._resolve_meeting_language_for_transcription", return_value=(None, [])),
            patch("app.worker.build_config", return_value=TranscriptionConfig()),
            patch("app.worker.AudioSourceResolver.resolve_for_provider", return_value=object()),
            patch("app.worker.get_provider", return_value=nullcontext(FakeProvider())),
            patch("app.worker._save_transcription_for_attempt", return_value=True),
            patch("app.worker.analytics.capture"),
            patch("app.worker.notify") as notify,
        ):
            assert stage_transcribe.run(meeting_id) == meeting_id

        notify.assert_called_once_with(
            "transcription_completed",
            "audit1@test.com",
            "audit",
            meeting_id=meeting_id,
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
            extra={"Duration": "1 min"},
        )
    finally:
        _delete_meeting(meeting_id)


def test_notification_task_identity_gate_and_notify_legacy_enqueue() -> None:
    with (
        patch("app.services.notifications._enabled", True),
        patch("app.worker.send_notification.delay") as enqueue,
    ):
        notify(
            "summary_generated",
            "owner@example.com",
            "Review",
            meeting_id=12,
            processing_run_id=34,
            dispatch_attempt_id="attempt-56",
        )
        assert enqueue.call_args.kwargs == {
            "processing_run_id": 34,
            "dispatch_attempt_id": "attempt-56",
        }

        enqueue.reset_mock()
        notify("summary_generated", "owner@example.com", "Review", meeting_id=12)
        enqueue.assert_called_once()
        assert len(enqueue.call_args.args) == 6
        assert enqueue.call_args.kwargs == {}

        enqueue.reset_mock()
        notify(
            "summary_generated",
            "owner@example.com",
            "Review",
            meeting_id=12,
            processing_run_id=34,
        )
        enqueue.assert_not_called()

    with (
        patch("app.services.notifications.get_provider") as get_provider,
        patch(
            "app.worker.meeting_processing_audit.success_effects_are_eligible",
            return_value=False,
        ) as eligible,
    ):
        provider = get_provider.return_value
        send_notification.run(
            "📝",
            "Summary Generated",
            "owner@example.com",
            "Review",
            12,
            {},
            processing_run_id=34,
            dispatch_attempt_id="attempt-56",
        )
        eligible.assert_awaited_once_with(12, 34, "attempt-56")
        provider.send.assert_not_called()


def test_records_run_stage_lifecycle_with_safe_values() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert run is not None
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
        assert meeting_processing_audit.set_root_task(
            run.id, "root-task-123", attempt_id=run.dispatch_attempt_id
        )

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert len(audit.runs) == 1
        saved_run = audit.runs[0]
        assert saved_run.trigger == "reprocess"
        assert saved_run.status == "completed"
        assert audit.current_run_id == run.id
        assert saved_run.root_task_id == "root-task-123"
        assert [event.status for event in saved_run.events] == ["started", "completed", "completed"]
        assert "https://example.com" not in (saved_run.events[0].message or "")
        assert saved_run.events[0].metadata == {"provider": "minio", "token": "[redacted]"}
    finally:
        _delete_meeting(meeting_id)


def test_save_highlights_replaces_rows_for_exact_active_attempt() -> None:
    from app.services.meeting_intelligence import (
        HighlightSaveRejected,
        intelligence_service,
    )

    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        result = intelligence_service.save_highlights(
            meeting_id,
            {"chapters": [{"title": "Current chapter", "timestamp_start": 9}],
             "action_items": [], "key_questions": []},
            owner_id=1,
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
        )

        assert result is not HighlightSaveRejected.STALE_OR_CANCELLED
        with Session(engine) as session:
            highlights = session.exec(
                select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            ).all()
            assert [(item.highlight_type, item.content) for item in highlights] == [
                ("chapter", "Current chapter")
            ]
    finally:
        _delete_highlight_test_meeting(meeting_id)


@pytest.mark.parametrize("attempt_state", ["stale", "cancelled", "identityless"])
def test_save_highlights_rejects_stale_cancelled_or_identityless_attempts(
    attempt_state: str,
) -> None:
    from app.services.meeting_intelligence import (
        HighlightSaveRejected,
        intelligence_service,
    )

    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        attempt_id: str | None = run.dispatch_attempt_id
        run_id: int | None = run.id
        if attempt_state == "stale":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.dispatch_attempt_id != attempt_id
        elif attempt_state == "cancelled":
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
        else:
            run_id = attempt_id = None

        result = intelligence_service.save_highlights(
            meeting_id,
            {"chapters": [], "action_items": [], "key_questions": []},
            owner_id=1,
            processing_run_id=run_id,
            dispatch_attempt_id=attempt_id,
        )
        assert result is HighlightSaveRejected.STALE_OR_CANCELLED
        with Session(engine) as session:
            highlights = session.exec(
                select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            ).all()
            assert [(item.highlight_type, item.content) for item in highlights] == [
                ("topic", "previous highlight")
            ]
    finally:
        _delete_highlight_test_meeting(meeting_id)


def test_intelligence_stage_treats_rejected_highlight_write_as_ignore() -> None:
    from app.services.meeting_intelligence import HighlightSaveRejected

    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="intelligence-task"),
            patch("app.worker.meeting_service.touch_processing_heartbeat"),
            patch("app.worker.meeting_service.mark_completed") as mark_completed,
            patch(
                "app.services.meeting_intelligence.intelligence_service.extract_highlights",
                return_value={"chapters": [], "action_items": [], "key_questions": []},
            ),
            patch(
                "app.services.meeting_intelligence.intelligence_service.save_highlights",
                return_value=HighlightSaveRejected.STALE_OR_CANCELLED,
            ) as save_highlights,
            patch("app.services.meeting_intelligence.intelligence_service.extract_structured_output") as extract_structured,
            pytest.raises(Ignore),
        ):
            stage_extract_intelligence.run(meeting_id)

        save_highlights.assert_called_once_with(
            meeting_id,
            {"chapters": [], "action_items": [], "key_questions": []},
            owner_id=1,
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
        )
        extract_structured.assert_not_called()
        mark_completed.assert_not_called()
        with Session(engine) as session:
            highlights = session.exec(
                select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            ).all()
            assert [item.content for item in highlights] == ["previous highlight"]
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "processing"
            assert meeting.structured_output_status == "processing"
            assert meeting.structured_output is None
    finally:
        _delete_highlight_test_meeting(meeting_id)


def test_cancellation_is_distinct_and_fences_late_audit_callbacks() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None

        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=run.id, owner_id=1
        )
        meeting_processing_audit.stage_started(
            run_id=run.id, meeting_id=meeting_id, stage="stage_summarize"
        )
        meeting_processing_audit.run_failed(run.id, error="late provider failure")
        meeting_processing_audit.run_completed(run.id, message="late completion")

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert audit.current_run_id == run.id
        assert audit.runs[0].status == "cancelled"
        assert audit.runs[0].final_error is None
        assert [(event.stage, event.status) for event in audit.runs[0].events] == [
            ("run", "cancelled")
        ]
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "cancelled"
    finally:
        _delete_meeting(meeting_id)


def test_old_run_cannot_cancel_a_new_current_generation() -> None:
    meeting_id = _make_meeting()
    try:
        old_run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert old_run is not None
        meeting_processing_audit.run_failed(old_run.id, error="attempt failed")
        new_run = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert new_run is not None and new_run.id != old_run.id

        assert not meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=old_run.id, owner_id=1
        )
        assert meeting_processing_audit.get_pending_run(meeting_id).id == new_run.id
    finally:
        _delete_meeting(meeting_id)


def test_audit_callbacks_reject_previous_dispatch_for_the_same_run() -> None:
    meeting_id = _make_meeting()
    try:
        first_dispatch = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert first_dispatch is not None
        old_attempt_id = first_dispatch.dispatch_attempt_id

        recovery_dispatch = meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="recovery")
        assert recovery_dispatch is not None
        assert recovery_dispatch.id == first_dispatch.id
        latest_attempt_id = recovery_dispatch.dispatch_attempt_id
        assert latest_attempt_id is not None
        assert latest_attempt_id != old_attempt_id

        def start_stage(run_id: int, attempt_id: str | None):
            return meeting_processing_audit.stage_started(
                run_id=run_id,
                attempt_id=attempt_id,
                meeting_id=meeting_id,
                stage="stage_download",
            )

        stale_event = start_stage(first_dispatch.id, old_attempt_id)
        stale_sender = _FakeTask(
            name="stage_download",
            request=_FakeRequest(
                task_id="stale-task",
                args=(meeting_id,),
                run_id=first_dispatch.id,
                attempt_id=old_attempt_id,
            ),
        )
        _record_audit_stage_succeeded(sender=stale_sender, result=meeting_id)
        _record_audit_stage_failed(
            task_id="stale-task",
            exception=RuntimeError("stale attempt failure"),
            args=(meeting_id,),
            sender=stale_sender,
        )
        current_event = start_stage(recovery_dispatch.id, latest_attempt_id)
        current_sender = _FakeTask(
            name="stage_download",
            request=_FakeRequest(
                task_id="current-task",
                args=(meeting_id,),
                run_id=recovery_dispatch.id,
                attempt_id=latest_attempt_id,
            ),
        )
        _record_audit_stage_succeeded(sender=current_sender, result=meeting_id)

        assert stale_event is None
        assert current_event is not None
        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert len(audit.runs) == 1
        assert audit.runs[0].dispatch_attempt_id == latest_attempt_id
        assert [event.status for event in audit.runs[0].events] == ["started", "completed"]
        assert audit.runs[0].status == "running"
    finally:
        _delete_meeting(meeting_id)


def test_audit_read_hides_current_run_from_foreign_owner() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=2)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Meeting not found"
    finally:
        _delete_meeting(meeting_id)


def test_pipeline_signatures_attach_headers_without_embedding_link_error() -> None:
    signatures = _build_pipeline_signatures(
        123, [stage_download, stage_embedding], 456, "attempt-456"
    )

    assert signatures[0].options["headers"]["meeting_processing_run_id"] == "456"
    assert signatures[0].options["headers"]["dispatch_attempt_id"] == "attempt-456"
    assert signatures[0].options["headers"]["meeting_id"] == "123"
    assert signatures[0].options["headers"]["stage"] == "stage_download"
    assert signatures[0].options["link_error"]
    assert signatures[1].options["headers"]["stage"] == "stage_embedding"
    assert signatures[1].options["link_error"] == []


def test_standard_dispatch_threads_run_and_attempt_identity_to_chain() -> None:
    run = SimpleNamespace(id=456, dispatch_attempt_id="attempt-456")
    with (
        patch("app.worker.meeting_processing_audit.get_or_create_pending_run", return_value=run),
        patch("app.worker.meeting_processing_audit.set_root_task") as set_root_task,
        patch("app.worker.chain") as chain_mock,
    ):
        chain_mock.return_value.apply_async.return_value.id = "root-task"

        dispatch_pipeline(123)

    signatures = chain_mock.call_args.args
    assert all(
        signature.options["headers"]["meeting_processing_run_id"] == "456"
        and signature.options["headers"]["dispatch_attempt_id"] == "attempt-456"
        for signature in signatures
    )
    set_root_task.assert_called_once_with(456, "root-task", attempt_id="attempt-456")


def test_dispatchers_refuse_missing_processing_identity() -> None:
    with (
        patch("app.worker.meeting_processing_audit.get_or_create_pending_run", return_value=None) as get_run,
        patch("app.worker.meeting_processing_audit.set_root_task") as set_root_task,
        patch("app.worker.chain") as chain_mock,
    ):
        dispatch_pipeline(123)
        dispatch_youtube_pipeline(123)
        _dispatch_recovery_stages(123, [stage_download])
        _dispatch_recovery_plan(123, SimpleNamespace(action="finalize"))

    assert get_run.call_count == 4
    chain_mock.assert_not_called()
    set_root_task.assert_not_called()


def test_success_signal_uses_sender_request_and_completes_embedding_run() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="pipeline")
        assert run is not None
        sender = _FakeTask(
            name="stage_embedding",
            request=_FakeRequest(
                task_id="embedding-task",
                args=(meeting_id,),
                run_id=run.id,
                attempt_id=run.dispatch_attempt_id,
            ),
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
            request=_FakeRequest(
                task_id="embedding-task",
                args=(meeting_id,),
                run_id=run.id,
                attempt_id=run.dispatch_attempt_id,
            ),
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
        request = _FakeRequest(
            task_id="embedding-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        sender = _FakeTask(name="stage_embedding", request=request)

        _record_audit_stage_failed(
            task_id="embedding-task",
            exception=RuntimeError("embedding failed"),
            args=(meeting_id,),
            sender=sender,
        )
        with patch("app.worker.meeting_service.mark_failed") as mark_failed:
            mark_failed.return_value = None
            on_stage_failure(request, RuntimeError("embedding failed"), None)
        mark_failed.assert_called_once_with(
            meeting_id,
            "embedding failed",
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
        )

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        saved_run = audit.runs[0]
        stage_failures = [event for event in saved_run.events if event.stage == "stage_embedding" and event.status == "failed"]
        run_failures = [event for event in saved_run.events if event.stage == "run" and event.status == "failed"]
        assert len(stage_failures) == 1
        assert len(run_failures) == 1
        assert saved_run.status == "failed"
    finally:
        _delete_meeting(meeting_id)


def test_visual_failure_callback_fails_only_visual_run_and_suppresses_pipeline_effects() -> None:
    meeting_id = _make_meeting()
    try:
        _seed_completed_visual_meeting(meeting_id)
        admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_id)
        assert admitted is not None
        run, _epoch = admitted
        request = _FakeRequest(
            task_id="visual-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        error = RuntimeError("visual provider failed")

        _assert_failure_callback_has_no_side_effects(request, error)

        audit = meeting_processing_audit.list_for_meeting(
            meeting_id=meeting_id, owner_id=1
        )
        assert audit.runs[0].status == "failed"
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "completed"
            assert meeting.visual_breakdown_status == "failed"
    finally:
        _delete_meeting(meeting_id)


def test_stale_attempt_callbacks_cannot_change_run_or_meeting_status() -> None:
    meeting_id = _make_meeting()
    try:
        old_dispatch = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert old_dispatch is not None
        stale_attempt_id = old_dispatch.dispatch_attempt_id
        current_dispatch = meeting_processing_audit.get_or_create_pending_run(
            meeting_id, trigger="recovery"
        )
        assert current_dispatch is not None
        assert current_dispatch.dispatch_attempt_id != stale_attempt_id

        stale_task = _FakeTask(
            name="stage_embedding",
            request=_FakeRequest(
                task_id="stale-embedding-task",
                args=(meeting_id,),
                run_id=old_dispatch.id,
                attempt_id=stale_attempt_id,
            ),
        )
        _record_audit_stage_succeeded(sender=stale_task, result=meeting_id)
        _record_audit_stage_failed(
            task_id="stale-embedding-task",
            exception=RuntimeError("stale failure"),
            args=(meeting_id,),
            sender=stale_task,
        )
        _assert_failure_callback_has_no_side_effects(
            stale_task.request, RuntimeError("stale failure")
        )

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert audit.runs[0].status == "queued"
        assert audit.runs[0].events == []
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "queued"
    finally:
        _delete_meeting(meeting_id)


def test_cancelled_attempt_failure_callback_does_not_overwrite_status() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=run.id, owner_id=1
        )
        request = _FakeRequest(
            task_id="cancelled-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )

        _assert_failure_callback_has_no_side_effects(
            request, RuntimeError("late failure")
        )

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert audit.runs[0].status == "cancelled"
        assert [(event.stage, event.status) for event in audit.runs[0].events] == [
            ("run", "cancelled")
        ]
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "cancelled"
    finally:
        _delete_meeting(meeting_id)


def test_failure_effects_are_suppressed_if_retry_supersedes_before_admission() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        request = _FakeRequest(
            task_id="superseded-failure-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        run_failed = meeting_processing_audit.run_failed

        def fail_then_retry(*args, **kwargs):
            assert run_failed(*args, **kwargs)
            retry = meeting_processing_audit.create_run(meeting_id, trigger="retry")
            assert retry is not None and retry.id != run.id

        with (
            patch.object(meeting_processing_audit, "run_failed", side_effect=fail_then_retry),
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_failure_email") as email,
        ):
            on_stage_failure(request, RuntimeError("superseded failure"), None)

        capture.assert_not_called()
        email.assert_not_called()
        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert audit.current_run_id != run.id
        assert audit.runs[1].status == "failed"
    finally:
        _delete_meeting(meeting_id)


def test_failure_effects_are_suppressed_if_cancelled_before_admission() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        request = _FakeRequest(
            task_id="cancelled-failure-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        mark_failed = meeting_service.mark_failed

        def fail_then_cancel(*args, **kwargs):
            failed_meeting = mark_failed(*args, **kwargs)
            assert failed_meeting is not None
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
            return failed_meeting

        with (
            patch.object(meeting_service, "mark_failed", side_effect=fail_then_cancel),
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_failure_email") as email,
        ):
            on_stage_failure(request, RuntimeError("cancelled failure"), None)

        capture.assert_not_called()
        email.assert_not_called()
        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        assert audit.runs[0].status == "cancelled"
        assert meeting_service.get(Meeting, meeting_id).status == "cancelled"
    finally:
        _delete_meeting(meeting_id)


def test_recovery_completion_rejects_stale_attempt_and_accepts_current_attempt() -> None:
    meeting_id = _make_meeting()
    try:
        stale_run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        stale_attempt_id = stale_run.dispatch_attempt_id
        current_run = meeting_processing_audit.get_or_create_pending_run(
            meeting_id, trigger="recovery"
        )
        assert current_run.id == stale_run.id
        assert current_run.dispatch_attempt_id != stale_attempt_id
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            meeting.status = "processing"
            meeting.structured_output_status = "completed"
            meeting.structured_output = {"topics": []}
            session.add(meeting)
            session.commit()

        assert not meeting_service.mark_completed_for_processing_run(
            meeting_id,
            processing_run_id=stale_run.id,
            dispatch_attempt_id=stale_attempt_id,
        )
        assert meeting_service.get(Meeting, meeting_id).status == "processing"
        assert meeting_service.mark_completed_for_processing_run(
            meeting_id,
            processing_run_id=current_run.id,
            dispatch_attempt_id=current_run.dispatch_attempt_id,
        )
        assert meeting_service.get(Meeting, meeting_id).status == "completed"
    finally:
        _delete_meeting(meeting_id)


def test_recovery_finalizer_requires_output_and_applied_completion() -> None:
    meeting = SimpleNamespace(structured_output_status="pending", structured_output=None)
    with (
        patch("app.worker.meeting_service.get", return_value=meeting),
        patch("app.worker._audit_run_id_from_task", return_value=7),
        patch("app.worker._audit_attempt_id_from_task", return_value="attempt-7"),
        patch("app.worker.meeting_service.mark_completed_for_processing_run", return_value=False) as complete,
    ):
        with pytest.raises(RuntimeError, match="completed structured output"):
            finalize_recovered_meeting.run(123)
        complete.assert_not_called()

        meeting.structured_output_status = "completed"
        meeting.structured_output = {"topics": []}
        with pytest.raises(RuntimeError, match="was rejected"):
            finalize_recovered_meeting.run(123)
        complete.assert_called_once_with(
            123, processing_run_id=7, dispatch_attempt_id="attempt-7"
        )

        complete.reset_mock()
        complete.return_value = True
        assert finalize_recovered_meeting.run(123) == 123
        complete.assert_called_once_with(
            123, processing_run_id=7, dispatch_attempt_id="attempt-7"
        )

        sender = _FakeTask(
            name="finalize_recovered_meeting",
            request=_FakeRequest(
                task_id="recovery-finalizer",
                args=(123,),
                run_id=7,
                attempt_id="attempt-7",
            ),
        )
        with (
            patch("app.worker.meeting_processing_audit.stage_completed"),
            patch("app.worker.meeting_processing_audit.run_completed") as run_completed,
        ):
            _record_audit_stage_succeeded(sender=sender, result=124)
            run_completed.assert_not_called()
            _record_audit_stage_succeeded(sender=sender, result=123)
        run_completed.assert_called_once_with(
            7, attempt_id="attempt-7", message="Processing pipeline completed"
        )


def test_stale_transcription_attempt_is_ignored_and_cleans_only_its_temp(tmp_path) -> None:
    meeting_id = _make_meeting()
    try:
        stale = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        current = meeting_processing_audit.get_or_create_pending_run(
            meeting_id, trigger="retry"
        )
        assert stale is not None and current is not None
        assert stale.id == current.id
        assert stale.dispatch_attempt_id != current.dispatch_attempt_id

        _seed_transcription_outputs(meeting_id)

        with patch("app.worker.TEMP_DIR", str(tmp_path)):
            stale_path = _temp_path_for(
                meeting_id, "users/1/audio.mp3", run_id=stale.id,
                attempt_id=stale.dispatch_attempt_id,
            )
            current_path = _temp_path_for(
                meeting_id, "users/1/audio.mp3", run_id=current.id,
                attempt_id=current.dispatch_attempt_id,
            )
            open(stale_path, "wb").close()
            open(current_path, "wb").close()
            with (
                patch("app.worker._audit_run_id_from_task", return_value=stale.id),
                patch("app.worker._audit_attempt_id_from_task", return_value=stale.dispatch_attempt_id),
                patch("app.worker._probe_local_media") as probe,
                pytest.raises(Ignore),
            ):
                stage_transcribe.run(meeting_id)
            probe.assert_not_called()
            assert not os.path.exists(stale_path)
            assert os.path.exists(current_path)

        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.transcript_text == "keep transcript"
            assert meeting.summary_text == "keep summary"
            assert meeting.structured_output == {"keep": True}
            assert meeting.structured_output_status == "completed"
            assert meeting.duration_seconds == 77
            assert session.exec(select(TranscriptSegment).where(
                TranscriptSegment.meeting_id == meeting_id
            )).first().text == "keep segment"
            assert session.exec(select(MeetingHighlight).where(
                MeetingHighlight.meeting_id == meeting_id
            )).first().content == "keep highlight"
    finally:
        with Session(engine) as session:
            session.exec(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id))
            session.exec(delete(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id))
            session.commit()
        _delete_meeting(meeting_id)


def test_cancelled_attempt_cannot_apply_transcript_or_no_audio_skip() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transcription_outputs(meeting_id)

        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=run.id, owner_id=1
        )
        late_result = SimpleNamespace(text="late transcript", segments=[], audio_duration_seconds=180)
        assert not _save_transcription_for_attempt(
            meeting_id, run.id, run.dispatch_attempt_id, late_result,
            owner_id=1, user_tier="free",
        )
        assert not _mark_transcription_skipped_for_no_audio(
            meeting_id, 180, run.id, run.dispatch_attempt_id
        )
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.status == "cancelled"
            assert meeting.transcript_text == "keep transcript"
            assert meeting.summary_text == "keep summary"
            assert meeting.structured_output == {"keep": True}
            assert meeting.duration_seconds == 77
            assert session.exec(select(TranscriptSegment).where(
                TranscriptSegment.meeting_id == meeting_id
            )).first().text == "keep segment"
            assert session.exec(select(MeetingHighlight).where(
                MeetingHighlight.meeting_id == meeting_id
            )).first().content == "keep highlight"
    finally:
        with Session(engine) as session:
            session.exec(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id))
            session.exec(delete(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id))
            session.commit()
        _delete_meeting(meeting_id)


def test_transliteration_current_attempt_writes_output_without_unfenced_status_change() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        requested = SimpleNamespace(transliterate_from="source", code="target")

        task_id = "transliteration-task"
        meeting_processing_audit.stage_started(
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
            meeting_id=meeting_id,
            stage="stage_transliterate",
            task_id=task_id,
            metadata={"celery_task_name": "stage_transliterate"},
        )

        def assert_claimed_before_provider(**_kwargs):
            with Session(engine) as session:
                events = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                    .where(MeetingProcessingEvent.stage == "stage_transliterate")
                    .where(MeetingProcessingEvent.event_type == "started")
                    .where(MeetingProcessingEvent.task_id == task_id)
                ).all()
                assert len(events) == 1
                assert events[0].metadata_["provider_claimed"] is True
                assert events[0].started_at is not None
            return "translated text"

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value=task_id),
            patch("app.services.languages.catalog.get_entry", return_value=requested),
            patch(
                "app.services.languages.transliteration.transliterate",
                side_effect=assert_claimed_before_provider,
            ) as translate,
            patch("app.worker.analytics.capture") as capture,
        ):
            assert stage_transliterate.run(meeting_id) == meeting_id

        translate.assert_called_once_with(
            text="source text", source_code="source", target_code="target"
        )
        capture.assert_not_called()
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.transliterated_text == "translated text"
            assert meeting.status == "processing"
            assert meeting.sub_status == "before-transliteration"
    finally:
        _delete_meeting(meeting_id)


def test_duplicate_transliteration_delivery_claims_once_during_provider_work() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        requested = SimpleNamespace(transliterate_from="source", code="target")
        provider_started = Event()
        release_provider = Event()
        call_lock = Lock()
        provider_calls = 0

        def slow_transliterate(**_kwargs):
            nonlocal provider_calls
            with call_lock:
                provider_calls += 1
            provider_started.set()
            assert release_provider.wait(timeout=10)
            return "translated once"

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="duplicate-delivery-task"),
            patch("app.services.languages.catalog.get_entry", return_value=requested),
            patch(
                "app.services.languages.transliteration.transliterate",
                side_effect=slow_transliterate,
            ),
            patch("app.worker.analytics.capture") as capture,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(stage_transliterate.run, meeting_id)
                assert provider_started.wait(timeout=10)
                duplicate = executor.submit(stage_transliterate.run, meeting_id)
                try:
                    with pytest.raises(Ignore):
                        duplicate.result(timeout=10)
                finally:
                    release_provider.set()
                assert first.result(timeout=10) == meeting_id

        assert provider_calls == 1
        capture.assert_not_called()
        with Session(engine) as session:
            claims = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                .where(MeetingProcessingEvent.stage == "stage_transliterate")
                .where(MeetingProcessingEvent.event_type == "started")
            ).all()
            failures = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                .where(MeetingProcessingEvent.stage == "stage_transliterate")
                .where(MeetingProcessingEvent.event_type == "failed")
            ).all()
            meeting = session.get(Meeting, meeting_id)
            assert len(claims) == 1
            assert failures == []
            assert meeting.transliterated_text == "translated once"
            assert meeting.status == "processing"
            assert meeting.sub_status == "before-transliteration"
    finally:
        _delete_meeting(meeting_id)


def test_stale_transliteration_attempt_is_ignored_before_work_or_status_change() -> None:
    meeting_id = _make_meeting()
    try:
        stale = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        current = meeting_processing_audit.get_or_create_pending_run(
            meeting_id, trigger="retry"
        )
        assert stale is not None and current is not None
        _seed_transliteration_input(meeting_id)
        with (
            patch("app.worker._audit_run_id_from_task", return_value=stale.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=stale.dispatch_attempt_id),
            patch("app.services.languages.catalog.get_entry") as get_entry,
            patch("app.services.languages.transliteration.transliterate") as translate,
            patch("app.worker.analytics.capture") as capture,
            pytest.raises(Ignore),
        ):
            stage_transliterate.run(meeting_id)

        get_entry.assert_not_called()
        translate.assert_not_called()
        capture.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.transliterated_text == "previous output"
        assert meeting.status == "processing"
        assert meeting.sub_status == "before-transliteration"
    finally:
        _delete_meeting(meeting_id)


def test_cancelled_transliteration_attempt_is_ignored_before_work() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id, run_id=run.id, owner_id=1
        )
        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.services.languages.catalog.get_entry") as get_entry,
            patch("app.services.languages.transliteration.transliterate") as translate,
            patch("app.worker.analytics.capture") as capture,
            pytest.raises(Ignore),
        ):
            stage_transliterate.run(meeting_id)

        get_entry.assert_not_called()
        translate.assert_not_called()
        capture.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.transliterated_text == "previous output"
        assert meeting.status == "cancelled"
        assert meeting.sub_status is None
    finally:
        _delete_meeting(meeting_id)


def test_identityless_transliteration_is_ignored_without_status_change() -> None:
    meeting_id = _make_meeting()
    try:
        _seed_transliteration_input(meeting_id)
        with (
            patch("app.worker._audit_run_id_from_task", return_value=None),
            patch("app.worker._audit_attempt_id_from_task", return_value=None),
            patch("app.services.languages.transliteration.transliterate") as translate,
            pytest.raises(Ignore),
        ):
            stage_transliterate.run(meeting_id)

        translate.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.transliterated_text == "previous output"
        assert meeting.status == "processing"
        assert meeting.sub_status == "before-transliteration"
    finally:
        _delete_meeting(meeting_id)


def test_transliteration_result_is_rejected_if_attempt_is_cancelled_during_work() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        requested = SimpleNamespace(transliterate_from="source", code="target")

        def cancel_during_transliteration(**_kwargs):
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
            return "late output"

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.services.languages.catalog.get_entry", return_value=requested),
            patch(
                "app.services.languages.transliteration.transliterate",
                side_effect=cancel_during_transliteration,
            ),
            patch("app.worker.analytics.capture") as capture,
            pytest.raises(Ignore),
        ):
            stage_transliterate.run(meeting_id)

        capture.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.transliterated_text == "previous output"
        assert meeting.status == "cancelled"
        assert meeting.sub_status is None
        with Session(engine) as session:
            claims = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                .where(MeetingProcessingEvent.stage == "stage_transliterate")
                .where(MeetingProcessingEvent.event_type == "started")
            ).all()
            assert len(claims) == 1
            assert claims[0].metadata_["provider_claimed"] is True
            assert claims[0].started_at is not None
    finally:
        _delete_meeting(meeting_id)


def test_transliteration_provider_is_not_called_if_cancellation_wins_before_claim() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        requested = SimpleNamespace(transliterate_from="source", code="target")
        task_id = "cancel-before-claim"
        meeting_processing_audit.stage_started(
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
            meeting_id=meeting_id,
            stage="stage_transliterate",
            task_id=task_id,
            metadata={"celery_task_name": "stage_transliterate"},
        )
        claim_transliteration = meeting_processing_audit.claim_transliteration

        def cancel_before_claim(**kwargs):
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
            return claim_transliteration(**kwargs)

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value=task_id),
            patch("app.services.languages.catalog.get_entry", return_value=requested),
            patch.object(
                meeting_processing_audit,
                "claim_transliteration",
                side_effect=cancel_before_claim,
            ),
            patch("app.services.languages.transliteration.transliterate") as translate,
            patch("app.worker.analytics.capture") as capture,
            pytest.raises(Ignore),
        ):
            stage_transliterate.run(meeting_id)

        translate.assert_not_called()
        capture.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.transliterated_text == "previous output"
        assert meeting.status == "cancelled"
        assert meeting.sub_status is None
        with Session(engine) as session:
            claims = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                .where(MeetingProcessingEvent.stage == "stage_transliterate")
                .where(MeetingProcessingEvent.event_type == "started")
            ).all()
            assert len(claims) == 1
            assert claims[0].metadata_.get("provider_claimed") is not True
            assert claims[0].started_at is None
    finally:
        _delete_meeting(meeting_id)


def test_transliteration_failure_uses_single_current_attempt_failure_path() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_transliteration_input(meeting_id)
        requested = SimpleNamespace(transliterate_from="source", code="target")
        task_id = "transliteration-failure-task"
        error = RuntimeError("transliteration provider failed")
        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value=task_id),
            patch("app.services.languages.catalog.get_entry", return_value=requested),
            patch("app.services.languages.transliteration.transliterate", side_effect=error),
            patch("app.worker.analytics.capture") as direct_capture,
            pytest.raises(RuntimeError, match="transliteration provider failed"),
        ):
            stage_transliterate.run(meeting_id)
        direct_capture.assert_not_called()

        request = _FakeRequest(
            task_id=task_id,
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        sender = _FakeTask(name="stage_transliterate", request=request)
        for _ in range(2):
            _record_audit_stage_failed(
                task_id=task_id,
                exception=error,
                args=(meeting_id,),
                sender=sender,
            )

        with (
            patch("app.worker.analytics.capture") as accepted_capture,
            patch("app.services.email.email_service.send_failure_email") as email,
        ):
            on_stage_failure(request, error, None)
            on_stage_failure(request, error, None)

        audit = meeting_processing_audit.list_for_meeting(meeting_id=meeting_id, owner_id=1)
        saved_run = audit.runs[0]
        assert saved_run.status == "failed"
        assert sum(
            event.stage == "stage_transliterate" and event.status == "failed"
            for event in saved_run.events
        ) == 1
        assert sum(event.stage == "run" and event.status == "failed" for event in saved_run.events) == 1
        accepted_capture.assert_called_once()
        email.assert_called_once()
        assert meeting_service.get(Meeting, meeting_id).status == "failed"
    finally:
        _delete_meeting(meeting_id)


def test_summary_current_attempt_is_single_use_and_saves_before_notifying() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_summary_input(meeting_id)
        task_id = "summary-delivery-task"
        provider_started = Event()
        release_provider = Event()
        call_lock = Lock()
        provider_calls = 0

        def slow_summary(*_args, **_kwargs):
            nonlocal provider_calls
            with call_lock:
                provider_calls += 1
            with Session(engine) as session:
                claims = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                    .where(MeetingProcessingEvent.stage == "stage_summarize")
                    .where(MeetingProcessingEvent.event_type == "started")
                ).all()
                assert len(claims) == 1
                assert claims[0].metadata_["provider_claimed"] is True
                assert claims[0].started_at is not None
            provider_started.set()
            assert release_provider.wait(timeout=10)
            return "generated summary"

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value=task_id),
            patch("app.worker.style_service.get_style_examples", return_value=""),
            patch("app.worker.template_service.get_active_default", return_value=None),
            patch("app.worker.get_completion_client", return_value=object()),
            patch("app.worker.summarize_transcript", side_effect=slow_summary),
            patch("app.services.ai_agent.infer_title", return_value="Quarterly Review"),
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_summary_email") as email,
            patch("app.worker.notify") as notify,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(stage_summarize.run, meeting_id)
                try:
                    assert provider_started.wait(timeout=10)
                    duplicate = executor.submit(stage_summarize.run, meeting_id)
                    with pytest.raises(Ignore):
                        duplicate.result(timeout=10)
                    capture.assert_not_called()
                    email.assert_not_called()
                    notify.assert_not_called()
                    with Session(engine) as session:
                        meeting = session.get(Meeting, meeting_id)
                        assert meeting.status == "processing"
                        assert meeting.sub_status == "summarizing"
                        assert meeting.summary_text == "previous summary"
                        assert meeting.title == "recording.mp3"
                finally:
                    release_provider.set()
                assert first.result(timeout=10) == meeting_id

        assert provider_calls == 1
        capture.assert_called_once()
        email.assert_called_once()
        notify.assert_called_once_with(
            "summary_generated",
            "audit1@test.com",
            "Quarterly Review",
            meeting_id=meeting_id,
            processing_run_id=run.id,
            dispatch_attempt_id=run.dispatch_attempt_id,
        )
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            claims = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                .where(MeetingProcessingEvent.stage == "stage_summarize")
                .where(MeetingProcessingEvent.event_type == "started")
            ).all()
            assert meeting.summary_text == "generated summary"
            assert meeting.title == "Quarterly Review"
            assert meeting.status == "processing"
            assert meeting.sub_status == "summarizing"
            assert len(claims) == 1
    finally:
        _delete_meeting(meeting_id)


@pytest.mark.parametrize("identity_mode", ["stale", "identityless"])
def test_summary_stale_or_identityless_attempt_has_no_provider_or_side_effects(identity_mode: str) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_summary_input(meeting_id)
        run_id, attempt_id = run.id, run.dispatch_attempt_id
        if identity_mode == "stale":
            meeting_processing_audit.get_or_create_pending_run(meeting_id, trigger="retry")
        else:
            run_id = attempt_id = None

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run_id),
            patch("app.worker._audit_attempt_id_from_task", return_value=attempt_id),
            patch("app.worker._task_request_id", return_value="stale-summary-task"),
            patch("app.worker.summarize_transcript") as summarize,
            patch("app.services.ai_agent.infer_title") as infer_title,
            patch("app.worker._update_sub_status_for_attempt") as update_status,
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_summary_email") as email,
            patch("app.worker.notify") as notify,
            pytest.raises(Ignore),
        ):
            stage_summarize.run(meeting_id)

        summarize.assert_not_called()
        infer_title.assert_not_called()
        update_status.assert_not_called()
        capture.assert_not_called()
        email.assert_not_called()
        notify.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.summary_text == "previous summary"
        assert meeting.title == "recording.mp3"
        assert meeting.status == "processing"
        assert meeting.sub_status == "before-summary"
    finally:
        _delete_meeting(meeting_id)


def test_summary_result_is_ignored_when_cancellation_wins_during_provider_call() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_summary_input(meeting_id)

        def cancel_during_summary(*_args, **_kwargs):
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
            return "late summary"

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="cancelled-summary-task"),
            patch("app.worker.style_service.get_style_examples", return_value=""),
            patch("app.worker.template_service.get_active_default", return_value=None),
            patch("app.worker.get_completion_client", return_value=object()),
            patch("app.worker.summarize_transcript", side_effect=cancel_during_summary) as summarize,
            patch("app.services.ai_agent.infer_title") as infer_title,
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_summary_email") as email,
            patch("app.worker.notify") as notify,
            pytest.raises(Ignore),
        ):
            stage_summarize.run(meeting_id)

        summarize.assert_called_once()
        infer_title.assert_not_called()
        capture.assert_not_called()
        email.assert_not_called()
        notify.assert_not_called()
        meeting = meeting_service.get(Meeting, meeting_id)
        assert meeting.summary_text == "previous summary"
        assert meeting.title == "recording.mp3"
        assert meeting.status == "cancelled"
        assert meeting.sub_status is None
    finally:
        _delete_meeting(meeting_id)


@pytest.mark.parametrize("identity_state", ["missing", "stale", "cancelled"])
def test_intelligence_stage_rejects_missing_or_inactive_identity_before_extraction(
    identity_state: str,
) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        run_id, attempt_id = run.id, run.dispatch_attempt_id
        if identity_state == "missing":
            run_id = attempt_id = None
        elif identity_state == "stale":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.dispatch_attempt_id != attempt_id
        else:
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run_id),
            patch("app.worker._audit_attempt_id_from_task", return_value=attempt_id),
            patch("app.worker._task_request_id", return_value="intelligence-admission-task"),
            patch("app.worker.meeting_service.touch_processing_heartbeat") as heartbeat,
            patch("app.worker.meeting_processing_audit.claim_intelligence", wraps=meeting_processing_audit.claim_intelligence) as claim,
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights") as extract,
            pytest.raises(Ignore),
        ):
            stage_extract_intelligence.run(meeting_id)

        extract.assert_not_called()
        heartbeat.assert_not_called()
        assert claim.call_count == (0 if identity_state == "missing" else 1)
    finally:
        _delete_highlight_test_meeting(meeting_id)


def test_intelligence_claim_is_single_use_per_attempt_and_reopens_for_new_attempt() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        first_attempt = run.dispatch_attempt_id
        first_start = meeting_processing_audit.stage_started(
            run_id=run.id,
            attempt_id=first_attempt,
            meeting_id=meeting_id,
            stage="stage_extract_intelligence",
            task_id="first-intelligence-task",
        )
        assert first_start is not None
        first_claim = meeting_processing_audit.claim_intelligence(
            run_id=run.id,
            attempt_id=first_attempt,
            meeting_id=meeting_id,
            task_id="first-intelligence-task",
        )
        assert first_claim is not None and first_claim.id == first_start.id
        assert first_claim.metadata_["provider_claimed"] is True
        duplicate_start = meeting_processing_audit.stage_started(
            run_id=run.id,
            attempt_id=first_attempt,
            meeting_id=meeting_id,
            stage="stage_extract_intelligence",
            task_id="duplicate-intelligence-task",
        )
        assert duplicate_start is not None and duplicate_start.id == first_claim.id
        assert meeting_processing_audit.claim_intelligence(
            run_id=run.id,
            attempt_id=first_attempt,
            meeting_id=meeting_id,
            task_id="duplicate-intelligence-task",
        ) is None

        next_dispatch = meeting_processing_audit.get_or_create_pending_run(
            meeting_id, trigger="retry"
        )
        assert next_dispatch is not None
        next_attempt = next_dispatch.dispatch_attempt_id
        assert next_attempt != first_attempt
        second_claim = meeting_processing_audit.claim_intelligence(
            run_id=run.id,
            attempt_id=next_attempt,
            meeting_id=meeting_id,
            task_id="retry-intelligence-task",
        )
        assert second_claim is not None
        assert second_claim.dispatch_attempt_id == next_attempt
        with Session(engine) as session:
            starts = session.exec(
                select(MeetingProcessingEvent)
                .where(MeetingProcessingEvent.run_id == run.id)
                .where(MeetingProcessingEvent.stage == "stage_extract_intelligence")
                .where(MeetingProcessingEvent.event_type == "started")
            ).all()
            assert {event.dispatch_attempt_id for event in starts} == {
                first_attempt, next_attempt
            }
            assert all(event.metadata_["provider_claimed"] is True for event in starts)
    finally:
        _delete_meeting(meeting_id)


def test_duplicate_intelligence_delivery_is_rejected_before_second_extraction() -> None:
    class AdmissionStop(BaseException):
        pass

    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        extraction_started = Event()
        release_extraction = Event()

        def stop_after_claim(_transcript: str):
            with Session(engine) as session:
                event = session.exec(
                    select(MeetingProcessingEvent)
                    .where(MeetingProcessingEvent.run_id == run.id)
                    .where(MeetingProcessingEvent.dispatch_attempt_id == run.dispatch_attempt_id)
                    .where(MeetingProcessingEvent.stage == "stage_extract_intelligence")
                    .where(MeetingProcessingEvent.event_type == "started")
                ).one()
                assert event.metadata_["provider_claimed"] is True
                assert event.started_at is not None
            extraction_started.set()
            assert release_extraction.wait(timeout=10)
            raise AdmissionStop()

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="duplicate-intelligence-task"),
            patch("app.worker.meeting_service.touch_processing_heartbeat"),
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights", side_effect=stop_after_claim) as extract,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(stage_extract_intelligence.run, meeting_id)
                try:
                    assert extraction_started.wait(timeout=10)
                    duplicate = executor.submit(stage_extract_intelligence.run, meeting_id)
                    with pytest.raises(Ignore):
                        duplicate.result(timeout=10)
                finally:
                    release_extraction.set()
                with pytest.raises(AdmissionStop):
                    first.result(timeout=10)

        extract.assert_called_once_with("highlight source")
    finally:
        _delete_highlight_test_meeting(meeting_id)


def test_intelligence_stage_persists_output_and_completes_current_meeting_attempt() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        highlights_data = {
            "chapters": [{"title": "Current chapter", "timestamp_start": 12}],
            "action_items": [],
            "key_questions": [],
        }
        structured_output = {"decisions": ["approved"]}
        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="intelligence-success-task"),
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights", return_value=highlights_data),
            patch("app.services.meeting_intelligence.intelligence_service.extract_structured_output", return_value=structured_output),
        ):
            assert stage_extract_intelligence.run(meeting_id) == meeting_id

        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved = session.exec(
                select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            ).all()
            saved_run = session.get(MeetingProcessingRun, run.id)
            assert meeting.structured_output == structured_output
            assert meeting.structured_output_status == "completed"
            assert meeting.status == "completed"
            assert meeting.sub_status is None
            assert [(item.highlight_type, item.content) for item in saved] == [
                ("chapter", "Current chapter")
            ]
            assert saved_run.status == "running"  # Pipeline run completes after embedding.
    finally:
        _delete_highlight_test_meeting(meeting_id)


@pytest.mark.parametrize("race", ["stale", "cancelled"])
def test_intelligence_stage_rejects_structured_result_if_attempt_becomes_inactive(
    race: str,
) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)

        def make_attempt_inactive(_transcript: str, _meeting_type: str):
            if race == "stale":
                current = meeting_processing_audit.get_or_create_pending_run(
                    meeting_id, trigger="retry"
                )
                assert current is not None
                assert current.dispatch_attempt_id != run.dispatch_attempt_id
            else:
                assert meeting_processing_audit.cancel_run(
                    meeting_id=meeting_id, run_id=run.id, owner_id=1
                )
            return {"late": True}

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="intelligence-raced-task"),
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights", return_value={}),
            patch("app.services.meeting_intelligence.intelligence_service.extract_structured_output", side_effect=make_attempt_inactive),
            pytest.raises(Ignore),
        ):
            stage_extract_intelligence.run(meeting_id)

        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            assert meeting.structured_output is None
            assert meeting.structured_output_status == "processing"
            assert meeting.status == ("cancelled" if race == "cancelled" else "processing")
            assert saved_run.status == ("cancelled" if race == "cancelled" else "running")
    finally:
        _delete_highlight_test_meeting(meeting_id)


def test_intelligence_extraction_exception_does_not_complete_before_failure_callback() -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        _seed_highlight_input(meeting_id)
        failure = RuntimeError("structured extraction failed")
        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=run.dispatch_attempt_id),
            patch("app.worker._task_request_id", return_value="intelligence-failure-task"),
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights", return_value={}),
            patch("app.services.meeting_intelligence.intelligence_service.extract_structured_output", side_effect=failure),
            pytest.raises(RuntimeError, match="structured extraction failed"),
        ):
            stage_extract_intelligence.run(meeting_id)

        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            assert meeting.status == "processing"
            assert meeting.structured_output_status == "processing"
            assert meeting.structured_output is None
            assert saved_run.status == "running"

        request = _FakeRequest(
            task_id="intelligence-failure-task",
            args=(meeting_id,),
            run_id=run.id,
            attempt_id=run.dispatch_attempt_id,
        )
        with (
            patch("app.worker.analytics.capture") as capture,
            patch("app.services.email.email_service.send_failure_email") as email,
            patch("app.worker.os.path.exists", return_value=False),
        ):
            on_stage_failure(request, failure, None)
        capture.assert_called_once()
        email.assert_called_once()
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            saved_run = session.get(MeetingProcessingRun, run.id)
            assert meeting.status == "failed"
            assert meeting.structured_output is None
            assert saved_run.status == "failed"
    finally:
        _delete_highlight_test_meeting(meeting_id)


@pytest.mark.parametrize("attempt_state", ["current", "stale", "cancelled"])
def test_intelligence_no_transcript_completion_is_attempt_aware(attempt_state: str) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        attempt_id = run.dispatch_attempt_id
        if attempt_state == "stale":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.dispatch_attempt_id != attempt_id
        elif attempt_state == "cancelled":
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )

        with (
            patch("app.worker._audit_run_id_from_task", return_value=run.id),
            patch("app.worker._audit_attempt_id_from_task", return_value=attempt_id),
            patch("app.worker._task_request_id", return_value="empty-transcript-task"),
            patch("app.services.meeting_intelligence.intelligence_service.extract_highlights") as extract_highlights,
            patch("app.services.meeting_intelligence.intelligence_service.extract_structured_output") as extract_structured,
        ):
            if attempt_state == "current":
                assert stage_extract_intelligence.run(meeting_id) == meeting_id
            else:
                with pytest.raises(Ignore):
                    stage_extract_intelligence.run(meeting_id)

        extract_highlights.assert_not_called()
        extract_structured.assert_not_called()
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            expected_status = {
                "current": "completed",
                "stale": "queued",
                "cancelled": "cancelled",
            }[attempt_state]
            assert meeting.status == expected_status
            assert meeting.structured_output_status == "pending"
            assert meeting.structured_output is None
    finally:
        _delete_meeting(meeting_id)


def test_youtube_artifact_keys_include_meeting_run_and_attempt() -> None:
    import app.worker as worker

    identities = [
        (4, 18, "attempt-one"),
        (4, 19, "attempt-one"),
        (4, 18, "attempt-two"),
        (5, 18, "attempt-one"),
    ]
    artifacts = [
        worker._youtube_attempt_artifacts(7, meeting_id, run_id, attempt_id, "video")
        for meeting_id, run_id, attempt_id in identities
    ]

    assert len({download_id for download_id, _ in artifacts}) == len(identities)
    assert len({file_key for _, file_key in artifacts}) == len(identities)
    assert "/meetings/youtube/4/runs/18/attempts/" in artifacts[0][1]


@pytest.mark.parametrize("identity_state", ["identityless", "stale", "cancelled", "duplicate"])
def test_youtube_stage_does_no_external_work_without_a_new_current_claim(
    monkeypatch, identity_state: str
) -> None:
    import app.worker as worker

    meeting_id = _make_meeting()
    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            meeting.source_url = "https://youtube.invalid/watch?v=test-video"
            session.add(meeting)
            session.commit()
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None
        run_id, attempt_id = run.id, run.dispatch_attempt_id
        if identity_state == "identityless":
            run_id = attempt_id = None
        elif identity_state == "stale":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.dispatch_attempt_id != attempt_id
        elif identity_state == "cancelled":
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )
        else:
            assert meeting_processing_audit.claim_youtube_download(
                run_id=run.id,
                attempt_id=attempt_id,
                meeting_id=meeting_id,
                task_id="first-youtube-delivery",
            ) is not None
        monkeypatch.setattr(worker, "_audit_run_id_from_task", lambda _task: run_id)
        monkeypatch.setattr(worker, "_audit_attempt_id_from_task", lambda _task: attempt_id)
        monkeypatch.setattr(worker, "_task_request_id", lambda _task: "duplicate-youtube-delivery")

        with (
            patch("app.services.youtube.extract_metadata") as metadata,
            patch("app.services.youtube.download_audio") as download,
            patch.object(worker.storage, "upload_file") as upload,
            patch.object(
                worker.meeting_processing_audit,
                "claim_youtube_download",
                wraps=meeting_processing_audit.claim_youtube_download,
            ) as claim,
            pytest.raises(Ignore),
        ):
            worker.stage_youtube_download.run(meeting_id)

        metadata.assert_not_called()
        download.assert_not_called()
        upload.assert_not_called()
        assert claim.call_count == (0 if identity_state == "identityless" else 1)
    finally:
        _delete_meeting(meeting_id)


def test_youtube_audio_promotes_and_hands_off_to_transcription(tmp_path) -> None:
    from contextlib import nullcontext
    from pathlib import Path

    import app.worker as worker
    from app.services.transcription.types import TranscriptionConfig

    meeting_id = _make_meeting()
    handoff_path = None
    metadata = {
        "id": "video-123",
        "title": "YouTube title",
        "duration": 120,
        "thumbnail": None,
        "channel": "Test channel",
    }

    def fake_download(_url, output_dir, download_id):
        path = os.path.join(output_dir, f"yt_{download_id}.mp3")
        with open(path, "wb") as audio:
            audio.write(b"mock audio bytes")
        return path

    class FakeProvider:
        provider_name = "openai-file"

        def transcribe(self, request, **_callbacks):
            assert request.source.local_path == Path(handoff_path)
            assert request.source.local_path.is_file()
            return SimpleNamespace(audio_duration_seconds=60, language="en")

    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            meeting.source_url = "https://youtube.invalid/watch?v=video-123"
            prior_file_path = meeting.file_path
            session.add(meeting)
            session.commit()
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        download_id, file_key = worker._youtube_attempt_artifacts(
            1, meeting_id, run.id, run.dispatch_attempt_id, metadata["id"]
        )
        provider = FakeProvider()

        with (
            patch.object(worker, "TEMP_DIR", str(tmp_path)),
            patch.object(
                worker, "_required_processing_identity",
                return_value=(run.id, run.dispatch_attempt_id),
            ),
            patch.object(worker, "_task_request_id", return_value="youtube-task"),
            patch.object(worker, "_update_sub_status_for_attempt", return_value=True),
            patch("app.services.youtube.extract_metadata", return_value=metadata),
            patch("app.services.youtube.download_audio", side_effect=fake_download),
            patch.object(worker.storage, "upload_file") as upload,
            patch.object(worker.storage, "delete_file") as delete,
            patch.object(worker.analytics, "capture"),
            patch.object(worker, "_configured_transcription_provider", return_value="openai-file"),
            patch.object(worker, "_probe_local_media", return_value=None),
            patch.object(worker, "_resolve_meeting_language_for_transcription", return_value=(None, [])),
            patch.object(worker, "build_config", return_value=TranscriptionConfig()),
            patch.object(worker, "_save_transcription_for_attempt", return_value=True),
            patch.object(worker, "get_provider", return_value=nullcontext(provider)),
            patch.object(worker, "notify"),
        ):
            handoff_path = worker._temp_path_for(
                meeting_id, file_key, run_id=run.id, attempt_id=run.dispatch_attempt_id
            )
            assert worker.stage_youtube_download.run(meeting_id) == meeting_id
            with Session(engine) as session:
                meeting = session.get(Meeting, meeting_id)
                assert meeting.file_path == file_key
            assert os.path.isfile(handoff_path)
            assert handoff_path.endswith(".mp3")
            upload.assert_called_once_with(b"mock audio bytes", file_key, "audio/mpeg")

            # The real transcription stage consumes the exact promoted-key path;
            # provider work is mocked and the file contains no real media.
            assert worker.stage_transcribe.run(meeting_id) == meeting_id
            assert not os.path.exists(handoff_path)
            delete.assert_not_called()
            assert file_key != prior_file_path
    finally:
        _delete_meeting(meeting_id)


@pytest.mark.parametrize("invalidated_by", ["stale", "cancelled"])
def test_youtube_audio_promotion_rejects_stale_or_cancelled_attempt(invalidated_by: str) -> None:
    import app.worker as worker

    meeting_id = _make_meeting()
    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            prior_file_path = meeting.file_path
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        old_attempt = run.dispatch_attempt_id
        _, attempt_file_key = worker._youtube_attempt_artifacts(
            1, meeting_id, run.id, old_attempt, "video"
        )
        assert meeting_processing_audit.claim_youtube_download(
            run_id=run.id,
            attempt_id=old_attempt,
            meeting_id=meeting_id,
            task_id="youtube-before-invalidation",
        ) is not None
        if invalidated_by == "stale":
            current = meeting_processing_audit.get_or_create_pending_run(
                meeting_id, trigger="retry"
            )
            assert current is not None and current.dispatch_attempt_id != old_attempt
        else:
            assert meeting_processing_audit.cancel_run(
                meeting_id=meeting_id, run_id=run.id, owner_id=1
            )

        assert not meeting_processing_audit.promote_youtube_audio(
            run_id=run.id,
            attempt_id=old_attempt,
            meeting_id=meeting_id,
            owner_id=1,
            file_key=attempt_file_key,
        )
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.file_path == prior_file_path
    finally:
        _delete_meeting(meeting_id)


def test_cancelled_youtube_upload_cleans_only_its_attempt_artifacts(tmp_path) -> None:
    import app.worker as worker

    meeting_id = _make_meeting()
    unrelated_local = tmp_path / "original-upload.mp3"
    unrelated_local.write_bytes(b"preserve original")
    metadata = {
        "id": "video-cleanup",
        "title": "YouTube title",
        "duration": 120,
        "thumbnail": None,
        "channel": "Test channel",
    }

    def fake_download(_url, output_dir, download_id):
        path = os.path.join(output_dir, f"yt_{download_id}.mp3")
        with open(path, "wb") as audio:
            audio.write(b"mock audio bytes")
        return path

    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            meeting.source_url = "https://youtube.invalid/watch?v=video-cleanup"
            prior_file_path = meeting.file_path
            session.add(meeting)
            session.commit()
        run = meeting_processing_audit.get_or_create_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        download_id, file_key = worker._youtube_attempt_artifacts(
            1, meeting_id, run.id, run.dispatch_attempt_id, metadata["id"]
        )
        work_dir = None
        checks = 0

        def cancel_after_upload(*_args):
            nonlocal checks
            checks += 1
            if checks == 6:
                assert meeting_processing_audit.cancel_run(
                    meeting_id=meeting_id, run_id=run.id, owner_id=1
                )
                return False
            return True

        with (
            patch.object(worker, "TEMP_DIR", str(tmp_path)),
            patch.object(
                worker, "_required_processing_identity",
                return_value=(run.id, run.dispatch_attempt_id),
            ),
            patch.object(worker, "_task_request_id", return_value="youtube-cancelled-task"),
            patch.object(worker, "_update_sub_status_for_attempt", side_effect=cancel_after_upload),
            patch("app.services.youtube.extract_metadata", return_value=metadata),
            patch("app.services.youtube.download_audio", side_effect=fake_download),
            patch.object(worker.storage, "upload_file"),
            patch.object(worker.storage, "delete_file") as delete,
            pytest.raises(Ignore),
        ):
            work_dir = worker._youtube_attempt_temp_dir(
                meeting_id, run.id, run.dispatch_attempt_id
            )
            worker.stage_youtube_download.run(meeting_id)

        delete.assert_called_once_with(file_key)
        assert file_key != prior_file_path
        assert not os.path.exists(work_dir)
        assert unrelated_local.read_bytes() == b"preserve original"
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            assert meeting.file_path == prior_file_path
    finally:
        _delete_meeting(meeting_id)
