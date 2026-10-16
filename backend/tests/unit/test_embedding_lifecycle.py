# SPDX-License-Identifier: AGPL-3.0-only
"""Fenced normal-pipeline embedding lifecycle tests."""

import sys
from types import ModuleType, SimpleNamespace

import pytest
from celery.exceptions import Ignore


@pytest.fixture(autouse=True)
def create_test_user():
    """Keep these fake-backed unit tests off the shared PostgreSQL fixture."""
    yield


def _worker(monkeypatch):
    storage = ModuleType("app.services.storage")
    storage.StorageProvider = object
    storage.storage = SimpleNamespace(
        get_presigned_download_url=lambda *args, **kwargs: "http://example.invalid/audio",
        upload_file=lambda *args, **kwargs: None,
        delete_file=lambda *args, **kwargs: None,
        delete_prefix=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "app.services.storage", storage)
    import app.worker as worker

    return worker


def _run(task, meeting_id, headers=None):
    task.push_request(headers=headers or {})
    try:
        return task.run(meeting_id)
    finally:
        task.pop_request()


def _meeting():
    return SimpleNamespace(
        id=10,
        owner_id=20,
        group_id=30,
        source_type="upload",
        transcript_text="current transcript content",
        transliterated_text=None,
        summary_text="current summary",
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
    )


class _Session:
    def __init__(self, active_sessions):
        self.active_sessions = active_sessions

    def __enter__(self):
        self.active_sessions.append(self)
        return self

    def __exit__(self, *args):
        self.active_sessions.remove(self)


def _install_fence(monkeypatch, worker, state, events, active_sessions):
    monkeypatch.setattr(
        worker,
        "Session",
        lambda engine: _Session(active_sessions),
    )

    def locked_current_run(session, run_id, meeting_id, attempt_id):
        assert session in active_sessions
        events.append("validate")
        if (
            run_id != state["run_id"]
            or attempt_id != state["attempt_id"]
            or state["status"] not in {"queued", "running"}
        ):
            return None, None
        return state["meeting"], SimpleNamespace(
            id=run_id,
            status=state["status"],
            dispatch_attempt_id=attempt_id,
        )

    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "_locked_current_run",
        locked_current_run,
    )


def _settings(monkeypatch, worker):
    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"meeting_processing_run_id": "51"},
        {"dispatch_attempt_id": "attempt-51"},
    ],
)
def test_missing_or_partial_identity_fails_before_embedding(monkeypatch, headers):
    worker = _worker(monkeypatch)
    _settings(monkeypatch, worker)
    calls = []
    monkeypatch.setattr(
        worker,
        "Session",
        lambda engine: calls.append("database") or pytest.fail("identity must be checked first"),
    )
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: calls.append("provider") or pytest.fail("provider must not be called"),
    )

    with pytest.raises(Ignore):
        _run(worker.stage_embedding, 10, headers)

    assert calls == []


@pytest.mark.parametrize("change", ["cancel", "supersede"])
def test_attempt_invalidated_during_embedding_preserves_vectors(monkeypatch, change):
    worker = _worker(monkeypatch)
    _settings(monkeypatch, worker)
    state = {
        "run_id": 51,
        "attempt_id": "attempt-51",
        "status": "running",
        "meeting": _meeting(),
    }
    events = []
    active_sessions = []
    vectors = ["newer-index"]
    _install_fence(monkeypatch, worker, state, events, active_sessions)

    def embed(texts):
        assert not active_sessions, "provider calls must not hold database locks"
        events.append("embed")
        if change == "cancel":
            state["status"] = "cancelled"
        else:
            state["run_id"] = 52
            state["attempt_id"] = "attempt-52"
        return [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=embed))

    def vector_store():
        def delete_by_filter(**kwargs):
            events.append("delete")
            vectors.clear()

        def upsert_points(points):
            events.append("upsert")
            vectors.extend(point.text for point in points)

        return SimpleNamespace(delete_by_filter=delete_by_filter, upsert_points=upsert_points)

    monkeypatch.setattr(worker, "get_vector_store", vector_store)
    requeues = []
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: requeues.append(meeting_id))

    with pytest.raises(Ignore):
        _run(
            worker.stage_embedding,
            10,
            {"meeting_processing_run_id": "51", "dispatch_attempt_id": "attempt-51"},
        )

    assert events == ["validate", "embed", "validate"]
    assert vectors == ["newer-index"]
    assert requeues == []


def test_current_attempt_replaces_meeting_vectors(monkeypatch):
    worker = _worker(monkeypatch)
    _settings(monkeypatch, worker)
    state = {
        "run_id": 51,
        "attempt_id": "attempt-51",
        "status": "running",
        "meeting": _meeting(),
    }
    events = []
    active_sessions = []
    vectors = ["old-index"]
    _install_fence(monkeypatch, worker, state, events, active_sessions)

    def embed(texts):
        assert not active_sessions
        events.append("embed")
        return [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=embed))

    def delete_by_filter(**kwargs):
        assert active_sessions
        assert kwargs == {"owner_id": 20, "group_id": 30, "meeting_id": 10}
        events.append("delete")
        vectors.clear()

    def upsert_points(points):
        assert active_sessions
        events.append("upsert")
        vectors.extend(point.text for point in points)

    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(delete_by_filter=delete_by_filter, upsert_points=upsert_points),
    )

    result = _run(
        worker.stage_embedding,
        10,
        {"meeting_processing_run_id": "51", "dispatch_attempt_id": "attempt-51"},
    )

    assert result == 10
    assert events == ["validate", "embed", "validate", "delete", "upsert"]
    assert "old-index" not in vectors
    assert vectors
