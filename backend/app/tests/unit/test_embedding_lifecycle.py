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
    sys.modules["app.services.storage"] = module


def _worker():
    _install_storage_stub()
    import app.worker as worker

    return worker


def test_indexing_disabled_short_circuits_all_tasks(monkeypatch):
    worker = _worker()
    calls = []
    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", False)
    monkeypatch.setattr(worker, "get_vector_store", lambda: calls.append("store"))

    assert worker.stage_embedding.run(1) == 1
    assert worker.delete_meeting_vectors.run(1) == 1
    assert worker.reindex_meeting.run(1) == 1
    assert worker.delete_group_vectors.run(2) == 2
    assert worker.reindex_group.run(2) == 2
    assert calls == []


def test_stage_embedding_skips_ungrouped_meeting(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: SimpleNamespace(id=meeting_id, group_id=None))
    calls = []
    monkeypatch.setattr(worker, "get_vector_store", lambda: calls.append("store"))

    assert worker.stage_embedding.run(10) == 10
    assert calls == []


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
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meeting)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
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

    assert worker.stage_embedding.run(10) == 10
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

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meeting)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: None)
    monkeypatch.setattr(
        worker,
        "get_embedding_provider",
        lambda: SimpleNamespace(embed=lambda texts: [[1.0, 2.0, 3.0] for _ in texts]),
    )
    monkeypatch.setattr(
        worker,
        "get_vector_store",
        lambda: SimpleNamespace(upsert_points=lambda points: upserts.append(points)),
    )
    monkeypatch.setattr(worker.analytics, "capture", lambda *args, **kwargs: None)

    assert worker.stage_embedding.run(11) == 11
    assert [point.kind for point in upserts[0]] == ["summary"]
    assert upserts[0][0].text == "A visual-only summary"


def test_pipeline_embedding_stage_is_not_linked_to_meeting_failure():
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
    )
    embedding_signature = signatures[-1]

    assert embedding_signature.task == "stage_embedding"
    assert embedding_signature.options["headers"] == {
        "meeting_id": "123",
        "stage": "stage_embedding",
        "meeting_processing_run_id": "456",
    }
    assert embedding_signature.options["link_error"] == []


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


def test_stage_embedding_discards_stale_snapshot_and_requeues_current_assignment(monkeypatch):
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
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: snapshot)
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(worker, "_get_group_introduction", lambda group_id: "Planning context")
    monkeypatch.setattr(worker.reindex_meeting, "delay", lambda meeting_id: requeued.append(meeting_id))
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: vector_calls.append(texts) or [[1.0] for _ in texts]))
    monkeypatch.setattr(worker, "get_vector_store", lambda: (_ for _ in ()).throw(AssertionError("stale work must not touch vectors")))

    assert worker.stage_embedding.run(10) == 10
    assert requeued == [10]
    assert vector_calls == [["Group introduction: Planning context\n\nsummary", "Group introduction: Planning context\n\none two three"]]
