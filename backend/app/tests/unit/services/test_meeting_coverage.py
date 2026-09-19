# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused MeetingService coverage without app startup or live providers."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models import Meeting, MeetingCreate, TranscriptSegment, TranscriptionType, VisualSegment
from app.services import base as base_module
from app.services import meeting as meeting_module
from app.services.meeting import MeetingService


class FakeExecResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class MeetingStore:
    def __init__(self):
        self.meetings: dict[int, Meeting] = {}
        self.segments: dict[int, TranscriptSegment] = {}
        self.visual_segments: list[VisualSegment] = []
        self.next_meeting_id = 1
        self.next_segment_id = 1
        self.exec_queue: list[list[object]] = []
        self.deleted_visual_for: list[int] = []
        self.deleted_meeting_ids: list[int] = []
        self.added: list[object] = []

    def session(self):
        return FakeMeetingSession(self)

    def add_meeting(self, **kwargs) -> Meeting:
        meeting = Meeting(**kwargs)
        self.persist(meeting)
        return meeting

    def persist(self, obj):
        self.added.append(obj)
        if isinstance(obj, Meeting):
            if obj.id is None:
                obj.id = self.next_meeting_id
                self.next_meeting_id += 1
            self.meetings[obj.id] = obj
        elif isinstance(obj, TranscriptSegment):
            if obj.id is None:
                obj.id = self.next_segment_id
                self.next_segment_id += 1
            self.segments[obj.id] = obj
        elif isinstance(obj, VisualSegment):
            self.visual_segments.append(obj)

    def get(self, model, obj_id: int):
        if model is Meeting:
            return self.meetings.get(obj_id)
        if model is TranscriptSegment:
            return self.segments.get(obj_id)
        raise AssertionError(f"Unexpected model: {model}")

    def delete(self, obj):
        if isinstance(obj, Meeting) and obj.id in self.meetings:
            self.deleted_meeting_ids.append(obj.id)
            del self.meetings[obj.id]
        elif isinstance(obj, TranscriptSegment) and obj.id in self.segments:
            del self.segments[obj.id]

    def exec_rows(self):
        if self.exec_queue:
            return self.exec_queue.pop(0)
        return list(self.meetings.values())


class FakeMeetingSession:
    def __init__(self, store: MeetingStore):
        self.store = store
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, model, obj_id: int):
        return self.store.get(model, obj_id)

    def exec(self, statement):
        text = str(statement)
        if "DELETE FROM visualsegment" in text:
            # SQLAlchemy Delete keeps criteria internal; tests assert that deletion was requested.
            self.store.deleted_visual_for.append(-1)
            return FakeExecResult([])
        return FakeExecResult(self.store.exec_rows())

    def add(self, obj):
        self.store.persist(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass

    def delete(self, obj):
        self.store.delete(obj)


@pytest.fixture(name="store", autouse=True)
def fixture_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MeetingStore]:
    store = MeetingStore()
    monkeypatch.setattr(meeting_module, "Session", lambda _engine: store.session())
    monkeypatch.setattr(base_module, "Session", lambda _engine: store.session())
    yield store


@pytest.fixture(name="service")
def fixture_service() -> MeetingService:
    return MeetingService()


def install_worker(monkeypatch: pytest.MonkeyPatch):
    calls = SimpleNamespace(dispatch=[], embedding=[], delete_vectors=[])

    class DelayRecorder:
        def __init__(self, sink):
            self.sink = sink

        def delay(self, meeting_id):
            self.sink.append(meeting_id)

    worker = types.ModuleType("app.worker")
    worker.dispatch_pipeline = lambda meeting_id: calls.dispatch.append(meeting_id)
    worker.stage_embedding = DelayRecorder(calls.embedding)
    worker.delete_meeting_vectors = DelayRecorder(calls.delete_vectors)
    monkeypatch.setitem(sys.modules, "app.worker", worker)
    return calls


def test_create_and_get_meeting_persist_and_load(service: MeetingService, store: MeetingStore) -> None:
    created = service.create_meeting(MeetingCreate(title="Planning", file_path="a.mp3"), owner_id=7)
    store.exec_queue.append([created])

    loaded = service.get_meeting(created.id)

    assert created.id is not None
    assert created.owner_id == 7
    assert loaded is created


def test_get_meetings_returns_session_rows(service: MeetingService, store: MeetingStore) -> None:
    newer = store.add_meeting(title="New", owner_id=1, summary_text="long")
    store.exec_queue.append([newer])

    assert service.get_meetings(owner_id=1) == [newer]


def test_status_transcription_type_substatus_heartbeat_and_completion(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Daily", owner_id=1, status="pending_upload")
    redis_calls: list[tuple[str, str]] = []
    redis_module = types.ModuleType("redis")

    class FakeRedis:
        def publish(self, channel, message):
            redis_calls.append((channel, message))

        def close(self):
            redis_calls.append(("closed", ""))

    redis_module.from_url = lambda _url: FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", redis_module)

    queued = service.update_status(meeting.id, "queued")
    typed = service.update_transcription_type(meeting.id, TranscriptionType.MEDICAL)
    meeting_type = service.update_meeting_type(meeting.id, "grooming")
    sub_status = service.update_sub_status(meeting.id, "transcribing")

    assert queued.processing_heartbeat_at is not None
    assert typed.transcription_type == TranscriptionType.MEDICAL
    assert meeting_type.meeting_type == "grooming"
    assert sub_status.sub_status == "transcribing"
    assert (f"meeting:{meeting.id}:status", "transcribing") in redis_calls

    completed = service.mark_completed(
        meeting.id,
        summary_text="summary",
        action_items_text="actions",
        template_id=3,
        template_name="SOAP",
    )

    assert completed.status == "completed"
    assert completed.sub_status is None
    assert completed.summary_text == "summary"
    assert completed.action_items_text == "actions"
    assert completed.template_id == 3
    assert completed.template_name == "SOAP"


def test_update_sub_status_ignores_redis_errors(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Redis", status="queued")
    redis_module = types.ModuleType("redis")
    redis_module.from_url = lambda _url: (_ for _ in ()).throw(RuntimeError("redis down"))
    monkeypatch.setitem(sys.modules, "redis", redis_module)

    updated = service.update_sub_status(meeting.id, "summarizing")

    assert updated.status == "processing"
    assert updated.sub_status == "summarizing"


def test_status_methods_return_none_for_missing_meetings(service: MeetingService) -> None:
    assert service.update_status(404, "queued") is None
    assert service.update_transcription_type(404, TranscriptionType.GENERAL) is None
    assert service.update_meeting_type(404, "generic") is None
    assert service.update_sub_status(404, "stage") is None
    assert service.mark_completed(404) is None
    assert service.save_summary(404, "summary") is None
    assert service.mark_failed(404, "boom") is None


def test_save_summary_marks_processing_without_completing(service: MeetingService, store: MeetingStore) -> None:
    meeting = store.add_meeting(title="Daily", owner_id=1, status="queued")

    saved = service.save_summary(meeting.id, "draft", template_id=8, template_name="Brief")

    assert saved.status == "processing"
    assert saved.sub_status == "summarizing"
    assert saved.summary_text == "draft"
    assert saved.template_id == 8
    assert saved.template_name == "Brief"


def test_touch_processing_heartbeat_handles_active_inactive_missing_and_session_errors(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = store.add_meeting(title="Active", status="processing")
    completed = store.add_meeting(title="Done", status="completed")
    heartbeat_at = datetime(2026, 1, 1)

    assert service.touch_processing_heartbeat(active.id, heartbeat_at=heartbeat_at) is True
    assert active.processing_heartbeat_at == heartbeat_at
    assert service.touch_processing_heartbeat(completed.id) is False
    assert service.touch_processing_heartbeat(999) is False

    def broken_session(_engine):
        raise RuntimeError("db down")

    monkeypatch.setattr(meeting_module, "Session", broken_session)
    assert service.touch_processing_heartbeat(active.id) is False


def test_queue_visual_breakdown_creates_epoch_and_reuses_active_epoch(
    service: MeetingService, store: MeetingStore
) -> None:
    meeting = store.add_meeting(title="Video", status="processing")
    store.exec_queue.append([meeting])

    epoch, created = service.queue_visual_breakdown(meeting.id)

    assert created is True
    assert meeting.visual_breakdown_status == "queued"
    assert meeting.visual_breakdown_params["run_epoch"] == epoch
    assert meeting.visual_breakdown_params["attempts"] == 0

    store.exec_queue.append([meeting])
    same_epoch, created_again = service.queue_visual_breakdown(meeting.id)

    assert (same_epoch, created_again) == (epoch, False)


def test_queue_visual_breakdown_raises_for_missing_meeting(service: MeetingService, store: MeetingStore) -> None:
    store.exec_queue.append([])
    with pytest.raises(RuntimeError, match="not found"):
        service.queue_visual_breakdown(404)


def test_begin_visual_breakdown_raises_for_missing_meeting(
    service: MeetingService, store: MeetingStore
) -> None:
    store.exec_queue.append([])

    with pytest.raises(RuntimeError, match="not found"):
        service.begin_visual_breakdown(404)


def test_begin_visual_breakdown_handles_terminal_processing_queued_and_new_epochs(
    service: MeetingService, store: MeetingStore
) -> None:
    terminal = store.add_meeting(
        title="Done",
        visual_breakdown_status="completed",
        visual_breakdown_params={"run_epoch": "terminal"},
    )
    processing = store.add_meeting(
        title="Processing",
        visual_breakdown_status="processing",
        visual_breakdown_params={"run_epoch": "active"},
    )
    queued = store.add_meeting(
        title="Queued",
        visual_breakdown_status="queued",
        visual_breakdown_params={"run_epoch": "queued"},
    )
    fresh = store.add_meeting(title="Fresh")
    store.exec_queue.extend([[terminal], [processing], [queued], [fresh]])

    assert service.begin_visual_breakdown(terminal.id) == ("terminal", False)
    assert service.begin_visual_breakdown(processing.id) == ("active", False)
    assert service.begin_visual_breakdown(queued.id) == ("queued", True)
    new_epoch, started = service.begin_visual_breakdown(fresh.id)

    assert queued.visual_breakdown_status == "processing"
    assert started is True
    assert fresh.visual_breakdown_status == "processing"
    assert fresh.visual_breakdown_params["run_epoch"] == new_epoch


def test_reset_stale_visual_breakdown_in_session_requeues_processing_and_ignores_non_processing(
    service: MeetingService, store: MeetingStore
) -> None:
    session = store.session()
    processing = store.add_meeting(
        title="Stale",
        id=10,
        visual_breakdown_status="processing",
        visual_breakdown_params={"run_epoch": "old", "side_effects": {"completion_event": True}},
    )
    completed = store.add_meeting(title="Done", visual_breakdown_status="completed")

    epoch = service.reset_stale_visual_breakdown_in_session(session, processing)

    assert epoch is not None and epoch != "old"
    assert processing.visual_breakdown_status == "queued"
    assert processing.visual_breakdown_error is None
    assert processing.visual_breakdown_completed_at is None
    assert processing.visual_breakdown_params["run_epoch"] == epoch
    assert processing.processing_heartbeat_at is not None
    assert store.deleted_visual_for == [-1]
    assert service.reset_stale_visual_breakdown_in_session(session, completed) is None


def test_increment_visual_breakdown_attempt_counts_only_matching_epoch(
    service: MeetingService, store: MeetingStore
) -> None:
    meeting = store.add_meeting(
        title="Video",
        visual_breakdown_params={"run_epoch": "epoch", "attempts": 2},
    )
    store.exec_queue.extend([[meeting], [meeting]])

    assert service.increment_visual_breakdown_attempt(meeting.id, "wrong") == 2
    assert service.increment_visual_breakdown_attempt(meeting.id, "epoch") == 3
    assert meeting.visual_breakdown_params["attempts"] == 3


def test_increment_visual_breakdown_attempt_raises_for_missing_meeting(
    service: MeetingService, store: MeetingStore
) -> None:
    store.exec_queue.append([])

    with pytest.raises(RuntimeError, match="not found"):
        service.increment_visual_breakdown_attempt(404, "epoch")


def test_finalize_visual_breakdown_completed_replaces_segments_and_claims_side_effects(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(
        title="Video",
        owner_id=5,
        status="processing",
        visual_breakdown_status="processing",
        visual_breakdown_params={"run_epoch": "epoch", "attempts": 2, "side_effects": {}},
        visual_breakdown_run_count=1,
    )
    replacements: list[tuple[int, list[dict]]] = []

    class FakeVisualSegmentService:
        @staticmethod
        def replace_for_meeting_in_session(_session, meeting_id, segments):
            replacements.append((meeting_id, list(segments)))

    visual_module = types.ModuleType("app.services.visual_segments")
    visual_module.VisualSegmentService = FakeVisualSegmentService
    monkeypatch.setitem(sys.modules, "app.services.visual_segments", visual_module)
    store.exec_queue.append([meeting])

    result = service.finalize_visual_breakdown(
        meeting.id,
        "epoch",
        outcome="completed",
        reason="ok",
        warning_code=None,
        result_params={"frames": 4},
        raw_output_s3_key="raw.json",
        model="qwen",
        segments=[{"caption": "slide"}],
    )

    assert result == {
        "applied": True,
        "meeting_id": meeting.id,
        "owner_id": 5,
        "title": "Video",
        "status": "completed",
        "warning_code": None,
        "segment_count": 1,
        "completion_event": True,
        "warning_event": False,
        "notification_event": True,
        "attempts": 2,
    }
    assert replacements == [(meeting.id, [{"caption": "slide"}])]
    assert meeting.visual_breakdown_status == "completed"
    assert meeting.visual_raw_output_s3_key == "raw.json"
    assert meeting.visual_breakdown_model == "qwen"
    assert meeting.visual_breakdown_params["frames"] == 4
    assert meeting.visual_breakdown_params["side_effects"] == {
        "completion_event": True,
        "notification_event": True,
    }
    assert meeting.visual_breakdown_run_count == 2


def test_finalize_visual_breakdown_rejects_invalid_stale_duplicate_and_missing(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    visual_module = types.ModuleType("app.services.visual_segments")
    visual_module.VisualSegmentService = SimpleNamespace(
        replace_for_meeting_in_session=lambda *_args, **_kwargs: None
    )
    monkeypatch.setitem(sys.modules, "app.services.visual_segments", visual_module)
    stale = store.add_meeting(title="Stale", visual_breakdown_params={"run_epoch": "new"})
    duplicate = store.add_meeting(
        title="Duplicate",
        visual_breakdown_params={"run_epoch": "epoch", "outcome": "completed"},
    )

    with pytest.raises(ValueError, match="Unsupported visual outcome"):
        service.finalize_visual_breakdown(1, "epoch", outcome="bad")

    store.exec_queue.extend([[stale], [duplicate], []])
    assert service.finalize_visual_breakdown(stale.id, "old", outcome="completed") == {
        "applied": False,
        "meeting_id": stale.id,
    }
    assert service.finalize_visual_breakdown(duplicate.id, "epoch", outcome="completed") == {
        "applied": False,
        "meeting_id": duplicate.id,
    }
    with pytest.raises(RuntimeError, match="not found"):
        service.finalize_visual_breakdown(404, "epoch", outcome="completed")


def test_finalize_visual_breakdown_warning_outcome_claims_warning_event_without_run_count(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(
        title="Warn",
        owner_id=2,
        visual_breakdown_params={"run_epoch": "epoch", "side_effects": {}},
        visual_breakdown_run_count=7,
    )
    visual_module = types.ModuleType("app.services.visual_segments")
    visual_module.VisualSegmentService = SimpleNamespace(
        replace_for_meeting_in_session=lambda *_args, **_kwargs: None
    )
    monkeypatch.setitem(sys.modules, "app.services.visual_segments", visual_module)
    store.exec_queue.append([meeting])

    result = service.finalize_visual_breakdown(
        meeting.id,
        "epoch",
        outcome="fallback",
        warning_code="low_confidence",
        run_count=False,
    )

    assert result["applied"] is True
    assert result["warning_event"] is True
    assert result["completion_event"] is False
    assert result["notification_event"] is False
    assert meeting.visual_breakdown_status == "fallback"
    assert meeting.visual_breakdown_error == "low_confidence"
    assert meeting.visual_breakdown_run_count == 7


def test_mark_failed_and_add_segment(service: MeetingService, store: MeetingStore) -> None:
    meeting = store.add_meeting(title="Failure")

    failed = service.mark_failed(meeting.id, "provider down")
    segment = service.add_segment(meeting.id, 1.5, 3.0, "hello")

    assert failed.status == "failed"
    assert failed.sub_status is None
    assert failed.summary_text == "[Error: provider down]"
    assert segment.id is not None
    assert segment.meeting_id == meeting.id
    assert segment.start_time == 1.5
    assert segment.end_time == 3.0
    assert segment.text == "hello"


def test_initiate_processing_dispatches_only_pending_upload(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = store.add_meeting(title="Upload", file_path="users/1/a.mp3", status="pending_upload")
    calls = install_worker(monkeypatch)
    store.exec_queue.append([pending])

    initiated = service.initiate_processing("users/1/a.mp3")

    assert initiated is pending
    assert initiated.status == "queued"
    assert initiated.processing_heartbeat_at is not None
    assert calls.dispatch == [pending.id]

    store.exec_queue.append([])
    assert service.initiate_processing("missing.mp3") is None


def test_update_and_restore_summary_preserve_original_and_reindex_grouped_meeting(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Grouped", summary_text="AI", group_id=9)
    calls = install_worker(monkeypatch)

    updated = service.update_summary(meeting.id, "Human")
    restored = service.restore_summary(meeting.id)

    assert updated.original_summary_text == "AI"
    assert updated.summary_text == "AI"
    assert updated.summary_edited is False
    assert restored.summary_text == "AI"
    assert calls.embedding == [meeting.id, meeting.id]


def test_summary_embedding_errors_are_ignored(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(
        title="Grouped", summary_text="AI", original_summary_text="AI", group_id=9
    )
    worker = types.ModuleType("app.worker")

    class BrokenDelay:
        def delay(self, _meeting_id):
            raise RuntimeError("queue down")

    worker.stage_embedding = BrokenDelay()
    monkeypatch.setitem(sys.modules, "app.worker", worker)

    assert service.update_summary(meeting.id, "Human").summary_text == "Human"
    assert service.restore_summary(meeting.id).summary_text == "AI"


def test_update_summary_without_existing_summary_does_not_set_original(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Empty", summary_text=None, group_id=None)
    calls = install_worker(monkeypatch)

    updated = service.update_summary(meeting.id, "First")

    assert updated.original_summary_text is None
    assert updated.summary_text == "First"
    assert updated.summary_edited is True
    assert calls.embedding == []


def test_restore_summary_missing_and_no_original_return_none(service: MeetingService, store: MeetingStore) -> None:
    meeting = store.add_meeting(title="No original", summary_text="Human")

    assert service.update_summary(404, "Missing") is None
    assert service.restore_summary(404) is None
    assert service.restore_summary(meeting.id) is None


def test_create_and_count_active_youtube(service: MeetingService, store: MeetingStore) -> None:
    meeting = service.create_from_youtube("https://youtu.be/abc", owner_id=4)
    store.exec_queue.append([
        meeting,
        store.add_meeting(title="Other", owner_id=4, source_type="youtube", status="processing"),
    ])

    assert meeting.title == "YouTube Video"
    assert meeting.source_type == "youtube"
    assert meeting.source_url == "https://youtu.be/abc"
    assert meeting.status == "queued"
    assert meeting.processing_heartbeat_at is not None
    assert service.count_active_youtube(owner_id=4) == 2


def test_update_field_persists_value_and_reindexes_text_fields_for_grouped_meeting(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Field", group_id=2)
    calls = install_worker(monkeypatch)

    updated = service.update_field(meeting.id, "summary_text", "new")

    assert updated.summary_text == "new"
    assert calls.embedding == [meeting.id]
    assert service.update_field(404, "summary_text", "missing") is None


def test_update_field_ignores_embedding_errors_and_non_text_fields(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Field", group_id=2)
    worker = types.ModuleType("app.worker")

    class BrokenDelay:
        def delay(self, _meeting_id):
            raise RuntimeError("celery down")

    worker.stage_embedding = BrokenDelay()
    monkeypatch.setitem(sys.modules, "app.worker", worker)

    assert service.update_field(meeting.id, "summary_text", "new").summary_text == "new"
    assert service.update_field(meeting.id, "title", "Renamed").title == "Renamed"


def test_delete_meeting_requests_vector_cleanup_and_deletes_record(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Delete")
    calls = install_worker(monkeypatch)

    assert service.delete_meeting(meeting.id) is True
    assert calls.delete_vectors == [meeting.id]
    assert meeting.id in store.deleted_meeting_ids
    assert service.delete_meeting(404) is False


def test_delete_meeting_continues_when_cleanup_task_import_or_delay_fails(
    service: MeetingService, store: MeetingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = store.add_meeting(title="Delete")
    worker = types.ModuleType("app.worker")

    class BrokenDelete:
        def delay(self, _meeting_id):
            raise RuntimeError("queue down")

    worker.delete_meeting_vectors = BrokenDelete()
    monkeypatch.setitem(sys.modules, "app.worker", worker)

    assert service.delete_meeting(meeting.id) is True
