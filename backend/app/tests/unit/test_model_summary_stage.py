# SPDX-License-Identifier: AGPL-3.0-only
"""Offline tests for summary generation and title inference stages."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

MEETING_ID = 15
RUN_ID = 101
ATTEMPT_ID = "attempt-15"

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

def _worker():
    _install_storage_stub()
    import app.worker as worker

    return worker

def _run_stage(task, meeting_id):
    task.push_request(
        headers={
            "meeting_processing_run_id": str(RUN_ID),
            "dispatch_attempt_id": ATTEMPT_ID,
        },
        id="split-stage-task",
    )
    try:
        return task.run(meeting_id)
    finally:
        task.pop_request()

@pytest.fixture(autouse=True)
def isolated_worker_dependencies(monkeypatch):
    worker = _worker()

    class EmptySegmentSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            return []

    monkeypatch.setattr(worker, "Session", lambda engine: EmptySegmentSession())
    monkeypatch.setattr(worker, "get_completion_client", lambda *a, **k: object())
    monkeypatch.setattr(worker.meeting_processing_audit, "stage_completed", lambda **k: None)

def _fake_multimodal_context(monkeypatch):
    """Provide a stub build_context; worker imports it at call time."""
    multimodal = ModuleType("app.services.multimodal_context")
    multimodal.build_context = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "app.services.multimodal_context", multimodal)

def _patch_success_effects(monkeypatch, worker):
    async def _not_eligible(*args, **kwargs):
        return False

    monkeypatch.setattr(
        worker.meeting_processing_audit, "success_effects_are_eligible", _not_eligible
    )
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)

def test_summarize_stage_generates_and_saves_summary_without_title_inference(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        title="recording.mp3",
        transcript_text="spoken words",
        requested_language=None,
        created_at=None,
    )
    claims = []
    saves = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_summary",
        lambda **kwargs: claims.append(kwargs) or SimpleNamespace(id=1, metadata_={}),
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    _fake_multimodal_context(monkeypatch)
    monkeypatch.setattr(worker.style_service, "get_style_examples", lambda: [])
    monkeypatch.setattr(
        worker.template_service, "get_active_default", lambda owner_id: None
    )
    _patch_success_effects(monkeypatch, worker)

    with (
        patch("app.worker.summarize_transcript", return_value="New summary text") as summarize,
        patch("app.services.ai_agent.infer_title") as infer_title,
        patch("app.services.email.email_service.send_summary_email") as send_email,
        patch.object(worker, "notify", lambda *args, **kwargs: None),
    ):
        monkeypatch.setattr(
            worker.meeting_service,
            "save_summary_for_attempt",
            lambda *args, **kwargs: saves.append((args, kwargs)) or meeting,
        )
        result = _run_stage(worker.stage_summarize, MEETING_ID)

    assert result == MEETING_ID
    assert claims[0]["meeting_id"] == MEETING_ID
    (claim_kwargs,) = claims
    assert claim_kwargs["meeting_id"] == MEETING_ID

    # Summary generated and saved with its own transactional completion marker;
    # no title inference and none passed along.
    summarize.assert_called_once()
    infer_title.assert_not_called()
    ((save_args, save_kwargs),) = saves
    assert save_args == (MEETING_ID, RUN_ID, ATTEMPT_ID, "New summary text")
    assert "inferred_title" not in save_kwargs
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    assert send_email.call_count == 0

def test_summarize_stage_saves_an_empty_no_source_attempt_without_a_title(monkeypatch):
    """No transcript and no visuals: the stage saves an empty summary and stops."""
    worker = _worker()
    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        title="recording.mp3",
        transcript_text=None,
        requested_language=None,
        created_at=None,
    )
    saves = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_summary",
        lambda **kwargs: SimpleNamespace(id=1, metadata_={}),
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    _fake_multimodal_context(monkeypatch)
    _patch_success_effects(monkeypatch, worker)
    monkeypatch.setattr(
        worker.meeting_service,
        "save_summary_for_attempt",
        lambda *args, **kwargs: saves.append((args, kwargs)) or meeting,
    )
    with (
        patch("app.worker.summarize_transcript") as summarize,
        patch("app.services.ai_agent.infer_title") as infer_title,
    ):
        _run_stage(worker.stage_summarize, MEETING_ID)

    summarize.assert_not_called()
    infer_title.assert_not_called()
    ((save_args, save_kwargs),) = saves
    assert save_args[3] is None
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    assert "inferred_title" not in save_kwargs


SUMMARY_PROGRESS_STAGE = "stage_summarize_progress"


def _fake_summarize_with_progress(events: list[dict]):
    """A summarize_transcript stand-in that drives the pipeline's progress callback."""

    def fake_summarize(*args, **kwargs):
        progress = kwargs["progress_callback"]
        progress(
            phase="partial_chunk_summary",
            block_index=1,
            block_total=2,
            outcome="started",
            correlation_id="a" * 32,
        )
        progress(
            phase="partial_chunk_summary",
            block_index=1,
            block_total=2,
            outcome="completed",
            duration_ms=5,
            correlation_id="a" * 32,
        )
        progress(
            phase="partial_chunk_summary",
            block_index=2,
            block_total=2,
            outcome="started",
            correlation_id="b" * 32,
        )
        # Window 2 fails visibly while the pipeline keeps going.
        progress(
            phase="partial_chunk_summary",
            block_index=2,
            block_total=2,
            outcome="failed",
            duration_ms=7,
            error_class="AIProviderError",
            correlation_id="b" * 32,
        )
        progress(
            phase="summary_synthesis",
            block_index=3,
            block_total=3,
            outcome="started",
            correlation_id="c" * 32,
        )
        progress(
            phase="summary_synthesis",
            block_index=3,
            block_total=3,
            outcome="completed",
            duration_ms=20,
            correlation_id="c" * 32,
        )
        events.append(kwargs["progress_callback"])
        return "New summary text"

    return fake_summarize


def _summarize_stage_with_progress(
    monkeypatch, summarize_side_effect, *, fail_audit_writes: bool = False
):
    worker = _worker()
    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        title="recording.mp3",
        transcript_text="spoken words",
        requested_language=None,
        created_at=None,
    )
    saves = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_summary",
        lambda **kwargs: SimpleNamespace(id=1, metadata_={}),
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    _fake_multimodal_context(monkeypatch)
    monkeypatch.setattr(worker.style_service, "get_style_examples", lambda: [])
    monkeypatch.setattr(
        worker.template_service, "get_active_default", lambda owner_id: None
    )
    _patch_success_effects(monkeypatch, worker)

    writes = {"started": [], "completed": [], "failed": []}

    def _record_started(**kwargs):
        if fail_audit_writes:
            raise RuntimeError("audit database unavailable")
        writes["started"].append(kwargs)
        return SimpleNamespace(id=len(writes["started"]) + 1)

    def _record_completed(**kwargs):
        if fail_audit_writes:
            raise RuntimeError("audit database unavailable")
        writes["completed"].append(kwargs)

    def _record_failed(**kwargs):
        if fail_audit_writes:
            raise RuntimeError("audit database unavailable")
        writes["failed"].append(kwargs)

    monkeypatch.setattr(
        worker.meeting_processing_audit, "stage_started", _record_started
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit, "stage_completed", _record_completed
    )
    monkeypatch.setattr(worker.meeting_processing_audit, "stage_failed", _record_failed)
    monkeypatch.setattr(
        worker.meeting_service,
        "save_summary_for_attempt",
        lambda *args, **kwargs: saves.append((args, kwargs)) or meeting,
    )

    with (
        patch(
            "app.worker.summarize_transcript",
            side_effect=summarize_side_effect,
        ) as summarize,
        patch("app.services.ai_agent.infer_title"),
    ):
        result = _run_stage(worker.stage_summarize, MEETING_ID)

    assert result == MEETING_ID
    assert summarize.call_count == 1
    return writes, saves, summarize


def test_summarize_stage_records_window_and_synthesis_progress_on_the_current_attempt(
    monkeypatch,
):
    writes, saves, summarize = _summarize_stage_with_progress(
        monkeypatch, _fake_summarize_with_progress([])
    )

    ((save_args, save_kwargs),) = saves
    assert save_args == (MEETING_ID, RUN_ID, ATTEMPT_ID, "New summary text")

    started = writes["started"]
    assert [event["stage"] for event in started] == [
        SUMMARY_PROGRESS_STAGE
    ] * 3
    # Every event is fenced to the exact dispatch run and attempt from the task headers.
    for event in [*started, *writes["completed"], *writes["failed"]]:
        assert event["run_id"] == RUN_ID
        assert event["attempt_id"] == ATTEMPT_ID
        assert event["meeting_id"] == MEETING_ID
    # One started event per provider call, discriminated per call.
    task_ids = [event["task_id"] for event in started]
    assert len(set(task_ids)) == 3
    assert all(task_ids)
    assert [event["metadata"]["block_index"] for event in started] == [1, 2, 3]
    assert [event["metadata"]["phase"] for event in started] == [
        "partial_chunk_summary",
        "partial_chunk_summary",
        "summary_synthesis",
    ]
    assert [event["metadata"]["block_total"] for event in started] == [2, 2, 3]
    assert [event["metadata"]["correlation_id"] for event in started] == [
        "a" * 32,
        "b" * 32,
        "c" * 32,
    ]

    completed = writes["completed"]
    assert [event["metadata"]["block_index"] for event in completed] == [1, 3]
    assert [event["metadata"]["duration_ms"] for event in completed] == [5, 20]
    assert [event["metadata"]["correlation_id"] for event in completed] == [
        "a" * 32,
        "c" * 32,
    ]
    assert [event["message"] for event in completed] == [
        "summary progress partial_chunk_summary block 1/2 completed",
        "summary progress summary_synthesis block 3/3 completed",
    ]

    failed = writes["failed"]
    assert len(failed) == 1
    assert failed[0]["metadata"]["block_index"] == 2
    assert failed[0]["metadata"]["duration_ms"] == 7
    assert failed[0]["metadata"]["correlation_id"] == "b" * 32
    # Only the sanitized error class is stored, never a provider message.
    assert failed[0]["error"] == "AIProviderError"


def test_summarize_stage_progress_audit_write_failures_do_not_break_the_summary(
    monkeypatch,
):
    writes, saves, _summarize = _summarize_stage_with_progress(
        monkeypatch,
        _fake_summarize_with_progress([]),
        fail_audit_writes=True,
    )

    # The audit recorder blew up on every call; the summary is still saved.
    assert not writes["started"]
    assert not writes["completed"]
    assert not writes["failed"]
    ((save_args, save_kwargs),) = saves
    assert save_args == (MEETING_ID, RUN_ID, ATTEMPT_ID, "New summary text")


def test_summarize_stage_without_progress_callback_records_no_progress_events(
    monkeypatch,
):
    """The default callback keeps the summarize stage byte-for-byte compatible."""

    def fake_summarize(*args, **kwargs):
        assert kwargs.get("progress_callback") is not None
        return "New summary text"

    writes, saves, _summarize = _summarize_stage_with_progress(
        monkeypatch, fake_summarize
    )

    assert not writes["started"]
    assert not writes["completed"]
    assert not writes["failed"]
    ((save_args, _save_kwargs),) = saves
    assert save_args == (MEETING_ID, RUN_ID, ATTEMPT_ID, "New summary text")
