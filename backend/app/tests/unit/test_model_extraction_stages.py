# SPDX-License-Identifier: AGPL-3.0-only
"""Offline tests for independently audited summary pipeline stages."""

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

def _claimed_stage_worker(meeting, monkeypatch):
    worker = _worker()
    claims = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_intelligence",
        lambda **kwargs: claims.append(kwargs)
        or SimpleNamespace(id=2, metadata_={"provider_claimed": True}),
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
    completed = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "stage_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    return claims, completed

def test_stage_extract_highlights_saves_empty_successes(monkeypatch):
    worker = _worker()
    meeting = _title_meeting()
    claims, completed = _claimed_stage_worker(meeting, monkeypatch)
    saves = []
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: pytest.fail(
            "highlight stage must not restart structured output"
        ),
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_highlights",
        lambda transcript: {"chapters": [], "action_items": [], "key_questions": []},
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.save_highlights",
        lambda *args, **kwargs: saves.append((args, kwargs)) or [],
    )

    result = _run_stage(worker.stage_extract_highlights, MEETING_ID)

    assert result == MEETING_ID
    (claim,) = claims
    assert claim["stage"] == "stage_extract_highlights"
    ((save_args, save_kwargs),) = saves
    assert save_args == (
        MEETING_ID,
        {"chapters": [], "action_items": [], "key_questions": []},
    )
    assert save_kwargs["owner_id"] == 7
    assert save_kwargs["dispatch_attempt_id"] == ATTEMPT_ID
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    # The completed marker is owned by the saver (same transaction as the
    # rows); no separate post-save stage_completed call remains.
    assert completed == []

def test_stage_extract_structured_output_is_a_separate_audited_stage(monkeypatch):
    worker = _worker()
    meeting = _title_meeting(meeting_type="standup")
    claims, completed = _claimed_stage_worker(meeting, monkeypatch)
    begins = []
    completes = []
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: begins.append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_intelligence_for_attempt",
        lambda *args, **kwargs: completes.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output",
        lambda transcript, meeting_type: {"meeting_type": meeting_type},
    )

    result = _run_stage(worker.stage_extract_structured_output, MEETING_ID)

    assert result == MEETING_ID
    (claim,) = claims
    assert claim["stage"] == "stage_extract_structured_output"
    assert begins[0]["dispatch_attempt_id"] == ATTEMPT_ID
    assert begins[0]["owner_id"] == 7
    ((complete_args, complete_kwargs),) = completes
    assert complete_args[1] == {"meeting_type": "standup"}
    assert complete_kwargs["owner_id"] == 7
    assert complete_kwargs["audit_task_id"] == "split-stage-task"
    # The completed marker is owned by the saver (same transaction as the
    # output); no separate post-save stage_completed call remains.
    assert completed == []

def test_extraction_stages_noop_without_sources(monkeypatch):
    worker = _worker()
    claims, completed = _claimed_stage_worker(
        _title_meeting(summary_text=None, transcript_text=None), monkeypatch
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_highlights",
        lambda transcript: pytest.fail("no provider for a meeting without a transcript"),
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.save_highlights",
        lambda *args, **kwargs: pytest.fail("no save without a transcript"),
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_without_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    with patch(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output"
    ) as extract_structured:
        assert _run_stage(worker.stage_extract_highlights, MEETING_ID) == MEETING_ID

    extract_structured.assert_not_called()
    highlight_completion = completed[0]
    assert highlight_completion["stage"] == "stage_extract_highlights"
    assert highlight_completion["message"]

    claims.clear()
    completed.clear()
    no_transcript = _title_meeting(transcript_text=None)
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: no_transcript)
    with (
        patch(
            "app.services.meeting_intelligence.intelligence_service.extract_structured_output"
        ) as extract_structured,
        patch(
            "app.services.meeting_intelligence.intelligence_service.extract_highlights",
            lambda transcript: pytest.fail("no provider for a meeting without a transcript"),
        ),
    ):
        assert _run_stage(worker.stage_extract_structured_output, MEETING_ID) == MEETING_ID

    extract_structured.assert_not_called()
    structured_completion = completed[0]
    assert structured_completion["stage"] == "stage_extract_structured_output"
    assert structured_completion["message"]
