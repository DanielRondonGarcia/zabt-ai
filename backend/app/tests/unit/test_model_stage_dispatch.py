# SPDX-License-Identifier: AGPL-3.0-only
"""Offline tests for independently audited summary pipeline stages."""

import sys
from types import ModuleType, SimpleNamespace

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
    monkeypatch.setattr(
        worker,
        "chain",
        lambda *signatures: SimpleNamespace(
            apply_async=lambda: SimpleNamespace(id="root")
        ),
    )
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


def test_dispatch_pipeline_lists_all_split_stages_in_order(monkeypatch):
    worker = _worker()
    _monkeypatch_run_creation(monkeypatch, worker)
    captured = _capture_signatures(monkeypatch, worker)

    worker.dispatch_pipeline(MEETING_ID)

    assert captured["stage_names"] == [
        "stage_download",
        "stage_transcribe",
        "stage_transliterate",
        "stage_optional_visual_breakdown",
        "stage_summarize",
        "stage_infer_title",
        "stage_extract_highlights",
        "stage_extract_structured_output",
        "stage_embedding",
    ]
    assert captured["meeting_id"] == MEETING_ID
    assert captured["run_id"] == 77
    assert captured["attempt_id"] == ATTEMPT_ID

def test_dispatch_youtube_pipeline_lists_all_split_stages_in_order(monkeypatch):
    worker = _worker()
    _monkeypatch_run_creation(monkeypatch, worker)
    captured = _capture_signatures(monkeypatch, worker)

    worker.dispatch_youtube_pipeline(MEETING_ID)

    assert captured["stage_names"] == [
        "stage_youtube_download",
        "stage_transcribe",
        "stage_transliterate",
        "stage_optional_visual_breakdown",
        "stage_summarize",
        "stage_infer_title",
        "stage_extract_highlights",
        "stage_extract_structured_output",
        "stage_embedding",
    ]
