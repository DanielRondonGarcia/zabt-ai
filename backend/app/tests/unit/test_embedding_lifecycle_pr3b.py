# SPDX-License-Identifier: AGPL-3.0-only
"""PR3b bounded indexing lifecycle tests with fakes only."""

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

    monkeypatch.setattr(worker.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(worker.settings, "EMBEDDING_MODEL", "fake-model")
    monkeypatch.setattr(worker.meeting_service, "get", lambda model, meeting_id: meetings.get(meeting_id))
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: SimpleNamespace(embed=lambda texts: [[float(i + 1)] for i, _ in enumerate(texts)]))
    monkeypatch.setattr(worker, "get_vector_store", lambda: vector_store)
    monkeypatch.setattr(worker.stage_embedding, "delay", lambda meeting_id: enqueued.append(meeting_id))

    # Group assignment: grouped meetings index with owner_id+group_id payload tags.
    meeting.group_id = 30
    assert worker.stage_embedding.run(101) == 101
    first_ids = sorted(vector_store.points_by_id)
    assert first_ids
    assert all(point.owner_id == 7 and point.group_id == 30 and point.meeting_id == 101 for point in vector_store.points_by_id.values())

    # Deterministic reindex: re-running the same content produces the same IDs and enqueues the assigned meeting.
    assert worker.stage_embedding.run(101) == 101
    assert sorted(vector_store.points_by_id) == first_ids

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, group_id):
            return SimpleNamespace(id=group_id, owner_id=7)

        def exec(self, statement):
            return SimpleNamespace(all=lambda: [101])

    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    assert worker.reindex_group.run(30) == 30
    assert enqueued == [101]

    # Unassignment cleanup removes only the meeting vectors.
    assert worker.delete_meeting_vectors.run(101) == 101
    assert vector_store.deletes[-1] == {"owner_id": 7, "group_id": None, "meeting_id": 101}
    assert vector_store.points_by_id == {}

    # Group deletion cleanup removes all remaining vectors for the group.
    meeting.group_id = 30
    worker.stage_embedding.run(101)
    assert vector_store.points_by_id
    assert worker.delete_group_vectors.run(30) == 30
    assert vector_store.deletes[-1] == {"owner_id": 7, "group_id": 30, "meeting_id": None}
    assert vector_store.points_by_id == {}
