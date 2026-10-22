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
