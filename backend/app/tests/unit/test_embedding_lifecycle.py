# SPDX-License-Identifier: AGPL-3.0-only
"""PR2 Celery lifecycle tests with fakes only."""

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


def _run_embedding(worker, meeting_id):
    worker.stage_embedding.push_request(
        headers={
            "meeting_processing_run_id": "456",
            "dispatch_attempt_id": "attempt-456",
        }
    )
    try:
        return worker.stage_embedding.run(meeting_id)
    finally:
        worker.stage_embedding.pop_request()


def _install_embedding_run_check(monkeypatch, worker, *meetings):
    responses = iter(meetings)
    run = SimpleNamespace(
        id=456,
        meeting_id=meetings[0].id,
        owner_id=meetings[0].owner_id,
        status="running",
        dispatch_attempt_id="attempt-456",
        started_at=None,
        completed_at=None,
        final_error=None,
    )

    def locked_current_run(session, run_id, meeting_id, attempt_id):
        assert (run_id, meeting_id, attempt_id) == (456, meetings[0].id, "attempt-456")
        return next(responses), run

    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "_locked_current_run",
        locked_current_run,
    )
    return run


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None




def test_indexing_disabled_rejects_embedding_stage(monkeypatch):
    worker = _worker()
    calls = []
    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", False)
    monkeypatch.setattr(worker, "get_vector_store", lambda: calls.append("store"))

    with pytest.raises(RuntimeError, match="indexing is disabled"):
        worker.stage_embedding.run(1)
    with pytest.raises(RuntimeError, match="indexing is disabled"):
        worker.reindex_group.run(2)

    assert calls == []

def test_stage_embedding_indexes_ungrouped_visual_content(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=10,
        owner_id=20,
        group_id=None,
        source_type="upload",
        transcript_text=None,
        transliterated_text=None,
        summary_text=None,
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
        visual_segments=[
            SimpleNamespace(
                start_time=12.5,
                end_time=18.0,
                caption="A person falls beside the doorway.",
            )
        ],
    )
    upserts = []
    embedded_texts = []
    group_context_requests = []

    transaction_items = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            transaction_items.append(item)

        def commit(self):
            transaction_items.append("commit")

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    run = _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(
        worker,
        "_get_group_introduction",
        lambda group_id: group_context_requests.append(group_id),
    )
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(
            embed=lambda texts: embedded_texts.extend(texts) or [[1.0, 2.0, 3.0] for _ in texts]
        ),
    )
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(
            delete_by_filter=lambda **kwargs: None,
            upsert_points=lambda points: upserts.append(points),
        ),
    )
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)

    assert _run_embedding(worker, 10) == 10
    assert len(upserts) == 1
    assert len(upserts[0]) == 1
    assert upserts[0][0].group_id is None
    assert upserts[0][0].kind == "visual"
    assert "12.50s-18.00s" in upserts[0][0].text
    assert "A person falls beside the doorway." in upserts[0][0].text
    assert embedded_texts == [upserts[0][0].text]
    assert group_context_requests == []
    assert run.status == "completed"
    assert meeting.status == "completed"
    assert transaction_items[0] is run
    assert [item.stage for item in transaction_items if hasattr(item, "stage")] == [
        "stage_embedding",
        "run",
    ]
    assert transaction_items[-1] == "commit"


def test_stage_embedding_builds_points_and_upserts(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=10,
        owner_id=20,
        group_id=30,
        source_type="upload",
        transcript_text="one two three",
        transliterated_text=None,
        summary_text="summary",
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
    )
    upserts = []
    embedded_texts = []
    telemetry = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: meeting)

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: "Planning context")
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(
            embed=lambda texts: (
                embedded_texts.extend(texts)
                or [[1.0, 2.0, 3.0] for _ in texts]
            )
        ),
    )
    monkeypatch.setattr(worker, "get_vector_store", lambda: SimpleNamespace(upsert_points=lambda points: upserts.append(points)))
    monkeypatch.setattr(worker.analytics, "capture", lambda owner_id, event, props: telemetry.append((owner_id, event, props)))

    assert _run_embedding(worker, 10) == 10
    assert len(upserts) == 1
    assert {point.kind for point in upserts[0]} == {"summary", "transcript"}
    assert all(point.owner_id == 20 and point.group_id == 30 and point.meeting_id == 10 for point in upserts[0])
    assert all("Group introduction: Planning context" in text for text in embedded_texts)
    assert all("Group introduction" not in point.text for point in upserts[0])
    assert [event for _, event, _ in telemetry] == ["embedding_index_started", "embedding_index_completed"]
    assert all("text" not in props for _, _, props in telemetry)


def test_stage_embedding_indexes_summary_without_transcript(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=11,
        owner_id=21,
        group_id=31,
        source_type="upload",
        transcript_text=None,
        transliterated_text=None,
        summary_text="A visual-only summary",
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
    )
    upserts = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: meeting)

        def add(self, item):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(embed=lambda texts: [[1.0, 2.0, 3.0] for _ in texts]),
    )
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(
            delete_by_filter=lambda **kwargs: None,
            upsert_points=lambda points: upserts.append(points),
        ),
    )
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)

    assert _run_embedding(worker, 11) == 11
    assert [point.kind for point in upserts[0]] == ["summary"]
    assert upserts[0][0].text == "A visual-only summary"




def test_assignment_and_summary_hooks_enqueue_indexing(monkeypatch):
    _install_storage_stub()
    from app.api.v1.endpoints import meetings as meeting_endpoint
    from app.services.meeting import MeetingService

    worker = _worker()
    delayed = []
    monkeypatch.setattr(worker.stage_embedding, "delay", lambda meeting_id: delayed.append(("index", meeting_id)))
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: delayed.append(("reindex", meeting_id)))

    assert meeting_endpoint._enqueue_group_assignment_indexing(1, 3, 4) is None
    assert meeting_endpoint._enqueue_group_assignment_indexing(1, 4, 4) is None
    assert delayed == [("reindex", 1), ("reindex", 1)]

    service = MeetingService()
    monkeypatch.setattr(service, "get", lambda model, meeting_id: SimpleNamespace(id=meeting_id, group_id=9, original_summary_text=None, summary_text="old"))
    monkeypatch.setattr(service, "save", lambda meeting: meeting)
    service.update_summary(55, "new")
    assert delayed[-1] == ("index", 55)


def test_pipeline_embedding_stage_links_failure_callback():
    worker = _worker()

    signatures = worker._build_pipeline_signatures(
        123,
        [
            worker.stage_download,
            worker.stage_transcribe,
            worker.stage_transliterate,
            worker.stage_optional_visual_breakdown,
            worker.stage_summarize,
            worker.stage_extract_intelligence,
            worker.stage_embedding,
        ],
        456,
        "attempt-456",
    )
    embedding_signature = signatures[-1]

    assert embedding_signature.task == "stage_embedding"
    assert embedding_signature.options["headers"] == {
        "meeting_id": "123",
        "stage": "stage_embedding",
        "meeting_processing_run_id": "456",
        "dispatch_attempt_id": "attempt-456",
    }
    assert [callback.task for callback in embedding_signature.options["link_error"]] == [
        "on_stage_failure"
    ]

def test_embedding_completion_records_stage_and_run_in_caller_transaction():
    worker = _worker()
    audit_module = worker.meeting_processing_audit
    events = []
    run = SimpleNamespace(
        id=456,
        meeting_id=10,
        dispatch_attempt_id="attempt-456",
        status="running",
        started_at=None,
        completed_at=None,
        final_error="stale failure",
    )

    class FakeSession:
        def add(self, item):
            events.append(item)

    session = FakeSession()
    assert audit_module.complete_embedding_in_session(
        session,
        run=run,
        meeting_id=10,
        attempt_id="attempt-456",
        task_id="embedding-task",
        point_count=3,
    )

    assert run.status == "completed"
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.final_error is None
    audit_events = [event for event in events if hasattr(event, "stage")]
    assert events[0] is run
    assert [(event.stage, event.event_type, event.status) for event in audit_events] == [
        ("stage_embedding", "completed", "completed"),
        ("run", "completed", "completed"),
    ]
    assert audit_events[0].metadata_ == {"point_count": 3}
    assert all(event.dispatch_attempt_id == "attempt-456" for event in audit_events)

def test_stage_embedding_rejects_empty_meeting_content(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=12,
        owner_id=22,
        group_id=None,
        source_type="youtube",
        transcript_text=None,
        transliterated_text=None,
        summary_text=None,
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
        visual_segments=[],
        status="processing",
    )
    upserts = []
    deletes = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: (_ for _ in ()).throw(AssertionError("no chunks to embed")))
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(
            delete_by_filter=lambda **kwargs: deletes.append(kwargs),
            upsert_points=lambda points: upserts.extend(points),
        ),
    )

    with pytest.raises(RuntimeError, match="no indexable content"):
        _run_embedding(worker, 12)

    assert upserts == []
    assert deletes == []
    assert meeting.status == "processing"

def test_stage_embedding_rejects_embedding_count_mismatch(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=13,
        owner_id=23,
        group_id=None,
        source_type="youtube",
        transcript_text="non-empty transcript",
        transliterated_text=None,
        summary_text=None,
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
        visual_segments=[],
        status="processing",
    )
    upserts = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: []))
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(delete_by_filter=lambda **kwargs: None, upsert_points=lambda points: upserts.extend(points)),
    )

    with pytest.raises(RuntimeError, match="Embedding count mismatch"):
        _run_embedding(worker, 13)

    assert upserts == []
    assert meeting.status == "processing"

def test_stage_embedding_completes_meeting_only_after_vector_persistence(monkeypatch):
    worker = _worker()
    meeting = SimpleNamespace(
        id=14,
        owner_id=24,
        group_id=None,
        source_type="upload",
        transcript_text="non-empty transcript",
        transliterated_text=None,
        summary_text=None,
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
        visual_segments=[],
        status="processing",
        sub_status="indexing_embeddings",
    )
    events = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, value):
            events.append("meeting-save")

        def commit(self):
            events.append("meeting-commit")

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, meeting, meeting)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(embed=lambda texts: [[0.1, 0.2, 0.3] for _ in texts]),
    )

    def upsert(points):
        assert meeting.status == "processing"
        assert points
        events.append("vector-persisted")

    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(delete_by_filter=lambda **kwargs: None, upsert_points=upsert),
    )
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)

    assert _run_embedding(worker, 14) == 14

    assert meeting.status == "completed"
    assert events.index("vector-persisted") < events.index("meeting-commit")

@pytest.mark.parametrize(
    ("method_name", "transcript_text"),
    [
        ("complete_intelligence_for_attempt", "existing transcript"),
        ("complete_without_intelligence_for_attempt", None),
    ],
)
def test_intelligence_completion_waits_for_embedding_gate(monkeypatch, method_name, transcript_text):
    worker = _worker()
    meeting = SimpleNamespace(
        id=15,
        owner_id=25,
        transcript_text=transcript_text,
        status="processing",
        sub_status="extracting_intelligence",
        processing_heartbeat_at=None,
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

    monkeypatch.setattr(sys.modules["app.services.meeting"], "Session", lambda engine: FakeSession())
    monkeypatch.setattr(
        worker.meeting_service,
        "_locked_intelligence_meeting",
        lambda session, *args, **kwargs: meeting,
    )
    method = getattr(worker.meeting_service, method_name)
    if method_name == "complete_intelligence_for_attempt":
        result = method(
            15,
            {"topics": []},
            processing_run_id=456,
            dispatch_attempt_id="attempt-456",
            owner_id=25,
        )
    else:
        result = method(
            15,
            processing_run_id=456,
            dispatch_attempt_id="attempt-456",
            owner_id=25,
        )

    assert result is True
    assert meeting.status == "processing"
    assert meeting.sub_status == "indexing_embeddings"

def test_embedding_failure_callback_marks_meeting_failed_for_exact_run(monkeypatch):
    worker = _worker()
    request = SimpleNamespace(
        args=(14,),
        headers={
            "meeting_processing_run_id": "789",
            "dispatch_attempt_id": "attempt-789",
        },
    )
    failed_meeting = SimpleNamespace(status="failed", owner_id=None)
    failures = []
    monkeypatch.setattr(
        worker.meeting_service,
        "mark_failed",
        lambda meeting_id, error, **kwargs: failures.append((meeting_id, error, kwargs)) or failed_meeting,
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "run_failed",
        lambda run_id, **kwargs: failures.append(("run", run_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "failure_effects_are_eligible",
        lambda **kwargs: False,
    )

    worker.on_stage_failure(request, RuntimeError("Qdrant unavailable"), None)

    assert len(failures) == 2
    assert failures[0][0] == 14
    assert str(failures[0][1]) == "Qdrant unavailable"
    assert failures[0][2] == {
        "processing_run_id": 789,
        "dispatch_attempt_id": "attempt-789",
        "preserve_existing_summary": False,
    }
    assert failures[1][0:2] == ("run", 789)
    assert failures[1][2]["attempt_id"] == "attempt-789"
    assert str(failures[1][2]["error"]) == "Qdrant unavailable"

def test_assign_group_enqueues_indexing_after_assignment_commit(monkeypatch):
    _install_storage_stub()
    from app.api.v1.endpoints import meetings as meeting_endpoint

    worker = _worker()
    events = []
    meeting = SimpleNamespace(id=10, owner_id=2, group_id=3)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, meeting_id):
            return meeting

        def add(self, item):
            events.append("add")

        def commit(self):
            events.append("commit")

        def refresh(self, item):
            events.append("refresh")

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: meeting)

    monkeypatch.setattr(meeting_endpoint.meeting_service, "get_meeting_for_access", lambda meeting_id, user_id: meeting)
    monkeypatch.setattr(meeting_endpoint.meeting_service, "require_editor_in_session", lambda session, meeting_id, user_id: meeting)
    monkeypatch.setattr(meeting_endpoint.meeting_service, "require_group_editor_in_session", lambda session, group_id, user_id: None)
    monkeypatch.setattr(meeting_endpoint, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(meeting_endpoint, "_build_meeting_response", lambda item, user_id: item)
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: events.append(("reindex", meeting_id)))

    response = meeting_endpoint.assign_group_to_meeting(
        10,
        meeting_endpoint.AssignGroupPayload(group_id=4),
        current_user=SimpleNamespace(id=2),
    )

    assert response.group_id == 4
    assert events == ["add", "commit", "refresh", ("reindex", 10)]


def test_assign_group_returns_retryable_error_after_durable_assignment(monkeypatch):
    _install_storage_stub()
    from fastapi import HTTPException
    from app.api.v1.endpoints import meetings as meeting_endpoint

    worker = _worker()
    events = []
    meeting = SimpleNamespace(id=10, owner_id=2, group_id=3)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: meeting)

        def get(self, model, object_id):
            return SimpleNamespace(id=object_id, owner_id=2)

        def add(self, item):
            events.append("add")

        def commit(self):
            events.append("commit")

        def refresh(self, item):
            events.append("refresh")

    monkeypatch.setattr(meeting_endpoint.meeting_service, "require_editor_in_session", lambda session, meeting_id, user_id: meeting)
    monkeypatch.setattr(meeting_endpoint.meeting_service, "require_group_editor_in_session", lambda session, group_id, user_id: None)
    monkeypatch.setattr(meeting_endpoint, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(meeting_endpoint, "_build_meeting_response", lambda item, user_id: item)
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: (_ for _ in ()).throw(RuntimeError("broker unavailable")))

    with pytest.raises(HTTPException) as exc_info:
        meeting_endpoint.assign_group_to_meeting(
            10,
            meeting_endpoint.AssignGroupPayload(group_id=4),
            current_user=SimpleNamespace(id=2),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "meeting_reindex_pending",
        "meeting_id": 10,
        "group_id": 4,
        "message": "The meeting assignment was saved, but its AI index is pending. Retry the meeting index operation.",
    }
    assert meeting.group_id == 4
    assert events == ["add", "commit", "refresh"]

    monkeypatch.setattr(
        worker.reindex_meeting,
        "delay",
        lambda meeting_id: events.append(("reindex", meeting_id))
        or SimpleNamespace(id="retry-task"),
    )
    retry_response = meeting_endpoint.assign_group_to_meeting(
        10,
        meeting_endpoint.AssignGroupPayload(group_id=4),
        current_user=SimpleNamespace(id=2),
    )

    assert retry_response.group_id == 4
    assert events == ["add", "commit", "refresh", "add", "commit", "refresh", ("reindex", 10)]


def test_meeting_reindex_endpoint_provides_retry_path(monkeypatch):
    _install_storage_stub()
    from app.api.v1.endpoints import meetings as meeting_endpoint

    worker = _worker()
    meeting = SimpleNamespace(id=10, owner_id=2, group_id=4)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, object_id):
            return meeting

    monkeypatch.setattr(meeting_endpoint, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(
        meeting_endpoint.meeting_service,
        "require_editor_in_session",
        lambda session, meeting_id, user_id: meeting,
    )
    monkeypatch.setattr(
        worker.reindex_meeting,
        "delay",
        lambda meeting_id: SimpleNamespace(id="retry-task"),
    )

    response = meeting_endpoint.reindex_meeting_endpoint(
        10,
        current_user=SimpleNamespace(id=2),
    )

    assert response.model_dump() == {"status": "accepted", "task_id": "retry-task"}


def test_reindex_meeting_runs_delete_before_index(monkeypatch):
    worker = _worker()
    events = []
    meeting = SimpleNamespace(id=10, group_id=4)

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.delete_meeting_vectors, "run", lambda meeting_id: events.append(("delete", meeting_id)))
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meeting)
    monkeypatch.setattr(worker.stage_embedding, "run", lambda meeting_id: events.append(("index", meeting_id)))

    assert worker.reindex_meeting.run(10) == 10
    assert events == [("delete", 10), ("index", 10)]


def test_stage_embedding_retries_when_summary_changes_during_embedding(monkeypatch):
    worker = _worker()

    def meeting(summary_text):
        return SimpleNamespace(
            id=16,
            owner_id=26,
            group_id=None,
            source_type="upload",
            transcript_text="stable transcript",
            transliterated_text=None,
            summary_text=summary_text,
            original_summary_text=None,
            structured_output=None,
            structured_output_status="pending",
            visual_segments=[],
            status="processing",
            sub_status="indexing_embeddings",
        )

    snapshot = meeting("original summary")
    current = meeting("edited during embedding")
    vector_effects = []
    embedded = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            vector_effects.append("meeting-save")

        def commit(self):
            vector_effects.append("meeting-commit")

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, snapshot, current)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(embed=lambda texts: embedded.extend(texts) or [[0.1] for _ in texts]),
    )
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(
            delete_by_filter=lambda **kwargs: vector_effects.append("delete"),
            upsert_points=lambda points: vector_effects.append("upsert"),
        ),
    )

    with pytest.raises(RuntimeError, match="content changed during embedding"):
        _run_embedding(worker, 16)

    assert embedded
    assert vector_effects == []
    assert current.status == "processing"
def test_stage_embedding_retries_when_assignment_changes_during_embedding(monkeypatch):
    worker = _worker()
    snapshot = SimpleNamespace(
        id=10,
        owner_id=20,
        group_id=30,
        source_type="upload",
        transcript_text="one two three",
        transliterated_text=None,
        summary_text="summary",
        original_summary_text=None,
        structured_output=None,
        structured_output_status="pending",
    )
    current = SimpleNamespace(id=10, owner_id=20, group_id=40)
    requeued = []
    vector_calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            assert getattr(statement, "_for_update_arg", None) is not None
            return SimpleNamespace(first=lambda: current)

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    _install_embedding_run_check(monkeypatch, worker, snapshot, current)
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: "Planning context")
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: requeued.append(meeting_id))
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: vector_calls.append(texts) or [[1.0] for _ in texts]))
    monkeypatch.setattr(worker, "get_vector_store", lambda: (_ for _ in ()).throw(AssertionError("stale work must not touch vectors")))

    with pytest.raises(RuntimeError, match="assignment changed"):
        _run_embedding(worker, 10)
    assert requeued == []
    assert vector_calls == [["Group introduction: Planning context\n\nsummary", "Group introduction: Planning context\n\none two three"]]
