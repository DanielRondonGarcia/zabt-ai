# SPDX-License-Identifier: AGPL-3.0-only
"""PR2 Celery lifecycle tests with fakes only."""

import sys
from types import ModuleType, SimpleNamespace


def _install_storage_stub():
    module = ModuleType("app.services.storage")
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
    telemetry = []
    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meeting)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: [[1.0, 2.0, 3.0] for _ in texts]))
    monkeypatch.setattr(worker, "get_vector_store", lambda: SimpleNamespace(upsert_points=lambda points: upserts.append(points)))
    monkeypatch.setattr(worker.analytics, "capture", lambda owner_id, event, props: telemetry.append((owner_id, event, props)))

    assert worker.stage_embedding.run(10) == 10
    assert len(upserts) == 1
    assert {point.kind for point in upserts[0]} == {"summary", "transcript"}
    assert all(point.owner_id == 20 and point.group_id == 30 and point.meeting_id == 10 for point in upserts[0])
    assert [event for _, event, _ in telemetry] == ["embedding_index_started", "embedding_index_completed"]
    assert all("text" not in props for _, _, props in telemetry)


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
    monkeypatch.setattr(worker.delete_meeting_vectors, "delay", lambda meeting_id: delayed.append(("delete", meeting_id)))

    assert meeting_endpoint._enqueue_group_assignment_indexing(1, 3, 4) is None
    assert delayed == [("delete", 1), ("index", 1)]

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

    monkeypatch.setattr(meeting_endpoint.meeting_service, "get_meeting", lambda meeting_id: meeting)
    monkeypatch.setattr(meeting_endpoint.group_service, "get_accessible", lambda group_id, user_id: SimpleNamespace(id=group_id))
    monkeypatch.setattr(meeting_endpoint, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(meeting_endpoint, "_build_meeting_response", lambda item: item)
    monkeypatch.setattr(worker.delete_meeting_vectors, "delay", lambda meeting_id: events.append(("delete", meeting_id)))
    monkeypatch.setattr(worker.stage_embedding, "delay", lambda meeting_id: events.append(("index", meeting_id)))

    response = meeting_endpoint.assign_group_to_meeting(
        10,
        meeting_endpoint.AssignGroupPayload(group_id=4),
        current_user=SimpleNamespace(id=2),
    )

    assert response.group_id == 4
    assert events == ["add", "commit", "refresh", ("delete", 10), ("index", 10)]
