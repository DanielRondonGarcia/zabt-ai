# SPDX-License-Identifier: AGPL-3.0-only
"""Offline tests for independently audited summary pipeline stages."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

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

def _run_stage(task, meeting_id):
    task.push_request(
        headers={
            "meeting_processing_run_id": str(RUN_ID),
            "dispatch_attempt_id": ATTEMPT_ID,
        },
        id="split-stage-task",
    )
    try:
        return task.run(meeting_id)
    finally:
        task.pop_request()

@pytest.fixture(autouse=True)
def isolated_worker_dependencies(monkeypatch):
    worker = _worker()

    class EmptySegmentSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def exec(self, statement):
            return []

    monkeypatch.setattr(worker, "Session", lambda engine: EmptySegmentSession())
    monkeypatch.setattr(worker, "get_completion_client", lambda *a, **k: object())
    monkeypatch.setattr(worker.meeting_processing_audit, "stage_completed", lambda **k: None)

def _title_meeting(**overrides):
    values = {
        "id": MEETING_ID,
        "owner_id": 7,
        "title": "recording.mp3",
        "summary_text": "A durable meeting summary.",
        "requested_language": "es",
        "transcript_text": "spoken words",
        "segments": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)

def _claimed_stage_worker(meeting, monkeypatch):
    worker = _worker()
    claims = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "claim_intelligence",
        lambda **kwargs: claims.append(kwargs)
        or SimpleNamespace(id=2, metadata_={"provider_claimed": True}),
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *args, **kwargs: True
    )
    # Highlights stages load the meeting through the repository service; the
    # unchanged title/structured stages keep their existing lookup.
    monkeypatch.setattr(
        worker.meeting_service, "get_meeting", lambda *args: meeting
    )
    monkeypatch.setattr(
        worker.meeting_service, "get", lambda *args: meeting
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "validate_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    completed = []
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "stage_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    return claims, completed

def test_stage_extract_highlights_saves_empty_successes(monkeypatch):
    worker = _worker()
    meeting = _title_meeting()
    claims, completed = _claimed_stage_worker(meeting, monkeypatch)
    saves = []
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: pytest.fail(
            "highlight stage must not restart structured output"
        ),
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_highlights",
        lambda transcript, segments=None: {
            "chapters": [],
            "action_items": [],
            "key_questions": [],
        },
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.save_highlights",
        lambda *args, **kwargs: saves.append((args, kwargs)) or [],
    )

    result = _run_stage(worker.stage_extract_highlights, MEETING_ID)

    assert result == MEETING_ID
    (claim,) = claims
    assert claim["stage"] == "stage_extract_highlights"
    ((save_args, save_kwargs),) = saves
    assert save_args == (
        MEETING_ID,
        {"chapters": [], "action_items": [], "key_questions": []},
    )
    assert save_kwargs["owner_id"] == 7
    assert save_kwargs["dispatch_attempt_id"] == ATTEMPT_ID
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    # The completed marker is owned by the saver (same transaction as the
    # rows); no separate post-save stage_completed call remains.
    assert completed == []

def test_stage_extract_structured_output_is_a_separate_audited_stage(monkeypatch):
    worker = _worker()
    meeting = _title_meeting(meeting_type="standup")
    claims, completed = _claimed_stage_worker(meeting, monkeypatch)
    begins = []
    completes = []
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: begins.append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_intelligence_for_attempt",
        lambda *args, **kwargs: completes.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output",
        lambda transcript, meeting_type: {"meeting_type": meeting_type},
    )

    result = _run_stage(worker.stage_extract_structured_output, MEETING_ID)

    assert result == MEETING_ID
    (claim,) = claims
    assert claim["stage"] == "stage_extract_structured_output"
    assert begins[0]["dispatch_attempt_id"] == ATTEMPT_ID
    assert begins[0]["owner_id"] == 7
    ((complete_args, complete_kwargs),) = completes
    assert complete_args[1] == {"meeting_type": "standup"}
    assert complete_kwargs["owner_id"] == 7
    assert complete_kwargs["audit_task_id"] == "split-stage-task"
    # The completed marker is owned by the saver (same transaction as the
    # output); no separate post-save stage_completed call remains.
    assert completed == []

def test_extraction_stages_noop_without_sources(monkeypatch):
    worker = _worker()
    claims, completed = _claimed_stage_worker(
        _title_meeting(summary_text=None, transcript_text=None), monkeypatch
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_highlights",
        lambda transcript, segments=None: pytest.fail(
            "no provider for a meeting without a transcript"
        ),
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.save_highlights",
        lambda *args, **kwargs: pytest.fail("no save without a transcript"),
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_without_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    with patch(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output"
    ) as extract_structured:
        assert _run_stage(worker.stage_extract_highlights, MEETING_ID) == MEETING_ID

    extract_structured.assert_not_called()
    highlight_completion = completed[0]
    assert highlight_completion["stage"] == "stage_extract_highlights"
    assert highlight_completion["message"]

    claims.clear()
    completed.clear()
    no_transcript = _title_meeting(transcript_text=None)
    monkeypatch.setattr(
        worker.meeting_service, "get_meeting", lambda *args: no_transcript
    )
    monkeypatch.setattr(
        worker.meeting_service, "get", lambda *args: no_transcript
    )
    with (
        patch(
            "app.services.meeting_intelligence.intelligence_service.extract_structured_output"
        ) as extract_structured,
        patch(
            "app.services.meeting_intelligence.intelligence_service.extract_highlights",
            lambda transcript, segments=None: pytest.fail(
                "no provider for a meeting without a transcript"
            ),
        ),
    ):
        assert _run_stage(worker.stage_extract_structured_output, MEETING_ID) == MEETING_ID

    extract_structured.assert_not_called()
    structured_completion = completed[0]
    assert structured_completion["stage"] == "stage_extract_structured_output"
    assert structured_completion["message"]


# ── Timed transcript-segment evidence reaches the highlight provider ────────

NUMERIC_TIMED_PAYLOAD = {
    "chapters": [
        {
            "title": "Open",
            "timestamp_start": 0.149,
            "timestamp_end": 10.0,
            "tags": ["intro"],
        }
    ],
    "action_items": [{"content": "Send notes", "speaker": "Bob", "timestamp": 20.5}],
    "key_questions": [],
}


def _install_sdk_parse(monkeypatch, requests, payload_factory):
    """Fake SDK whose parse really validates ``response_format`` (no bypass)."""
    from app.services import meeting_intelligence

    def parse(**kwargs):
        requests.append(kwargs)
        parsed = kwargs["response_format"].model_validate(payload_factory())
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
            )
        ),
    )
    return meeting_intelligence


def _null_parsed_sdk_parse(monkeypatch, requests, payload):
    """Schema-valid provider response whose parsed attribute is null."""
    from app.services import meeting_intelligence

    def parse(**kwargs):
        requests.append(kwargs)
        kwargs["response_format"].model_validate(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=None))]
        )

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
            )
        ),
    )
    return meeting_intelligence


def _timed_stage_common(monkeypatch, meeting):
    claims, completed = _claimed_stage_worker(meeting, monkeypatch)
    saves = []
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.save_highlights",
        lambda *args, **kwargs: saves.append((args, kwargs)) or [],
    )
    return claims, completed, saves


def test_stage_extract_highlights_forwards_timed_segments_with_numeric_payload(monkeypatch):
    worker = _worker()
    segments = [
        SimpleNamespace(
            start_time=0.149, end_time=1.5, text="buen trabajo", speaker="Bob", id=1
        ),
        SimpleNamespace(
            start_time=2.0, end_time=30.75, text="gracias", speaker=None, id=2
        ),
    ]
    meeting = _title_meeting(segments=segments)
    claims, completed, saves = _timed_stage_common(monkeypatch, meeting)
    requests = []
    meeting_intelligence = _install_sdk_parse(
        monkeypatch, requests, lambda: NUMERIC_TIMED_PAYLOAD
    )

    result = _run_stage(worker.stage_extract_highlights, MEETING_ID)

    assert result == MEETING_ID
    (request,) = requests
    assert request["messages"][1]["content"] == (
        "[0.149-1.5] Bob: buen trabajo\n[2.0-30.75] gracias"
    )
    assert request["response_format"] is meeting_intelligence.HighlightsExtraction
    ((save_args, save_kwargs),) = saves
    assert save_args[0] == MEETING_ID
    assert save_args[1] == meeting_intelligence.HighlightsExtraction.model_validate(
        NUMERIC_TIMED_PAYLOAD
    ).model_dump()
    assert save_kwargs["owner_id"] == 7
    assert save_kwargs["dispatch_attempt_id"] == ATTEMPT_ID
    assert save_kwargs["audit_task_id"] == "split-stage-task"
    (claim,) = claims
    assert claim["stage"] == "stage_extract_highlights"
    assert completed == []
    # The durable summary is untouched by the highlight save.
    assert meeting.summary_text == "A durable meeting summary."


def test_stage_extract_intelligence_forwards_eager_loaded_segments(monkeypatch):
    worker = _worker()
    segments = [
        SimpleNamespace(
            start_time=0.149, end_time=1.5, text="buen trabajo", speaker="Bob", id=1
        )
    ]
    meeting = _title_meeting(meeting_type="generic", segments=segments)
    claims, completed, saves = _timed_stage_common(monkeypatch, meeting)
    begins = []
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: begins.append(kwargs) or True,
    )
    completes = []
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_intelligence_for_attempt",
        lambda *args, **kwargs: completes.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output",
        lambda transcript, meeting_type: {"meeting_type": meeting_type},
    )
    requests = []
    meeting_intelligence = _install_sdk_parse(
        monkeypatch, requests, lambda: NUMERIC_TIMED_PAYLOAD
    )

    result = _run_stage(worker.stage_extract_intelligence, MEETING_ID)

    assert result == MEETING_ID
    (request,) = requests
    assert request["messages"][1]["content"] == "[0.149-1.5] Bob: buen trabajo"
    assert request["response_format"] is meeting_intelligence.HighlightsExtraction
    ((save_args, _save_kwargs),) = saves
    assert save_args[0] == MEETING_ID
    assert save_args[1] == meeting_intelligence.HighlightsExtraction.model_validate(
        NUMERIC_TIMED_PAYLOAD
    ).model_dump()
    assert begins[0]["dispatch_attempt_id"] == ATTEMPT_ID
    assert completes[0]["dispatch_attempt_id"] == ATTEMPT_ID
    # The intelligence claim is not stage-specific.
    assert claims[0]["meeting_id"] == MEETING_ID
    assert meeting.summary_text == "A durable meeting summary."


def test_timed_rejection_saves_no_highlights_and_completes_nothing(monkeypatch):
    worker = _worker()

    segments = [
        SimpleNamespace(
            start_time=0.149, end_time=1.5, text="buen trabajo", speaker="Bob", id=1
        )
    ]

    # A schema-valid response whose parsed attribute is null must still fail
    # on the timed path, without any save or completion side effect.
    requests = []
    _null_parsed_sdk_parse(monkeypatch, requests, NUMERIC_TIMED_PAYLOAD)
    completion_calls = []
    monkeypatch.setattr(
        worker.meeting_service,
        "complete_intelligence_for_attempt",
        lambda *args, **kwargs: completion_calls.append(kwargs) or True,
    )

    # Current split stage: claimed, rejected, and nothing persisted.
    meeting = _title_meeting(segments=segments)
    claims, completed, saves = _timed_stage_common(monkeypatch, meeting)
    with pytest.raises(
        ValueError, match="highlight extraction returned no parsed window"
    ):
        _run_stage(worker.stage_extract_highlights, MEETING_ID)
    assert len(requests) == 1
    assert saves == []
    assert completed == []
    assert claims and claims[0]["stage"] == "stage_extract_highlights"
    assert meeting.summary_text == "A durable meeting summary."

    # Legacy intelligence stage with the same eager-loaded timed segments:
    # the worker forwards them into the raw extract call, the null parsed
    # response is rejected, and nothing persists — no save, no completion,
    # no structured call (no partial result).
    requests.clear()
    legacy_meeting = _title_meeting(meeting_type="generic", segments=segments)
    legacy_claims, legacy_completed, legacy_saves = _timed_stage_common(
        monkeypatch, legacy_meeting
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_intelligence_for_attempt",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.meeting_intelligence.intelligence_service.extract_structured_output",
        lambda transcript, meeting_type: pytest.fail(
            "structured extraction must not run after a rejected highlight stage"
        ),
    )
    with pytest.raises(
        ValueError, match="highlight extraction returned no parsed window"
    ):
        _run_stage(worker.stage_extract_intelligence, MEETING_ID)
    assert len(requests) == 1
    assert legacy_saves == []
    assert legacy_completed == []
    assert completion_calls == []
