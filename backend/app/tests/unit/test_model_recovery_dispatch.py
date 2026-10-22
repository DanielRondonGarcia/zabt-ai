# SPDX-License-Identifier: AGPL-3.0-only
"""Offline resume-dispatch tests for split model stages."""

import sys
from types import ModuleType, SimpleNamespace

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

def _capture_signatures(monkeypatch, worker):
    captured = {}
    def fake_signatures(meeting_id, stages, run_id, attempt_id):
        captured["stage_names"] = [getattr(stage, "name", None) for stage in stages]
        captured["meeting_id"] = meeting_id
        captured["run_id"] = run_id
        captured["attempt_id"] = attempt_id
        return []
    monkeypatch.setattr(worker, "_build_pipeline_signatures", fake_signatures)
    monkeypatch.setattr(worker, "chain", lambda *signatures: SimpleNamespace(
        apply_async=lambda: SimpleNamespace(id="root")
    ))
    return captured

def _monkeypatch_run_creation(monkeypatch, worker):
    run = SimpleNamespace(id=77, dispatch_attempt_id=ATTEMPT_ID)
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "get_or_create_pending_run",
        lambda meeting_id, **kwargs: run,
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit, "set_root_task", lambda *args, **kwargs: True
    )
    return run

def test_resume_dispatch_carries_completed_markers_and_dispatches_split_stages(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=MEETING_ID,
        owner_id=7,
        source_type="upload",
        requested_language=None,
        transcript_text="spoken words",
        transliterated_text=None,
        summary_text="A durable meeting summary.",
        structured_output=None,
        structured_output_status="pending",
        visual_breakdown_status="completed",
        current_processing_run_id=88,
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *args: meeting)
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "previous_resume_lineage",
        lambda meeting_id, current_run_id, *, transcript_text: (
            99,
            ("stage_infer_title", "stage_extract_highlights"),
        ),
    )
    carried = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "carry_forward_completed_stages",
        lambda **kwargs: (
            carried.append(kwargs) or ("stage_infer_title", "stage_extract_highlights")
        ),
    )
    run = SimpleNamespace(id=88, dispatch_attempt_id=ATTEMPT_ID)
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "get_or_create_pending_run",
        lambda meeting_id, **kwargs: run,
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit, "set_root_task", lambda *args, **kwargs: True
    )
    captured = _capture_signatures(monkeypatch, worker)

    worker.dispatch_reprocess_resume(MEETING_ID)

    assert carried == [
        {
            "meeting_id": MEETING_ID,
            "source_run_id": 99,
            "stages": ("stage_extract_highlights", "stage_infer_title"),
        }
    ]
    assert captured["stage_names"] == [
        "stage_extract_structured_output",
        "stage_embedding",
    ]
