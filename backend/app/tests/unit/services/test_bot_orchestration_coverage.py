# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for bot orchestration without live workers or DB."""

from __future__ import annotations

from datetime import datetime
import sys
import types

import pytest

from app.models.bot_job import BotJob, BotJobStatus
from app.models.calendar_event import BotStatus, CalendarEvent
from app.services import bot_orchestration as module
from app.services.bot_orchestration import BotOrchestrationService


class FakeResult:
    def __init__(self, values):
        self.values = values

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class FakeSession:
    jobs: dict[int, BotJob] = {}
    events: dict[int, CalendarEvent] = {}
    next_job_id = 1
    meetings = []

    def __init__(self, _engine=None):
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add(self, obj):
        if isinstance(obj, BotJob):
            if obj.id is None:
                obj.id = FakeSession.next_job_id
                FakeSession.next_job_id += 1
            FakeSession.jobs[obj.id] = obj
        elif isinstance(obj, CalendarEvent):
            FakeSession.events[obj.id] = obj
        else:
            if getattr(obj, "id", None) is None:
                obj.id = len(FakeSession.meetings) + 1
            FakeSession.meetings.append(obj)
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            self.add(obj)

    def get(self, model, object_id):
        if model is BotJob:
            return FakeSession.jobs.get(object_id)
        if model is CalendarEvent:
            return FakeSession.events.get(object_id)
        return None

    def exec(self, _query):
        jobs = sorted(FakeSession.jobs.values(), key=lambda job: job.created_at, reverse=True)
        return FakeResult(jobs)


class FakeResponse:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return dict(self.payload)


class FakeAsyncClient:
    response = FakeResponse({"worker_instance_id": "worker-1"})
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, json):
        FakeAsyncClient.calls.append((url, json, self.kwargs))
        return FakeAsyncClient.response


@pytest.fixture(autouse=True)
def fake_dependencies(monkeypatch):
    FakeSession.jobs = {}
    FakeSession.events = {}
    FakeSession.next_job_id = 1
    FakeSession.meetings = []
    FakeAsyncClient.calls = []
    FakeAsyncClient.response = FakeResponse({"worker_instance_id": "worker-1"})
    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)


def _event(**overrides):
    values = {
        "id": 10,
        "user_id": 99,
        "integration_id": 5,
        "provider": "google",
        "external_event_id": "evt-1",
        "title": "Planning",
        "start_time": datetime.utcnow(),
        "end_time": datetime.utcnow(),
        "join_url": "https://meet.example/test",
        "auto_join": True,
    }
    values.update(overrides)
    event = CalendarEvent(**values)
    FakeSession.events[event.id] = event
    return event


@pytest.mark.asyncio
async def test_dispatch_success_creates_job_posts_to_worker_and_schedules_event():
    service = BotOrchestrationService()
    service.bot_worker_url = "http://worker"
    event = _event()

    job = await service.dispatch_bot(event)

    assert job.status == BotJobStatus.JOINING
    assert job.worker_instance_id == "worker-1"
    assert event.bot_status == BotStatus.SCHEDULED
    assert FakeAsyncClient.calls == [
        (
            "http://worker/jobs",
            {
                "join_url": event.join_url,
                "event_id": event.id,
                "bot_job_id": job.id,
                "callback_url": "http://api:8000/api/v1/integrations/bot-callback",
            },
            {"timeout": 30.0},
        )
    ]


@pytest.mark.asyncio
async def test_dispatch_rejects_missing_join_url_before_creating_job():
    with pytest.raises(ValueError, match="has no join_url"):
        await BotOrchestrationService().dispatch_bot(_event(join_url=None))

    assert FakeSession.jobs == {}


@pytest.mark.asyncio
async def test_dispatch_worker_failure_marks_job_failed_and_resets_event_to_idle():
    service = BotOrchestrationService()
    service.bot_worker_url = "http://worker"
    event = _event()
    FakeAsyncClient.response = FakeResponse(error=RuntimeError("worker down"))

    with pytest.raises(RuntimeError, match="worker down"):
        await service.dispatch_bot(event)

    job = next(iter(FakeSession.jobs.values()))
    assert job.status == BotJobStatus.FAILED
    assert job.error_message == "Failed to dispatch to bot worker"
    assert event.bot_status == BotStatus.IDLE


def test_callback_handles_missing_identifier_and_missing_job_without_changes():
    service = BotOrchestrationService()

    service.handle_callback({"status": "completed"})
    service.handle_callback({"bot_job_id": 999, "status": "completed"})

    assert FakeSession.jobs == {}


def test_callback_updates_job_by_job_id_for_success_and_failure_by_event_id():
    event = _event()
    old_job = BotJob(id=1, calendar_event_id=event.id, join_url=event.join_url, status=BotJobStatus.RECORDING)
    new_job = BotJob(id=2, calendar_event_id=event.id, join_url=event.join_url, status=BotJobStatus.RECORDING)
    FakeSession.jobs = {1: old_job, 2: new_job}

    BotOrchestrationService().handle_callback(
        {
            "bot_job_id": 2,
            "status": "completed",
            "duration_seconds": 42,
            "speakers_count": 3,
            "attendees": [{"email": "a@example.com"}],
        }
    )

    assert new_job.status == BotJobStatus.COMPLETED
    assert new_job.audio_url is None
    assert event.bot_status == BotStatus.COMPLETED

    BotOrchestrationService().handle_callback(
        {"event_id": event.id, "status": "failed", "error_message": "bot rejected"}
    )

    failed_jobs = [job for job in FakeSession.jobs.values() if job.status == BotJobStatus.FAILED]
    assert len(failed_jobs) == 1
    assert failed_jobs[0].error_message == "bot rejected"
    assert event.bot_status == BotStatus.IDLE


def test_callback_completed_audio_creates_meeting_and_dispatches_pipeline(monkeypatch):
    event = _event()
    job = BotJob(id=1, calendar_event_id=event.id, join_url=event.join_url, status=BotJobStatus.RECORDING)
    FakeSession.jobs = {1: job}
    dispatched = []

    worker_module = types.ModuleType("app.worker")
    worker_module.dispatch_pipeline = lambda meeting_id: dispatched.append(meeting_id)
    monkeypatch.setitem(sys.modules, "app.worker", worker_module)

    BotOrchestrationService().handle_callback(
        {"bot_job_id": 1, "status": "completed", "audio_url": "s3://recording.wav"}
    )

    assert event.meeting_id == 1
    assert dispatched == [1]
    assert FakeSession.meetings[0].title == "Planning"
    assert FakeSession.meetings[0].owner_id == event.user_id
    assert FakeSession.meetings[0].source_type == "bot"


def test_get_jobs_for_event_and_get_job_read_from_session():
    event = _event()
    job = BotJob(id=7, calendar_event_id=event.id, join_url=event.join_url, status=BotJobStatus.QUEUED)
    FakeSession.jobs = {7: job}

    service = BotOrchestrationService()

    assert service.get_jobs_for_event(event.id) == [job]
    assert service.get_job(7) is job
    assert service.get_job(999) is None
