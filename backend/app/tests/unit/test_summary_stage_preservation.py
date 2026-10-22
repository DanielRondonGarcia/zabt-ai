# SPDX-License-Identifier: AGPL-3.0-only
"""S3: a successful summary must survive downstream stage failures.

Covers on_stage_failure stage routing (worker.py) and mark_failed summary
persistence (services/meeting.py) with fakes only — no DB or network.
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest


def _install_storage_stub():
    module = ModuleType("app.services.storage")
    module.StorageProvider = object
    module.storage = SimpleNamespace(
        get_presigned_download_url=lambda *a, **k: "http://example.invalid/audio",
        upload_file=lambda *a, **k: None,
        delete_file=lambda *a, **k: None,
        delete_prefix=lambda *a, **k: None,
    )
    module.get_storage_for_provider = lambda *a, **k: module.storage
    module.get_current_storage = lambda *a, **k: module.storage
    module.get_current_storage_provider = lambda *a, **k: "minio"
    module.storage_provider_from_object_key = lambda *a, **k: "minio"
    sys.modules["app.services.storage"] = module


def _install_email_capture(sent):
    module = ModuleType("app.services.email")

    class FakeEmailService:
        def send_failure_email(self, email, meeting, error):
            sent.append((email, meeting.id, error))

    module.email_service = FakeEmailService()
    sys.modules["app.services.email"] = module


def _worker():
    _install_storage_stub()
    import app.worker as worker

    return worker


MEETING_ID = 15
RUN_ID = 101
ATTEMPT_ID = "attempt-15"


def _failed_meeting_stub():
    return SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        status="failed",
        source_type="upload",
        transcription_type="auto",
    )


def _trigger_stage_failure(worker, monkeypatch, stage, failed_meeting):
    """Run on_stage_failure with mocked persistence and side effects."""
    mark_failed_calls = []
    run_failed_calls = []
    eligible_calls = []
    analytics_calls = []
    email_sent = []

    def fake_mark_failed(meeting_id, error_message, **kwargs):
        mark_failed_calls.append((meeting_id, error_message, kwargs))
        return failed_meeting

    monkeypatch.setattr(worker.meeting_service, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "run_failed",
        lambda run_id, *, attempt_id, error: run_failed_calls.append(
            (run_id, attempt_id, error)
        ),
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "failure_effects_are_eligible",
        lambda **kwargs: eligible_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.analytics,
        "capture",
        lambda owner_id, event, props: analytics_calls.append((owner_id, event, props)),
    )
    _install_email_capture(email_sent)
    monkeypatch.setattr(
        worker, "User", SimpleNamespace(get=lambda session, uid: SimpleNamespace(email="owner@example.invalid"))
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, uid):
            return SimpleNamespace(email="owner@example.invalid")

    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())

    request = SimpleNamespace(
        args=(MEETING_ID,),
        headers={
            "meeting_processing_run_id": str(RUN_ID),
            "dispatch_attempt_id": ATTEMPT_ID,
            "stage": stage,
        },
        task=stage,
    )
    worker.on_stage_failure.run(request, RuntimeError("Request timed out."), "traceback")

    return {
        "mark_failed": mark_failed_calls,
        "run_failed": run_failed_calls,
        "eligible": eligible_calls,
        "analytics": analytics_calls,
        "email": email_sent,
    }


# ── New rule: post-summary model stage failures must not overwrite the summary ─


def test_intelligence_failure_preserves_summary_and_reports(monkeypatch):
    worker = _worker()
    effects = _trigger_stage_failure(
        worker, monkeypatch, "stage_extract_intelligence", _failed_meeting_stub()
    )

    (meeting_id, error, kwargs), = effects["mark_failed"]
    assert meeting_id == MEETING_ID
    assert "Request timed out." in error
    kwargs_only = dict(kwargs)
    assert kwargs_only["processing_run_id"] == RUN_ID
    assert kwargs_only["dispatch_attempt_id"] == ATTEMPT_ID
    # The success summary must not be clobbered by a downstream failure.
    assert kwargs_only["preserve_existing_summary"] is True


@pytest.mark.parametrize(
    "stage",
    [
        "stage_infer_title",
        "stage_extract_highlights",
        "stage_extract_structured_output",
    ],
)
def test_forthcoming_post_summary_stage_failures_preserve_summary(monkeypatch, stage):
    worker = _worker()
    effects = _trigger_stage_failure(worker, monkeypatch, stage, _failed_meeting_stub())

    (meeting_id, _error, kwargs), = effects["mark_failed"]
    assert meeting_id == MEETING_ID
    assert dict(kwargs)["preserve_existing_summary"] is True


# ── Existing behavior that must not change ────────────────────────────────────


def test_summarize_stage_failure_still_overwrites_summary(monkeypatch):
    """A failed summary generation may replace the stored summary."""
    worker = _worker()
    effects = _trigger_stage_failure(
        worker, monkeypatch, "stage_summarize", _failed_meeting_stub()
    )

    (meeting_id, _error, kwargs), = effects["mark_failed"]
    assert meeting_id == MEETING_ID
    assert dict(kwargs)["preserve_existing_summary"] is False


def test_embedding_stage_failure_still_preserves_summary(monkeypatch):
    worker = _worker()
    effects = _trigger_stage_failure(
        worker, monkeypatch, "stage_embedding", _failed_meeting_stub()
    )

    (meeting_id, _error, kwargs), = effects["mark_failed"]
    assert meeting_id == MEETING_ID
    assert dict(kwargs)["preserve_existing_summary"] is True


# ── mark_failed summary persistence through the public service entry point ────


def _mark_failed_session(meeting, run):
    responses = [meeting, run]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: responses.pop(0))

        def add(self, item):
            pass

        def commit(self):
            pass

        def refresh(self, item):
            pass

    return FakeSession()


def _mark_failed_meeting(summary_text):
    return SimpleNamespace(
        id=MEETING_ID,
        status="processing",
        sub_status="extracting_intelligence",
        summary_text=summary_text,
        current_processing_run_id=RUN_ID,
    )


def _mark_failed_run():
    return SimpleNamespace(
        id=RUN_ID,
        meeting_id=MEETING_ID,
        owner_id=7,
        status="running",
        dispatch_attempt_id=ATTEMPT_ID,
        trigger="transcription",
        started_at=None,
        completed_at=None,
        final_error=None,
    )


def _meeting_service():
    from app.services.meeting import MeetingService

    return MeetingService()


def test_mark_failed_preserves_usable_summary_when_requested(monkeypatch):
    service = _meeting_service()
    meeting = _mark_failed_meeting("Executive summary of the weekly review.")
    monkeypatch.setattr(
        "app.services.meeting.Session",
        lambda _engine: _mark_failed_session(meeting, _mark_failed_run()),
    )

    result = service.mark_failed(
        MEETING_ID,
        "Request timed out.",
        processing_run_id=RUN_ID,
        dispatch_attempt_id=ATTEMPT_ID,
        preserve_existing_summary=True,
    )

    assert result is meeting
    assert result.summary_text == "Executive summary of the weekly review."
    assert result.status == "failed"
    assert result.sub_status is None


def test_mark_failed_overwrites_unusable_summary_even_when_preserving(monkeypatch):
    """A stored failure marker/placeholder is not treated as a successful summary."""
    service = _meeting_service()
    meeting = _mark_failed_meeting("Summary could not be generated.")
    monkeypatch.setattr(
        "app.services.meeting.Session",
        lambda _engine: _mark_failed_session(meeting, _mark_failed_run()),
    )

    result = service.mark_failed(
        MEETING_ID,
        "Request timed out.",
        processing_run_id=RUN_ID,
        dispatch_attempt_id=ATTEMPT_ID,
        preserve_existing_summary=True,
    )

    assert result is meeting
    assert result.summary_text.startswith("[Error: ")
    assert "Request timed out." in result.summary_text
    assert result.status == "failed"


def test_mark_failed_still_overwrites_summary_without_preserve(monkeypatch):
    """Default behavior (e.g. failed summary generation) replaces the summary."""
    service = _meeting_service()
    meeting = _mark_failed_meeting("Good summary.")
    monkeypatch.setattr(
        "app.services.meeting.Session",
        lambda _engine: _mark_failed_session(meeting, _mark_failed_run()),
    )

    result = service.mark_failed(
        MEETING_ID,
        "Summarization model failed.",
        processing_run_id=RUN_ID,
        dispatch_attempt_id=ATTEMPT_ID,
    )

    assert result is meeting
    assert result.summary_text.startswith("[Error: ")
    assert result.status == "failed"


def test_mark_failed_rejects_stale_attempt_without_touching_summary(monkeypatch):
    """Identity gates keep earlier behavior: a stale attempt changes nothing."""
    service = _meeting_service()
    meeting = _mark_failed_meeting("Good summary.")
    stale_run = _mark_failed_run()
    stale_run.dispatch_attempt_id = "attempt-stale"
    monkeypatch.setattr(
        "app.services.meeting.Session",
        lambda _engine: _mark_failed_session(meeting, stale_run),
    )

    result = service.mark_failed(
        MEETING_ID,
        "Request timed out.",
        processing_run_id=RUN_ID,
        dispatch_attempt_id=ATTEMPT_ID,
        preserve_existing_summary=True,
    )

    assert result is None
    assert meeting.summary_text == "Good summary."
    assert meeting.status == "processing"
