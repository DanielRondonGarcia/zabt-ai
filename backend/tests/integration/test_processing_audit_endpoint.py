# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Integration tests for owner-scoped meeting processing audit endpoint."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace
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


def test_reprocess_creates_reprocess_run_and_audit_get_is_ordered(client, normal_user_token_headers) -> None:
    meeting_id = _make_meeting(status="failed")
    try:
        with patch("app.api.v1.endpoints.meetings.dispatch_transcription_job") as mock_dispatch:
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/reprocess",
                headers=normal_user_token_headers,
            )
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

        audit_response = client.get(
            f"/api/v1/meetings/{meeting_id}/processing-audit",
            headers=normal_user_token_headers,
        )
        assert audit_response.status_code == 200, audit_response.text
        body = audit_response.json()
        assert body["meeting_id"] == meeting_id
        assert [run["id"] for run in body["runs"]][:2] == [second_run.id, first_run.id]
        first_body = next(run for run in body["runs"] if run["id"] == first_run.id)
        assert [event["event_type"] for event in first_body["events"]] == ["started", "completed"]
    finally:
        _delete_meeting(meeting_id)


def test_audit_get_404_for_other_owner_and_missing_meeting(client, normal_user_token_headers) -> None:
    other_meeting_id = _make_meeting(owner_id=42)
    try:
        other_response = client.get(
            f"/api/v1/meetings/{other_meeting_id}/processing-audit",
            headers=normal_user_token_headers,
        )
        assert other_response.status_code == 404

        missing_response = client.get(
            "/api/v1/meetings/999999999/processing-audit",
            headers=normal_user_token_headers,
        )
        assert missing_response.status_code == 404
    finally:
        _delete_meeting(other_meeting_id)


def test_audit_get_returns_sanitized_errors_without_raw_url_or_credentials(
    client, normal_user_token_headers
) -> None:
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

        response = client.get(
            f"/api/v1/meetings/{meeting_id}/processing-audit",
            headers=normal_user_token_headers,
        )
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


def test_owner_can_cancel_exact_current_processing_run(client, normal_user_token_headers) -> None:
    meeting_id = _make_meeting()
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="resummarize")
        assert run is not None and run.dispatch_attempt_id

        response = client.post(
            f"/api/v1/meetings/{meeting_id}/processing-runs/{run.id}/cancel",
            headers=normal_user_token_headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == run.id
        assert body["status"] == "cancelled"
        assert "committed work may remain" in body["message"].lower()
        assert "provider call may finish" in body["message"].lower()
        with Session(engine) as session:
            assert session.get(MeetingProcessingRun, run.id).status == "cancelled"
            assert session.get(Meeting, meeting_id).status == "cancelled"
    finally:
        _delete_meeting(meeting_id)


def test_foreign_owner_cannot_cancel_meeting_run(client, normal_user_token_headers) -> None:
    meeting_id = _make_meeting(owner_id=42)
    try:
        run = meeting_processing_audit.create_run(meeting_id, trigger="resummarize")
        assert run is not None

        response = client.post(
            f"/api/v1/meetings/{meeting_id}/processing-runs/{run.id}/cancel",
            headers=normal_user_token_headers,
        )

        assert response.status_code == 404
        with Session(engine) as session:
            assert session.get(MeetingProcessingRun, run.id).status == "queued"
            assert session.get(Meeting, meeting_id).status == "completed"
    finally:
        _delete_meeting(meeting_id)


def test_stale_run_cannot_cancel_newer_current_generation(client, normal_user_token_headers) -> None:
    meeting_id = _make_meeting()
    try:
        stale = meeting_processing_audit.create_run(meeting_id, trigger="resummarize")
        assert stale is not None and stale.dispatch_attempt_id
        meeting_processing_audit.run_failed(
            stale.id, attempt_id=stale.dispatch_attempt_id, error="superseded"
        )
        current = meeting_processing_audit.create_run(meeting_id, trigger="reprocess")
        assert current is not None and current.id != stale.id

        response = client.post(
            f"/api/v1/meetings/{meeting_id}/processing-runs/{stale.id}/cancel",
            headers=normal_user_token_headers,
        )

        assert response.status_code == 409
        with Session(engine) as session:
            assert session.get(MeetingProcessingRun, stale.id).status == "failed"
            assert session.get(MeetingProcessingRun, current.id).status == "queued"
            assert session.get(Meeting, meeting_id).status == "completed"
    finally:
        _delete_meeting(meeting_id)


def test_resummarize_creates_run_returns_id_and_dispatches_exact_headers(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    try:
        with (
            patch("celery.chain") as chain,
            patch("app.worker.stage_summarize") as summarize,
            patch("app.worker.stage_extract_intelligence") as intelligence,
        ):
            chain.return_value.apply_async.return_value.id = "resummarize-root-task"
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/summarize",
                headers=normal_user_token_headers,
            )

        assert response.status_code == 202, response.text
        run = meeting_processing_audit.get_pending_run(meeting_id)
        assert run is not None and run.dispatch_attempt_id
        assert run.trigger == "resummarize"
        assert response.json()["run_id"] == run.id
        assert run.root_task_id == "resummarize-root-task"
        assert response.json()["root_task_recorded"] is True

        summarize_headers = summarize.s.return_value.set.call_args.kwargs["headers"]
        intelligence_headers = intelligence.s.return_value.set.call_args.kwargs["headers"]
        expected_identity = {
            "meeting_id": str(meeting_id),
            "meeting_processing_run_id": str(run.id),
            "dispatch_attempt_id": run.dispatch_attempt_id,
        }
        assert summarize_headers == {
            **expected_identity,
            "stage": "stage_summarize",
        }
        assert intelligence_headers == {
            **expected_identity,
            "stage": "stage_extract_intelligence",
        }
        chain.assert_called_once_with(
            summarize.s.return_value.set.return_value,
            intelligence.s.return_value.set.return_value,
        )
        chain.return_value.apply_async.assert_called_once_with(args=[meeting_id])
    finally:
        _delete_meeting(meeting_id)


def test_concurrent_resummarize_requests_admit_only_one_run_and_dispatch(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    dispatch_entered = Event()
    release_dispatch = Event()

    def blocked_enqueue(*_args, **_kwargs):
        dispatch_entered.set()
        if not release_dispatch.wait(timeout=10):
            raise TimeoutError("test did not release the first dispatch")
        return SimpleNamespace(id="single-resummarize-root")

    try:
        with patch("celery.chain") as chain:
            chain.return_value.apply_async.side_effect = blocked_enqueue
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(
                    client.post,
                    f"/api/v1/meetings/{meeting_id}/summarize",
                    headers=normal_user_token_headers,
                )
                try:
                    assert dispatch_entered.wait(timeout=10)
                    second = pool.submit(
                        client.post,
                        f"/api/v1/meetings/{meeting_id}/summarize",
                        headers=normal_user_token_headers,
                    )
                    second_response = second.result(timeout=10)
                finally:
                    release_dispatch.set()
                first_response = first.result(timeout=10)

        assert first_response.status_code == 202, first_response.text
        assert second_response.status_code == 409, second_response.text
        chain.assert_called_once()
        chain.return_value.apply_async.assert_called_once()
        with Session(engine) as session:
            runs = session.exec(
                select(MeetingProcessingRun).where(
                    MeetingProcessingRun.meeting_id == meeting_id
                )
            ).all()
            meeting = session.get(Meeting, meeting_id)
            assert len(runs) == 1
            assert runs[0].trigger == "resummarize"
            assert runs[0].root_task_id == "single-resummarize-root"
            assert runs[0].status == "queued"
            assert meeting.status == "processing"
            assert meeting.sub_status == "summarizing"
    finally:
        release_dispatch.set()
        _delete_meeting(meeting_id)


def test_resummarize_dispatch_failure_fails_exact_run_and_restores_meeting(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    try:
        with (
            patch("celery.chain") as chain,
            patch.object(
                meeting_processing_audit,
                "run_failed",
                wraps=meeting_processing_audit.run_failed,
            ) as fail_run,
        ):
            chain.return_value.apply_async.side_effect = RuntimeError("broker unavailable")
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/summarize",
                headers=normal_user_token_headers,
            )

        assert response.status_code == 503
        with Session(engine) as session:
            run = session.exec(
                select(MeetingProcessingRun).where(
                    MeetingProcessingRun.meeting_id == meeting_id
                )
            ).one()
            meeting = session.get(Meeting, meeting_id)
            assert run.trigger == "resummarize"
            assert run.status == "failed"
            assert meeting.status == "completed"
            assert meeting.sub_status is None
        fail_run.assert_called_once_with(
            run.id,
            attempt_id=run.dispatch_attempt_id,
            error="Could not enqueue standalone re-summarization",
        )
    finally:
        _delete_meeting(meeting_id)


def test_resummarize_surfaces_run_failure_audit_persistence_failure(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    try:
        with (
            patch("celery.chain") as chain,
            patch.object(meeting_processing_audit, "run_failed", return_value=False) as fail_run,
        ):
            chain.return_value.apply_async.side_effect = RuntimeError("broker unavailable")
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/summarize",
                headers=normal_user_token_headers,
            )

        assert response.status_code == 503
        assert "could not be confirmed in the audit" in response.json()["detail"]
        with Session(engine) as session:
            run = session.exec(
                select(MeetingProcessingRun).where(
                    MeetingProcessingRun.meeting_id == meeting_id
                )
            ).one()
            assert run.status == "queued"
            assert session.get(Meeting, meeting_id).status == "completed"
        fail_run.assert_called_once_with(
            run.id,
            attempt_id=run.dispatch_attempt_id,
            error="Could not enqueue standalone re-summarization",
        )
    finally:
        _delete_meeting(meeting_id)


def test_resummarize_root_task_persistence_failure_is_reported_without_stopping_worker(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    try:
        with (
            patch("celery.chain") as chain,
            patch.object(
                meeting_processing_audit, "set_root_task", return_value=False
            ) as set_root_task,
        ):
            chain.return_value.apply_async.return_value.id = "already-queued-root"
            response = client.post(
                f"/api/v1/meetings/{meeting_id}/summarize",
                headers=normal_user_token_headers,
            )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["root_task_id"] == "already-queued-root"
        assert body["root_task_recorded"] is False
        assert "task was queued" in body["warning"]
        set_root_task.assert_called_once()
        chain.return_value.apply_async.assert_called_once()
        with Session(engine) as session:
            run = session.exec(
                select(MeetingProcessingRun).where(
                    MeetingProcessingRun.meeting_id == meeting_id
                )
            ).one()
            assert run.status == "queued"
    finally:
        _delete_meeting(meeting_id)


def test_resummarize_does_not_dispatch_without_run_attempt_identity(
    client, normal_user_token_headers
) -> None:
    meeting_id = _make_meeting()
    invalid_runs = [
        None,
        (
            SimpleNamespace(id=12345, dispatch_attempt_id=None, trigger="resummarize"),
            ("completed", None, None),
        ),
    ]
    try:
        for invalid_run in invalid_runs:
            with (
                patch.object(
                    meeting_processing_audit,
                    "create_resummarize_run",
                    return_value=invalid_run,
                ),
                patch("celery.chain") as chain,
            ):
                response = client.post(
                    f"/api/v1/meetings/{meeting_id}/summarize",
                    headers=normal_user_token_headers,
                )
            assert response.status_code == 409
            chain.assert_not_called()
        with Session(engine) as session:
            assert session.get(Meeting, meeting_id).status == "completed"
    finally:
        _delete_meeting(meeting_id)
