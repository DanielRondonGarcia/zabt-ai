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

def _title_meeting(**overrides):
    values = {
        "id": MEETING_ID,
        "owner_id": 7,
        "title": "recording.mp3",
        "summary_text": "A durable meeting summary.",
        "requested_language": "es",
        "transcript_text": "spoken words",
    }
    values.update(overrides)
    return SimpleNamespace(**values)

def test_stage_infer_title_saves_title_for_the_exact_attempt(monkeypatch):
    worker = _worker()
    meeting = _title_meeting()
    claims = []
    updates = []
    saves = []
    completed = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_intelligence",
        lambda **kwargs: claims.append(kwargs)
        or SimpleNamespace(id=2, metadata_={"provider_claimed": True}),
    )
    monkeypatch.setattr(
        worker,
        "_update_sub_status_for_attempt",
        lambda *args, **kwargs: updates.append(args) or True,
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    monkeypatch.setattr(
        worker.meeting_service,
        "validate_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "save_inferred_title_for_attempt",
        lambda *args, **kwargs: saves.append((args, kwargs))
        or _title_meeting(title="Fresh Title"),
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "stage_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    with patch("app.services.ai_agent.infer_title", return_value="Fresh Title") as infer_title:
        monkeypatch.setattr(
            worker, "get_completion_client", lambda *args, **kwargs: object()
        )
        result = _run_stage(worker.stage_infer_title, MEETING_ID)

    assert result == MEETING_ID
    (claim,) = claims
    assert claim["run_id"] == RUN_ID
    assert claim["attempt_id"] == ATTEMPT_ID
    assert claim["meeting_id"] == MEETING_ID
    assert claim["stage"] == "stage_infer_title"
    infer_title.assert_called_once()

    ((save_args, save_kwargs),) = saves
    assert save_args == (MEETING_ID, RUN_ID, ATTEMPT_ID, "Fresh Title")
    assert save_kwargs["owner_id"] == 7
    # The completed marker is owned by the saver (same transaction as the
    # title); no separate post-save stage_completed call remains.
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    assert completed == []

def test_stage_infer_title_rejects_a_stale_or_duplicate_attempt(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_intelligence",
        lambda **kwargs: None,
    )
    sub_updates = []
    monkeypatch.setattr(
        worker,
        "_update_sub_status_for_attempt",
        lambda *args, **kwargs: sub_updates.append(args) or True,
    )
    from celery.exceptions import Ignore

    with patch("app.services.ai_agent.infer_title") as infer_title:
        saves = []
        monkeypatch.setattr(
            worker.meeting_service, "get", lambda *args: _title_meeting()
        )
        monkeypatch.setattr(
            worker.meeting_service,
            "validate_intelligence_for_attempt",
            lambda *args, **kwargs: pytest.fail("identity checked before any gating"),
        )
        monkeypatch.setattr(
            worker.meeting_service,
            "save_inferred_title_for_attempt",
            lambda *args, **kwargs: saves.append(kwargs),
        )
        completed = []
        monkeypatch.setattr(
            worker.meeting_processing_audit,
            "stage_completed",
            lambda **kwargs: completed.append(kwargs),
        )
        with pytest.raises(Ignore):
            _run_stage(worker.stage_infer_title, MEETING_ID)

    infer_title.assert_not_called()
    assert sub_updates == []
    assert saves == []
    assert completed == []

def test_stage_infer_title_noops_without_a_durable_summary(monkeypatch):
    worker = _worker()
    meeting = _title_meeting(summary_text="[Error: provider failed]")
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_intelligence",
        lambda **kwargs: SimpleNamespace(id=2, metadata_={"provider_claimed": True}),
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    monkeypatch.setattr(
        worker.meeting_service,
        "validate_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "save_inferred_title_for_attempt",
        lambda *args, **kwargs: pytest.fail("title save must not run without a summary"),
    )
    completed = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "stage_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    with patch("app.services.ai_agent.infer_title") as infer_title:
        monkeypatch.setattr(
            worker, "get_completion_client", lambda *args, **kwargs: object()
        )
        result = _run_stage(worker.stage_infer_title, MEETING_ID)

    assert result == MEETING_ID
    infer_title.assert_not_called()
    (completion,) = completed
    assert completion["stage"] == "stage_infer_title"
    assert completion["message"]

def test_title_service_replaces_only_file_extension_titles(monkeypatch):
    import app.services.meeting_processing_audit as audit_module

    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        title="My Custom Title",
        summary_text="A durable meeting summary.",
        status="processing",
    )
    locks = []

    def fake_locked_current_run(session, run_id, meeting_id, attempt_id, **kwargs):
        locks.append((run_id, meeting_id, attempt_id))
        return meeting, SimpleNamespace(id=RUN_ID, owner_id=7)

    monkeypatch.setattr(
        audit_module.meeting_processing_audit,
        "_locked_current_run",
        fake_locked_current_run,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            pass

        def commit(self):
            pass

        def refresh(self, item):
            pass

    meeting_module = sys.modules["app.services.meeting"]
    monkeypatch.setattr(meeting_module, "Session", lambda engine: FakeSession())

    saved = meeting_module.meeting_service.save_inferred_title_for_attempt(
        MEETING_ID, RUN_ID, ATTEMPT_ID, "Fresh Title", owner_id=7
    )

    assert saved is meeting
    assert meeting.title == "My Custom Title"
    meeting.title = "recording.MP3"
    meeting_module.meeting_service.save_inferred_title_for_attempt(
        MEETING_ID, RUN_ID, ATTEMPT_ID, "Fresh Title", owner_id=7
    )
    assert meeting.title == "Fresh Title"
    assert locks == [(RUN_ID, MEETING_ID, ATTEMPT_ID)] * 2

def test_rejected_title_save_changes_nothing_for_a_stale_attempt(monkeypatch):
    import app.services.meeting_processing_audit as audit_module

    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        title="recording.mp3",
        status="processing",
    )
    monkeypatch.setattr(
        audit_module.meeting_processing_audit,
        "_locked_current_run",
        lambda *args, **kwargs: (None, None),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            pass

        def commit(self):
            pass

        def refresh(self, item):
            pass

    meeting_module = sys.modules["app.services.meeting"]
    monkeypatch.setattr(meeting_module, "Session", lambda engine: FakeSession())

    result = meeting_module.meeting_service.save_inferred_title_for_attempt(
        MEETING_ID, RUN_ID, ATTEMPT_ID, "Fresh Title", owner_id=7
    )

    assert result is None
    assert meeting.title == "recording.mp3"
