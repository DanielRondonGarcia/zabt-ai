# SPDX-License-Identifier: AGPL-3.0-only
"""PR3b bounded indexing lifecycle tests with fakes only."""

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


def _worker():
    _install_storage_stub()
    import app.worker as worker

    return worker


def _run_embedding(worker, meeting_id, *, run_id=456, attempt_id="attempt-456"):
    worker.stage_embedding.push_request(
        headers={
            "meeting_processing_run_id": str(run_id),
            "dispatch_attempt_id": attempt_id,
        }
    )
    try:
        return worker.stage_embedding.run(meeting_id)
    finally:
        worker.stage_embedding.pop_request()


class RecordingVectorStore:
    def __init__(self):
        self.points_by_id = {}
        self.upsert_batches = []
        self.deletes = []

    def upsert_points(self, points):
        self.upsert_batches.append(list(points))
        for point in points:
            self.points_by_id[point.id] = point

    def delete_by_filter(self, *, owner_id=None, group_id=None, meeting_id=None):
        self.deletes.append({"owner_id": owner_id, "group_id": group_id, "meeting_id": meeting_id})
        for point_id, point in list(self.points_by_id.items()):
            if owner_id is not None and point.owner_id != owner_id:
                continue
            if group_id is not None and point.group_id != group_id:
                continue
            if meeting_id is not None and point.meeting_id != meeting_id:
                continue
            del self.points_by_id[point_id]


def test_assignment_indexing_reindex_unassignment_and_group_delete_cleanup(monkeypatch):
    worker = _worker()
    vector_store = RecordingVectorStore()
    meeting = SimpleNamespace(
        id=101,
        owner_id=7,
        group_id=None,
        source_type="upload",
        transcript_text="alpha beta gamma",
        transliterated_text=None,
        summary_text="clinical summary",
        original_summary_text=None,
        structured_output={"diagnosis": "example"},
        structured_output_status="completed",
    )
    meetings = {101: meeting}
    enqueued = []
    run_id = 456
    run = SimpleNamespace(
        id=run_id,
        meeting_id=101,
        dispatch_attempt_id="attempt-456",
        status="running",
        started_at=None,
        completed_at=None,
        final_error=None,
    )
    runs = {run.id: run}
    meeting.current_processing_run_id = run.id

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.settings, "EMBEDDING_MODEL", "fake-model")
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meetings.get(meeting_id))
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: [[float(i + 1)] for i, _ in enumerate(texts)]))
    monkeypatch.setattr(worker, "get_vector_store", lambda: vector_store)
    monkeypatch.setattr(worker, "_capture_embedding_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: enqueued.append(meeting_id))
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "_locked_current_run",
        lambda session, requested_run_id, meeting_id, attempt_id: (
            meeting,
            runs[requested_run_id],
        )
        if (
            requested_run_id in runs
            and meeting_id == 101
            and meeting.current_processing_run_id == requested_run_id
            and runs[requested_run_id].dispatch_attempt_id == attempt_id
            and runs[requested_run_id].status in {"queued", "running"}
        )
        else (None, None),
    )

    def index_current_attempt():
        current_run = runs[meeting.current_processing_run_id]
        if current_run.status not in {"queued", "running"}:
            next_id = max(runs) + 1
            current_run = SimpleNamespace(
                id=next_id,
                meeting_id=101,
                dispatch_attempt_id=f"attempt-{next_id}",
                status="running",
                started_at=None,
                completed_at=None,
                final_error=None,
            )
            runs[next_id] = current_run
            meeting.current_processing_run_id = next_id
        return _run_embedding(
            worker,
            101,
            run_id=current_run.id,
            attempt_id=current_run.dispatch_attempt_id,
        )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, group_id):
            return SimpleNamespace(id=group_id, owner_id=7)

        def exec(self, statement):
            return SimpleNamespace(first=lambda: meeting, all=lambda: [101])

        def add(self, item):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())

    # Group assignment: grouped meetings index with owner_id+group_id payload tags.
    meeting.group_id = 30
    assert index_current_attempt() == 101
    first_ids = sorted(vector_store.points_by_id)
    assert first_ids
    assert all(point.owner_id == 7 and point.group_id == 30 and point.meeting_id == 101 for point in vector_store.points_by_id.values())

    # Deterministic reindex: re-running the same content produces the same IDs.
    assert index_current_attempt() == 101
    assert sorted(vector_store.points_by_id) == first_ids

    assert worker.reindex_group.run(30) == 30
    assert enqueued == [101]

    # Unassignment cleanup removes only the meeting vectors.
    assert worker.delete_meeting_vectors.run(101) == 101
    assert vector_store.deletes[-1] == {"owner_id": 7, "group_id": None, "meeting_id": 101}
    assert vector_store.points_by_id == {}

    # Group deletion cleanup removes all remaining vectors for the group.
    meeting.group_id = 30
    index_current_attempt()
    assert vector_store.points_by_id
    assert worker.delete_group_vectors.run(30) == 30
    assert vector_store.deletes[-1] == {"owner_id": None, "group_id": 30, "meeting_id": None}
    assert vector_store.points_by_id == {}


class _FailingVectorStore:
    def __init__(self, exc):
        self.exc = exc
        self.deletes = []

    def upsert_points(self, points):
        raise self.exc

    def delete_by_filter(self, *, owner_id=None, group_id=None, meeting_id=None):
        self.deletes.append({"owner_id": owner_id, "meeting_id": meeting_id})


def _stage_embedding_failure_env(monkeypatch, *, vector_store, run_row):
    """Shared fakes for stage_embedding failure recording tests (meeting 101)."""
    from app.services import meeting_processing_audit as audit_module

    worker = _worker()
    meeting = SimpleNamespace(
        id=101,
        owner_id=7,
        group_id=None,
        source_type="upload",
        transcript_text="alpha beta gamma",
        transliterated_text=None,
        summary_text="clinical summary",
        original_summary_text=None,
        structured_output={"diagnosis": "example"},
        structured_output_status="completed",
    )
    events = []
    run_failed_calls = []
    fake_run_rows = []

    class FakeAuditSession:  # Used by the real run_failed/_run_terminal path.
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            return SimpleNamespace(first=lambda: None)

        def add(self, item):
            fake_run_rows.append(item)

        def commit(self):
            events.append("audit-commit")

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.settings, "EMBEDDING_MODEL", "fake-model")
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meeting)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(embed=lambda texts: [[float(i + 1)] * 3 for i, _ in enumerate(texts)]),
    )
    monkeypatch.setattr(worker, "get_vector_store", lambda: vector_store)
    monkeypatch.setattr(
        worker,
        "_capture_embedding_event",
        lambda owner_id, event_name, properties: events.append((owner_id, event_name, properties)),
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "_locked_current_run",
        lambda session, requested_run_id, meeting_id=None, attempt_id=None, allow_terminal=False:
        (meeting, run_row),
    )
    monkeypatch.setattr(audit_module, "Session", lambda engine: FakeAuditSession())
    real_run_failed = type(worker.meeting_processing_audit).run_failed

    def recording_run_failed(run_id, **kwargs):
        run_failed_calls.append((run_id, kwargs))
        return real_run_failed(worker.meeting_processing_audit, run_id, **kwargs)

    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "run_failed",
        recording_run_failed,
        raising=False,
    )
    monkeypatch.setattr(worker, "Session", lambda engine: FakeAuditSession())
    return worker, meeting, events, run_failed_calls


def test_stage_embedding_failure_records_safe_final_error_on_run(monkeypatch):
    """L2: a failed embedding stage must not leave the run without final_error."""
    exc = ValueError(
        "UnexpectedResponse 400: JSON payload (47383887 bytes) is larger than allowed "
        "(limit: 33554432 bytes); see https://qdrant.example.internal/collections/x"
    )
    run = SimpleNamespace(
        id=456,
        meeting_id=101,
        dispatch_attempt_id="attempt-456",
        status="running",
        started_at=None,
        completed_at=None,
        final_error=None,
    )
    worker, _meeting, events, run_failed_calls = _stage_embedding_failure_env(
        monkeypatch, vector_store=_FailingVectorStore(exc), run_row=run
    )

    with pytest.raises(ValueError, match="larger than allowed"):
        _run_embedding(worker, 101, run_id=456, attempt_id="attempt-456")

    # The safe error summary lands on the run row through the existing
    # run_failed pattern (sanitize_message redacts URLs; no stack traces).
    assert run.status == "failed"
    assert run.final_error is not None
    assert "47383887" in run.final_error and "larger than allowed" in run.final_error
    assert "[redacted-url]" in run.final_error
    assert "qdrant.example.internal" not in run.final_error
    assert len(run.final_error) <= 1000
    assert run_failed_calls == [(456, {"attempt_id": "attempt-456", "error": exc})]
    # The existing embedding_index_failed capture event is still recorded.
    assert any(event_name == "embedding_index_failed" for _owner, event_name, _props in events)


def test_stage_embedding_defers_run_error_while_celery_may_retry(monkeypatch):
    """A retryable failure on a non-final attempt must not mark the run failed."""
    run = SimpleNamespace(
        id=456,
        meeting_id=101,
        dispatch_attempt_id="attempt-456",
        status="running",
        started_at=None,
        completed_at=None,
        final_error=None,
    )
    worker, _meeting, events, run_failed_calls = _stage_embedding_failure_env(
        monkeypatch, vector_store=_FailingVectorStore(RuntimeError("transient provider outage")), run_row=run
    )

    worker.stage_embedding.push_request(
        headers={
            "meeting_processing_run_id": "456",
            "dispatch_attempt_id": "attempt-456",
        }
    )
    worker.stage_embedding.request.update(retries=0)
    try:
        with pytest.raises(RuntimeError, match="transient provider outage"):
            worker.stage_embedding.run(101)
    finally:
        worker.stage_embedding.pop_request()

    assert run_failed_calls == []
    assert run.status == "running"
    assert run.final_error is None
    assert any(event_name == "embedding_index_failed" for _owner, event_name, _props in events)
