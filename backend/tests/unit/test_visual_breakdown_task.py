# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Tests for stage_visual_breakdown Celery task orchestration."""
from unittest.mock import MagicMock, patch
from hashlib import sha256
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore
from sqlmodel import Session, delete, select

from app.db.engine import engine
from app.models import Meeting, User, VisualSegment
from app.models.processing_audit import MeetingProcessingEvent, MeetingProcessingRun
from app.services.meeting_processing_audit import meeting_processing_audit
from app.services.visual_breakdown.types import VisualSegmentResponse, VisionWorkerResult
import app.worker as worker
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
            obj.current_processing_run_id = None
            session.add(obj)
            session.flush()
        session.exec(delete(MeetingProcessingEvent).where(MeetingProcessingEvent.meeting_id == mid))
        session.exec(delete(MeetingProcessingRun).where(MeetingProcessingRun.meeting_id == mid))
        if obj is not None:
            session.delete(obj)
        session.commit()


@pytest.fixture
def pipeline_identity(meeting_with_file):
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        meeting.status = "processing"
        session.add(meeting)
        session.commit()

    run = meeting_processing_audit.create_run(meeting_with_file, trigger="pipeline")
    assert run is not None and run.id is not None and run.dispatch_attempt_id
    with Session(engine) as session:
        persisted_run = session.get(MeetingProcessingRun, run.id)
        persisted_run.status = "running"
        session.add(persisted_run)
        session.commit()
    return run.id, run.dispatch_attempt_id


@pytest.fixture
def visual_run_identity(meeting_with_file):
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        meeting.status = "completed"
        meeting.summary_text = "Keep summary"
        meeting.transcript_text = "Keep transcript"
        meeting.visual_breakdown_status = "completed"
        meeting.visual_raw_output_s3_key = "users/1/meetings/prior/raw_output.json"
        session.add(meeting)
        session.commit()

    admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_with_file)
    assert admitted is not None
    run, _epoch = admitted
    assert run.id is not None and run.dispatch_attempt_id
    return run.id, run.dispatch_attempt_id


def _task_headers(
    run_id: int | None,
    attempt_id: str | None,
    *,
    terminal_success: bool = False,
):
    headers = {}
    if run_id is not None:
        headers["meeting_processing_run_id"] = str(run_id)
    if attempt_id is not None:
        headers["dispatch_attempt_id"] = attempt_id
    if terminal_success:
        headers["meeting_processing_terminal_success"] = "true"
    return patch(
        "app.worker._get_audit_header",
        side_effect=lambda _task, key: headers.get(key),
    )


def _completed_worker_result(request: dict) -> VisionWorkerResult:
    namespace = (
        f"users/{request['owner_id']}/meetings/{request['meeting_id']}"
        f"/visual/epoch-{request['visual_epoch']}"
    )
    run_id = request.get("processing_run_id")
    attempt_id = request.get("dispatch_attempt_id")
    if run_id is not None and attempt_id:
        attempt_hash = sha256(str(attempt_id).encode("utf-8")).hexdigest()
        namespace += f"/run-{run_id}/attempt-{attempt_hash}"
    else:
        namespace += f"/standalone-{'a' * 32}"
    return VisionWorkerResult(
        status="completed",
        segments=[
            VisualSegmentResponse(
                id="a" * 32, sequence=0, start_time=0.0, end_time=5.0,
                screenshot_s3_key=f"{namespace}/{'a' * 32}.jpg",
                caption="Login page", confidence=0.9,
            ),
            VisualSegmentResponse(
                id="b" * 32, sequence=1, start_time=5.0, end_time=10.0,
                screenshot_s3_key=f"{namespace}/{'b' * 32}.jpg",
                caption="Dashboard", confidence=0.85,
            ),
        ],
        raw_output_s3_key=f"{namespace}/raw_output.json",
        model="gpt-4o-mini",
        params={"fps": 2},
        stage_metrics={
            "extract_frames": {"duration_ms": 1000, "frame_count": 40},
            "compute_signals": {"duration_ms": 500, "candidate_count": 6},
        },
    )


def test_happy_path_completes_meeting_and_persists_segments(meeting_with_file):
    meeting_id = meeting_with_file

    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture") as mock_capture,
        patch("app.worker.notify") as mock_notify,
    ):
        mock_client = MagicMock()
        mock_client.submit_and_wait.side_effect = lambda request, **kwargs: _completed_worker_result(request)
        mock_cls.return_value = mock_client

        out = stage_visual_breakdown(meeting_id)

    assert out == meeting_id
    mock_cls.assert_called_once_with(
        storage_provider=worker.get_storage_for_provider("minio")
    )
    request = mock_cls.return_value.submit_and_wait.call_args.args[0]
    assert request["file_path"] == "users/1/meetings/X/audio.mp4"
    assert "video_url" not in request

    # Meeting fields updated
    with Session(engine) as session:
        m = session.get(Meeting, meeting_id)
        assert m.visual_breakdown_status == "completed"
        assert m.visual_breakdown_completed_at is not None
        assert "/visual/epoch-" in m.visual_raw_output_s3_key
        assert m.visual_raw_output_s3_key.endswith("/raw_output.json")
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
    assert "processing_run_id" not in mock_notify.call_args.kwargs
    assert "dispatch_attempt_id" not in mock_notify.call_args.kwargs


def test_pipeline_visual_breakdown_uses_current_run_and_attempt(meeting_with_file, pipeline_identity):
    meeting_id = meeting_with_file
    run_id, attempt_id = pipeline_identity
    original_begin = worker.meeting_service.begin_visual_breakdown
    original_progress = worker.meeting_service.update_visual_breakdown_progress
    original_increment = worker.meeting_service.increment_visual_breakdown_attempt
    original_finalize = worker.meeting_service.finalize_visual_breakdown
    returned_results = []
    begin_results = []

    def capture_begin_result(*args, **kwargs):
        result = original_begin(*args, **kwargs)
        begin_results.append(result)
        return result

    def return_result(request, **_kwargs):
        result = _completed_worker_result(request)
        returned_results.append(result)
        return result

    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.settings.VISION_ENABLED", True),
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.worker.notify") as mock_notify,
        patch.object(
            worker.meeting_service,
            "begin_visual_breakdown",
            side_effect=capture_begin_result,
        ) as begin,
        patch.object(worker.meeting_service, "update_visual_breakdown_progress", wraps=original_progress) as progress,
        patch.object(worker.meeting_service, "increment_visual_breakdown_attempt", wraps=original_increment) as increment,
        patch.object(worker.meeting_service, "finalize_visual_breakdown", wraps=original_finalize) as finalize,
    ):
        mock_cls.return_value.submit_and_wait.side_effect = return_result
        assert stage_optional_visual_breakdown(meeting_id) == meeting_id

    epoch = begin_results[0][0]
    begin.assert_called_once_with(
        meeting_id,
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
    )
    increment.assert_called_once_with(
        meeting_id,
        epoch,
        processing_run_id=run_id,
        dispatch_attempt_id=attempt_id,
    )
    assert any(
        call.args == (meeting_id, epoch)
        and call.kwargs
        == {
            "processing_run_id": run_id,
            "dispatch_attempt_id": attempt_id,
            "sub_status": "analyzing_video",
        }
        for call in progress.call_args_list
    )
    finalize.assert_called_once()
    assert finalize.call_args.args == (meeting_id, epoch)
    assert finalize.call_args.kwargs["processing_run_id"] == run_id
    assert finalize.call_args.kwargs["dispatch_attempt_id"] == attempt_id
    with Session(engine) as session:
        assert session.get(Meeting, meeting_id).visual_raw_output_s3_key == returned_results[0].raw_output_s3_key
    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["processing_run_id"] == run_id
    assert mock_notify.call_args.kwargs["dispatch_attempt_id"] == attempt_id


def test_manual_visual_run_completes_audit_without_reopening_meeting(
    meeting_with_file, visual_run_identity
):
    meeting_id = meeting_with_file
    run_id, attempt_id = visual_run_identity
    headers = {
        "meeting_processing_run_id": str(run_id),
        "dispatch_attempt_id": attempt_id,
        "meeting_processing_terminal_success": "true",
    }
    sender = SimpleNamespace(
        name="stage_visual_breakdown",
        request=SimpleNamespace(headers=headers, args=(meeting_id,), id="visual-task"),
    )

    with (
        _task_headers(run_id, attempt_id, terminal_success=True),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.worker.notify"),
    ):
        mock_cls.return_value.submit_and_wait.side_effect = (
            lambda request, **kwargs: _completed_worker_result(request)
        )
        assert stage_visual_breakdown(meeting_id) == meeting_id
        worker._record_audit_stage_succeeded(sender=sender, result=meeting_id)

    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        run = session.get(MeetingProcessingRun, run_id)
        assert meeting.status == "completed"
        assert meeting.sub_status is None
        assert meeting.summary_text == "Keep summary"
        assert meeting.transcript_text == "Keep transcript"
        assert meeting.file_path == "users/1/meetings/X/audio.mp4"
        assert meeting.visual_breakdown_status == "completed"
        assert meeting.visual_raw_output_s3_key.endswith("/raw_output.json")
        assert run.status == "completed"


def test_manual_visual_provider_failure_does_not_replace_prior_outputs(
    meeting_with_file, visual_run_identity
):
    run_id, attempt_id = visual_run_identity
    failed_result = VisionWorkerResult(
        status="failed", segments=[], model="gpt-4o-mini", error="provider failed"
    )
    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.DirectVisionService") as mock_cls,
        patch.object(worker.meeting_service, "finalize_visual_breakdown") as finalize,
    ):
        mock_cls.return_value.submit_and_wait.return_value = failed_result
        with pytest.raises(RuntimeError, match="provider failed"):
            stage_visual_breakdown(meeting_with_file)

    finalize.assert_not_called()
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        assert meeting.status == "completed"
        assert meeting.summary_text == "Keep summary"
        assert meeting.transcript_text == "Keep transcript"
        assert meeting.visual_raw_output_s3_key == "users/1/meetings/prior/raw_output.json"


def test_completed_meeting_rejects_non_visual_processing_run(
    meeting_with_file, pipeline_identity
):
    run_id, attempt_id = pipeline_identity
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        meeting.status = "completed"
        session.add(meeting)
        session.commit()

    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        with pytest.raises(Ignore):
            stage_visual_breakdown(meeting_with_file)

    mock_cls.assert_not_called()

    assert meeting_processing_audit.run_failed(
        run_id, attempt_id=attempt_id, error="finish pipeline fixture"
    )
    admitted = meeting_processing_audit.create_visual_breakdown_run(meeting_with_file)
    assert admitted is not None
    visual_run, _epoch = admitted
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_with_file)
        meeting.visual_breakdown_status = "processing"
        session.add(meeting)
        session.commit()
    with (
        _task_headers(visual_run.id, visual_run.dispatch_attempt_id),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        with pytest.raises(Ignore):
            stage_visual_breakdown(meeting_with_file)
    mock_cls.assert_not_called()


def test_pipeline_visual_result_is_discarded_when_cancelled_during_provider(
    meeting_with_file, pipeline_identity
):
    meeting_id = meeting_with_file
    run_id, attempt_id = pipeline_identity
    original_media = "users/1/meetings/X/audio.mp4"
    prior_success = (
        f"users/1/meetings/{meeting_id}/visual/epoch-0/standalone-{'b' * 32}/raw_output.json"
    )
    objects = {original_media, prior_success}

    returned_results = []

    def cancel_then_return_result(request, **_kwargs):
        assert meeting_processing_audit.cancel_run(
            meeting_id=meeting_id,
            run_id=run_id,
            owner_id=1,
        )
        result = _completed_worker_result(request)
        returned_results.append(result)
        objects.add(result.raw_output_s3_key)
        objects.update(segment.screenshot_s3_key for segment in result.segments)
        return result

    deleted = []

    def delete_object(key):
        deleted.append(key)
        objects.discard(key)

    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.settings.VISION_ENABLED", True),
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.worker.notify") as mock_notify,
        patch("app.worker.storage.delete_file", side_effect=delete_object),
    ):
        mock_cls.return_value.submit_and_wait.side_effect = cancel_then_return_result
        with pytest.raises(Ignore):
            stage_optional_visual_breakdown(meeting_id)

    mock_notify.assert_not_called()
    current_keys = {
        returned_results[0].raw_output_s3_key,
        *(segment.screenshot_s3_key for segment in returned_results[0].segments),
    }
    assert set(deleted) == current_keys
    assert objects == {original_media, prior_success}
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        assert meeting.status == "cancelled"
        assert meeting.visual_breakdown_status == "processing"
        assert session.exec(
            select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
        ).first() is None


def test_rejected_visual_finalization_deletes_only_current_attempt_artifacts(
    meeting_with_file, pipeline_identity
):
    meeting_id = meeting_with_file
    run_id, attempt_id = pipeline_identity
    original_media = "users/1/meetings/X/audio.mp4"
    prior_success = (
        f"users/1/meetings/{meeting_id}/visual/epoch-0/standalone-{'b' * 32}/raw_output.json"
    )
    objects = {original_media, prior_success}
    deleted = []
    returned_results = []

    def return_result(request, **_kwargs):
        result = _completed_worker_result(request)
        returned_results.append(result)
        objects.add(result.raw_output_s3_key)
        objects.update(segment.screenshot_s3_key for segment in result.segments)
        return result

    def delete_object(key):
        deleted.append(key)
        objects.discard(key)

    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.settings.VISION_ENABLED", True),
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture"),
        patch("app.worker.notify"),
        patch.object(
            worker.meeting_service,
            "finalize_visual_breakdown",
            return_value={"applied": False},
        ) as finalize,
        patch("app.worker.storage.delete_file", side_effect=delete_object),
    ):
        mock_cls.return_value.submit_and_wait.side_effect = return_result
        with pytest.raises(Ignore):
            stage_optional_visual_breakdown(meeting_id)

    current_keys = {
        returned_results[0].raw_output_s3_key,
        *(segment.screenshot_s3_key for segment in returned_results[0].segments),
    }
    assert set(deleted) == current_keys
    assert objects == {original_media, prior_success}
    finalize.assert_called_once()
    assert finalize.call_args.kwargs["raw_output_s3_key"] in current_keys


def _visual_cleanup_result(namespace: str) -> SimpleNamespace:
    return SimpleNamespace(
        raw_output_s3_key=f"{namespace}/raw_output.json",
        segments=[
            SimpleNamespace(screenshot_s3_key=f"{namespace}/{'a' * 32}.jpg"),
            SimpleNamespace(screenshot_s3_key=f"{namespace}/{'b' * 32}.jpg"),
        ],
    )


def test_visual_cleanup_deletes_only_valid_standalone_result_artifacts():
    namespace = "users/1/meetings/2/visual/epoch-123/standalone-" + "c" * 32
    result = _visual_cleanup_result(namespace)
    expected_keys = {
        result.raw_output_s3_key,
        *(segment.screenshot_s3_key for segment in result.segments),
    }
    prior_keys = {
        "users/1/meetings/2/audio.mp4",
        "users/1/meetings/2/visual/epoch-122/standalone-" + "b" * 32 + "/raw_output.json",
    }

    with patch("app.worker.storage.delete_file") as delete_file:
        worker._cleanup_visual_result_artifacts(
            result,
            owner_id=1,
            meeting_id=2,
            visual_epoch=123,
            processing_run_id=None,
            dispatch_attempt_id=None,
        )

    deleted_keys = {call.args[0] for call in delete_file.call_args_list}
    assert delete_file.call_count == len(expected_keys)
    assert deleted_keys == expected_keys
    assert deleted_keys.isdisjoint(prior_keys)


@pytest.mark.parametrize(
    ("owner_id", "owner_component"),
    [
        pytest.param(True, "True", id="bool"),
        pytest.param(0, "unknown", id="zero"),
        pytest.param(-1, "-1", id="negative"),
        pytest.param("1", "1", id="string"),
        pytest.param(None, "unknown", id="missing"),
    ],
)
def test_visual_cleanup_rejects_malformed_owner_id_without_deleting(
    owner_id, owner_component
):
    namespace = (
        f"users/{owner_component}/meetings/2/visual/epoch-123/standalone-{'c' * 32}"
    )
    with patch("app.worker.storage.delete_file") as delete_file:
        worker._cleanup_visual_result_artifacts(
            _visual_cleanup_result(namespace),
            owner_id=owner_id,
            meeting_id=2,
            visual_epoch=123,
            processing_run_id=None,
            dispatch_attempt_id=None,
        )

    delete_file.assert_not_called()


@pytest.mark.parametrize(
    ("meeting_id", "meeting_component"),
    [
        pytest.param(True, "True", id="bool"),
        pytest.param(0, "unknown", id="zero"),
        pytest.param(-1, "-1", id="negative"),
        pytest.param("2", "2", id="string"),
        pytest.param(None, "unknown", id="missing"),
    ],
)
def test_visual_cleanup_rejects_malformed_meeting_id_without_deleting(
    meeting_id, meeting_component
):
    namespace = (
        f"users/1/meetings/{meeting_component}/visual/epoch-123/"
        f"standalone-{'c' * 32}"
    )
    with patch("app.worker.storage.delete_file") as delete_file:
        worker._cleanup_visual_result_artifacts(
            _visual_cleanup_result(namespace),
            owner_id=1,
            meeting_id=meeting_id,
            visual_epoch=123,
            processing_run_id=None,
            dispatch_attempt_id=None,
        )

    delete_file.assert_not_called()


@pytest.mark.parametrize(
    ("processing_run_id", "run_component"),
    [
        pytest.param(True, "True", id="bool"),
        pytest.param(0, "0", id="zero"),
        pytest.param(-1, "-1", id="negative"),
        pytest.param("7", "7", id="string"),
        pytest.param("../outside", "../outside", id="path-like-string"),
    ],
)
def test_visual_cleanup_rejects_malformed_run_id_without_deleting(
    processing_run_id, run_component
):
    attempt_id = "cleanup-attempt"
    attempt_hash = sha256(attempt_id.encode("utf-8")).hexdigest()
    namespace = (
        f"users/1/meetings/2/visual/epoch-123/run-{run_component}"
        f"/attempt-{attempt_hash}"
    )
    with patch("app.worker.storage.delete_file") as delete_file:
        worker._cleanup_visual_result_artifacts(
            _visual_cleanup_result(namespace),
            owner_id=1,
            meeting_id=2,
            visual_epoch=123,
            processing_run_id=processing_run_id,
            dispatch_attempt_id=attempt_id,
        )

    delete_file.assert_not_called()


@pytest.mark.parametrize(
    ("processing_run_id", "dispatch_attempt_id"),
    [
        pytest.param(7, None, id="missing-attempt"),
        pytest.param(None, "cleanup-attempt", id="missing-run"),
        pytest.param(7, "", id="empty-attempt"),
        pytest.param(7, 123, id="non-string-attempt"),
        pytest.param(7, True, id="boolean-attempt"),
    ],
)
def test_visual_cleanup_rejects_partial_or_malformed_pipeline_identity_without_deleting(
    processing_run_id, dispatch_attempt_id
):
    attempt_hash = sha256(str(dispatch_attempt_id).encode("utf-8")).hexdigest()
    namespace = (
        f"users/1/meetings/2/visual/epoch-123/run-{processing_run_id}"
        f"/attempt-{attempt_hash}"
    )
    with patch("app.worker.storage.delete_file") as delete_file:
        worker._cleanup_visual_result_artifacts(
            _visual_cleanup_result(namespace),
            owner_id=1,
            meeting_id=2,
            visual_epoch=123,
            processing_run_id=processing_run_id,
            dispatch_attempt_id=dispatch_attempt_id,
        )

    delete_file.assert_not_called()


def test_stale_pipeline_identity_skips_visual_provider(meeting_with_file, pipeline_identity):
    meeting_id = meeting_with_file
    run_id, attempt_id = pipeline_identity
    with (
        _task_headers(run_id + 1000, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        with pytest.raises(Ignore):
            stage_optional_visual_breakdown(meeting_id)

    mock_cls.assert_not_called()


def test_optional_pipeline_without_headers_fails_closed(meeting_with_file):
    with (
        _task_headers(None, None),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        with pytest.raises(Ignore):
            stage_optional_visual_breakdown(meeting_with_file)

    mock_cls.assert_not_called()


@pytest.mark.parametrize("missing_header", ["meeting_processing_run_id", "dispatch_attempt_id"])
def test_partial_pipeline_identity_fails_closed_before_visual_provider(
    meeting_with_file, pipeline_identity, missing_header
):
    meeting_id = meeting_with_file
    run_id, attempt_id = pipeline_identity
    headers = {
        "meeting_processing_run_id": str(run_id),
        "dispatch_attempt_id": attempt_id,
    }
    headers.pop(missing_header)
    with (
        patch(
            "app.worker._get_audit_header",
            side_effect=lambda _task, key: headers.get(key),
        ),
        patch("app.worker.DirectVisionService") as mock_cls,
    ):
        with pytest.raises(Ignore):
            stage_optional_visual_breakdown(meeting_id)

    mock_cls.assert_not_called()


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

    direct_service.assert_called_once_with(
        storage_provider=worker.get_storage_for_provider("minio")
    )
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


def test_optional_stage_disabled_skips_with_stable_id(meeting_with_file, pipeline_identity):
    run_id, attempt_id = pipeline_identity
    with (
        _task_headers(run_id, attempt_id),
        patch("app.worker._acquire_visual_lease", return_value=True),
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
    with (
        patch("app.worker.DirectVisionService") as mock_cls,
        patch("app.services.analytics.capture") as mock_capture,
        patch("app.worker.notify") as mock_notify,
    ):
        mock_cls.return_value.submit_and_wait.side_effect = (
            lambda request, **kwargs: _completed_worker_result(request)
        )
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


def test_summary_preserves_legacy_transcript_when_segments_are_absent(
    meeting_with_file, pipeline_identity, db: Session
):
    run_id, attempt_id = pipeline_identity
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
        _task_headers(run_id, attempt_id),
    ):
        out = stage_summarize(meeting_with_file)

    assert out == meeting_with_file
    assert summarize.call_args.kwargs["context"] is None


def test_summary_uses_visual_context_without_transcript(
    meeting_with_file, pipeline_identity, db: Session
):
    run_id, attempt_id = pipeline_identity
    db.add(
        VisualSegment(
            meeting_id=meeting_with_file,
            start_time=0.0,
            end_time=5.0,
            screenshot_s3_key="users/1/meetings/X/visual/silent.jpg",
            caption="A dashboard with a warning banner",
            sequence=0,
            confidence=0.9,
        )
    )
    db.commit()

    with (
        patch("app.worker.style_service.get_style_examples", return_value=[]),
        patch("app.worker.template_service.get_active_default", return_value=None),
        patch("app.worker.get_completion_client", return_value=MagicMock()),
        patch("app.worker.summarize_transcript", return_value="Visual-only summary") as summarize,
        patch("app.services.ai_agent.infer_title", return_value=None),
        patch("app.worker.analytics.capture"),
        patch("app.worker.notify"),
        patch("app.services.email.email_service.send_summary_email"),
        _task_headers(run_id, attempt_id),
    ):
        out = stage_summarize(meeting_with_file)

    assert out == meeting_with_file
    assert summarize.call_args.args[0] == ""
    context = summarize.call_args.kwargs["context"]
    assert context is not None
    assert any(
        item.source == "visual"
        for chunk in context.chunks
        for item in chunk.items
    )


def test_summary_and_intelligence_wait_for_embedding_before_completion(
    meeting_with_file, pipeline_identity, db: Session
):
    run_id, attempt_id = pipeline_identity
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
        _task_headers(run_id, attempt_id),
    ):
        stage_summarize(meeting_with_file)

    with Session(engine) as session:
        summarized = session.get(Meeting, meeting_with_file)
        assert summarized.status == "processing"
        assert summarized.sub_status == "summarizing"

    intelligence = MagicMock()
    intelligence.extract_highlights.return_value = []
    intelligence.extract_structured_output.return_value = {"topics": []}
    with (
        patch("app.services.meeting_intelligence.intelligence_service", intelligence),
        _task_headers(run_id, attempt_id),
    ):
        stage_extract_intelligence(meeting_with_file)

    with Session(engine) as session:
        waiting_for_embedding = session.get(Meeting, meeting_with_file)
        assert waiting_for_embedding.status == "processing"
        assert waiting_for_embedding.sub_status == "indexing_embeddings"
        assert waiting_for_embedding.structured_output_status == "completed"

    upserts = []
    embedding_provider = SimpleNamespace(
        embed=lambda texts: [[0.1, 0.2, 0.3] for _ in texts]
    )
    vector_store = SimpleNamespace(
        delete_by_filter=lambda **kwargs: None,
        upsert_points=lambda points: upserts.extend(points),
    )
    with (
        patch.object(worker.settings, "INDEXING_ENABLED", True),
        patch("app.worker.get_embedding_provider", return_value=embedding_provider),
        patch("app.worker.get_vector_store", return_value=vector_store),
        patch("app.worker.analytics.capture"),
        _task_headers(run_id, attempt_id),
    ):
        worker.stage_embedding(meeting_with_file)

    assert upserts
    with Session(engine) as session:
        completed = session.get(Meeting, meeting_with_file)
        assert completed.status == "completed"
        assert completed.sub_status is None
        assert completed.structured_output_status == "completed"
