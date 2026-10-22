# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Unit tests for durable-output meeting recovery decisions.

This module is intentionally independent of ``tests/conftest.py`` and the
real database/storage settings. Run it with ``--noconftest``.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.meeting_recovery import (
    build_recovery_plan,
    build_resume_plan,
    claim_recovery_plan,
    dispatch_recovery_plan,
    is_stale_meeting,
)


NOW = datetime(2026, 9, 12, 12, 0, 0)
GRACE_SECONDS = 900
FULL_CLOUD_REQUEST_TIMEOUT_SECONDS = 7200
FULL_CLOUD_RECOVERY_GRACE_SECONDS = 9000


def _meeting(**overrides):
    values = {
        "id": 456,
        "status": "processing",
        "source_type": "upload",
        "requested_language": None,
        "created_at": NOW - timedelta(seconds=GRACE_SECONDS + 1),
        "processing_heartbeat_at": None,
        "transcript_text": None,
        "transliterated_text": None,
        "summary_text": None,
        "structured_output": None,
        "structured_output_status": "pending",
        "visual_breakdown_status": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _dispatchers():
    return {
        "full": MagicMock(),
        "youtube": MagicMock(),
        "stages": MagicMock(),
        "finalize": MagicMock(),
    }


def _dispatch(meeting_id, plan, dispatchers):
    dispatch_recovery_plan(
        meeting_id,
        plan,
        dispatch_full_pipeline=dispatchers["full"],
        dispatch_youtube_pipeline=dispatchers["youtube"],
        dispatch_stages=dispatchers["stages"],
        finalize=dispatchers["finalize"],
    )


def test_stale_queued_meeting_dispatches_full_pipeline_once():
    meeting = _meeting(
        status="queued",
        transcript_text="old transcript",
        summary_text="old summary",
    )
    plan = claim_recovery_plan(
        meeting,
        now=NOW,
        grace_seconds=GRACE_SECONDS,
        acquire_claim=lambda: True,
    )
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    dispatchers["full"].assert_called_once_with(meeting.id)
    dispatchers["youtube"].assert_not_called()
    dispatchers["stages"].assert_not_called()


def test_fresh_processing_meeting_is_ignored():
    meeting = _meeting(processing_heartbeat_at=NOW - timedelta(seconds=1))
    claim = MagicMock(return_value=True)

    plan = claim_recovery_plan(
        meeting,
        now=NOW,
        grace_seconds=GRACE_SECONDS,
        acquire_claim=claim,
    )

    assert plan is None
    claim.assert_not_called()


def test_recovery_grace_requires_more_than_fifteen_minutes_without_heartbeat():
    fresh = _meeting(
        created_at=NOW - timedelta(seconds=GRACE_SECONDS),
    )
    stale = _meeting(
        created_at=NOW - timedelta(seconds=GRACE_SECONDS + 1),
    )

    assert not is_stale_meeting(
        fresh,
        now=NOW,
        grace_seconds=GRACE_SECONDS,
    )
    assert is_stale_meeting(
        stale,
        now=NOW,
        grace_seconds=GRACE_SECONDS,
    )


def test_full_cloud_long_request_claim_and_dispatch_wait_for_recovery_grace():
    live_request = _meeting(
        processing_heartbeat_at=NOW - timedelta(seconds=FULL_CLOUD_REQUEST_TIMEOUT_SECONDS),
    )
    live_claim = MagicMock(return_value=True)
    live_dispatchers = _dispatchers()

    live_plan = claim_recovery_plan(
        live_request,
        now=NOW,
        grace_seconds=FULL_CLOUD_RECOVERY_GRACE_SECONDS,
        acquire_claim=live_claim,
    )

    assert live_plan is None
    live_claim.assert_not_called()
    for dispatcher in live_dispatchers.values():
        dispatcher.assert_not_called()

    beyond_recovery_window = _meeting(
        processing_heartbeat_at=NOW - timedelta(seconds=FULL_CLOUD_RECOVERY_GRACE_SECONDS + 1),
    )
    stale_claim = MagicMock(return_value=True)
    stale_dispatchers = _dispatchers()

    stale_plan = claim_recovery_plan(
        beyond_recovery_window,
        now=NOW,
        grace_seconds=FULL_CLOUD_RECOVERY_GRACE_SECONDS,
        acquire_claim=stale_claim,
    )

    assert stale_plan is not None
    stale_claim.assert_called_once_with()
    _dispatch(beyond_recovery_window.id, stale_plan, stale_dispatchers)
    stale_dispatchers["full"].assert_called_once_with(beyond_recovery_window.id)
    stale_dispatchers["youtube"].assert_not_called()
    stale_dispatchers["stages"].assert_not_called()
    stale_dispatchers["finalize"].assert_not_called()

    assert not is_stale_meeting(
        live_request,
        now=NOW,
        grace_seconds=FULL_CLOUD_RECOVERY_GRACE_SECONDS,
    )
    assert is_stale_meeting(
        beyond_recovery_window,
        now=NOW,
        grace_seconds=FULL_CLOUD_RECOVERY_GRACE_SECONDS,
    )


def test_stale_processing_without_transcript_resumes_from_download():
    meeting = _meeting(status="processing", source_type="youtube")
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "full_pipeline"
    dispatchers["youtube"].assert_called_once_with(meeting.id)
    dispatchers["full"].assert_not_called()


def test_transcript_without_summary_resumes_the_missing_tail():
    meeting = _meeting(
        transcript_text="durable transcript",
        requested_language="urdu_roman",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "tail_pipeline"
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_transliterate",
            "stage_optional_visual_breakdown",
            "stage_summarize",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_transcript_without_summary_does_not_reopen_terminal_visual_work():
    meeting = _meeting(
        transcript_text="durable transcript",
        visual_breakdown_status="completed",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_summarize",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_summary_with_incomplete_intelligence_dispatches_intelligence_and_embedding():
    meeting = _meeting(
        transcript_text="durable transcript",
        summary_text="durable summary",
        structured_output_status="processing",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "intelligence"
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_completed_structured_output_dispatches_embedding_finalizer():
    meeting = _meeting(
        transcript_text="durable transcript",
        summary_text="durable summary",
        structured_output={"topics": []},
        structured_output_status="completed",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "finalize"
    dispatchers["finalize"].assert_called_once_with(meeting.id)
    dispatchers["full"].assert_not_called()
    dispatchers["youtube"].assert_not_called()
    dispatchers["stages"].assert_not_called()


def test_completed_intelligence_with_active_visual_runs_visual_then_embedding():
    meeting = _meeting(
        transcript_text="durable transcript",
        summary_text="durable summary",
        structured_output={"topics": []},
        structured_output_status="completed",
        visual_breakdown_status="processing",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "visual_then_finalize"
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        ("stage_optional_visual_breakdown", "stage_embedding"),
    )


def test_stale_processing_visual_epoch_is_invalidated_and_requeued():
    meeting = _meeting(
        transcript_text="durable transcript",
        visual_breakdown_status="processing",
    )
    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()

    _dispatch(meeting.id, plan, dispatchers)

    assert plan.reset_visual_epoch is True
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_optional_visual_breakdown",
            "stage_summarize",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_active_heartbeat_or_recovery_claim_prevents_duplicate_recovery():
    meeting = _meeting()
    claim = MagicMock(return_value=False)

    plan = claim_recovery_plan(
        meeting,
        now=NOW,
        grace_seconds=GRACE_SECONDS,
        acquire_claim=claim,
    )

    assert plan is None
    claim.assert_called_once_with()


def test_terminal_completed_and_failed_rows_are_ignored():
    for status in ("completed", "failed"):
        meeting = _meeting(status=status)
        assert not is_stale_meeting(
            meeting,
            now=NOW,
            grace_seconds=GRACE_SECONDS,
        )
        assert build_recovery_plan(meeting) is None

def _failed_meeting(**overrides):
    values = {"status": "failed"}
    values.update(overrides)
    return _meeting(**values)


def test_failed_meeting_resumes_only_the_stages_after_a_durable_transcript():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="[Error: provider failed]",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(meeting)
    dispatchers = _dispatchers()
    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "tail_pipeline"
    assert plan.include_summary is True
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_summarize",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_failed_meeting_without_transcript_restarts_the_full_pipeline():
    meeting = _failed_meeting(transcript_text=None)

    plan = build_resume_plan(meeting)

    assert plan.action == "full_pipeline"


def test_failed_meeting_with_completed_intelligence_only_finalizes():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="durable summary",
        structured_output={"ok": True},
        structured_output_status="completed",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(meeting)
    dispatchers = _dispatchers()
    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "finalize"
    dispatchers["finalize"].assert_called_once_with(meeting.id)


def test_failed_meeting_does_not_reopen_a_failed_visual_stage():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="[Error: provider failed]",
        visual_breakdown_status="failed",
    )

    plan = build_resume_plan(meeting)

    assert plan.action == "tail_pipeline"
    assert plan.include_visual is False


def test_failed_meeting_regenerates_a_placeholder_summary():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="Summary could not be generated.",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(meeting)

    assert plan.action == "tail_pipeline"
    assert plan.include_summary is True


def test_failed_meeting_with_no_markers_reruns_title_and_highlights():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="A real summary",
        structured_output_status="pending",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(meeting)

    assert plan.action == "intelligence"
    assert plan.include_infer_title is True
    assert plan.include_highlights is True


def test_resume_only_reruns_the_failed_structured_step_after_completed_steps():
    """Title and highlight completions (including zero highlights) are reused."""
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="A real summary",
        structured_output_status="pending",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(
        meeting,
        previous_run_id=99,
        previous_completed_stages=("stage_infer_title", "stage_extract_highlights"),
    )
    dispatchers = _dispatchers()
    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "intelligence"
    assert plan.resume_lineage is True
    assert plan.previous_run_id == 99
    assert plan.include_infer_title is False
    assert plan.include_highlights is False
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        ("stage_extract_structured_output", "stage_embedding"),
    )


def test_resume_with_completed_title_and_uncompleted_highlights_keeps_title_only():
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="A real summary",
        structured_output_status="pending",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(
        meeting,
        previous_run_id=99,
        previous_completed_stages=("stage_infer_title",),
    )
    dispatchers = _dispatchers()
    _dispatch(meeting.id, plan, dispatchers)

    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_resume_of_a_regenerated_summary_retitles_and_keeps_saved_highlights():
    """A fresh summary has no title yet, but saved highlights are not repeated."""
    meeting = _failed_meeting(
        transcript_text="durable transcript",
        summary_text="[Error: provider failed]",
        visual_breakdown_status="completed",
    )

    plan = build_resume_plan(
        meeting,
        previous_run_id=99,
        previous_completed_stages=("stage_infer_title", "stage_extract_highlights"),
    )

    assert plan.action == "tail_pipeline"
    assert plan.include_summary is True
    assert plan.include_infer_title is True
    assert plan.include_highlights is False


def test_reused_stages_list_completed_work_the_resume_will_not_repeat():
    from app.services.meeting_recovery import (
        RESUME_CARRYABLE_STAGES,
        MeetingRecoveryPlan,
    )

    plan = MeetingRecoveryPlan(
        action="intelligence",
        include_infer_title=False,
        include_highlights=False,
        resume_lineage=True,
        previous_run_id=99,
        previous_completed_stages=(
            "stage_summarize",
            "stage_infer_title",
            "stage_extract_highlights",
        ),
    )

    assert set(plan.reused_stages()) == {
        "stage_summarize",
        "stage_infer_title",
        "stage_extract_highlights",
    }
    assert "stage_extract_structured_output" not in plan.reused_stages()
    assert RESUME_CARRYABLE_STAGES >= {
        "stage_summarize",
        "stage_infer_title",
        "stage_extract_highlights",
        "stage_extract_structured_output",
    }


def test_completed_stage_markers_from_a_new_transcript_epoch_are_not_reused():
    from app.services.meeting_recovery import valid_resume_completed_stages

    events = [
        SimpleNamespace(
            stage="stage_infer_title",
            metadata_={"transcript_fingerprint": "old-epoch-hash"},
        )
    ]

    assert valid_resume_completed_stages(events, transcript_text="new transcript") == ()
    assert valid_resume_completed_stages(events, transcript_text=None) == ()


def test_valid_resume_markers_survive_same_epoch_but_not_no_source_skips():
    from app.services.meeting_recovery import (
        transcript_fingerprint,
        valid_resume_completed_stages,
    )

    transcript = "same durable transcript"
    fingerprint = transcript_fingerprint(transcript)
    assert fingerprint is not None and fingerprint != transcript
    real_completed = {"event_type": "completed", "status": "completed"}
    same_epoch = valid_resume_completed_stages(
        [
            SimpleNamespace(
                stage="stage_extract_highlights",
                metadata_={"transcript_fingerprint": fingerprint},
                **real_completed,
            ),
            SimpleNamespace(
                stage="stage_infer_title",
                metadata_={
                    "transcript_fingerprint": fingerprint,
                    "skipped_no_source": True,
                },
                **real_completed,
            ),
            SimpleNamespace(
                stage="stage_summarize",
                metadata_={},
                **real_completed,
            ),
            SimpleNamespace(
                stage="stage_extract_structured_output",
                metadata_={"transcript_fingerprint": "other"},
                **real_completed,
            ),
        ],
        transcript_text=transcript,
    )

    assert "stage_extract_highlights" in same_epoch
    assert "stage_infer_title" not in same_epoch
    assert "stage_summarize" not in same_epoch
    assert "stage_extract_structured_output" not in same_epoch


def _lineage_session(meeting, previous_run, events):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, object_id):
            return meeting

        def exec(self, statement):
            return SimpleNamespace(
                first=lambda: previous_run,
                all=lambda: list(events),
            )

    return FakeSession()


def test_previous_resume_lineage_trusts_only_the_immediately_previous_run(monkeypatch):
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = __import__("app.services.meeting_recovery", fromlist=["x"]).transcript_fingerprint(transcript)
    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text=transcript)
    previous_run = SimpleNamespace(
        id=99, owner_id=7, status="failed", dispatch_attempt_id="attempt-a"
    )
    events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-a",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            id=2,
            stage="stage_extract_highlights",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-a",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            id=3,
            stage="stage_extract_structured_output",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-a",
            metadata_={"transcript_fingerprint": "other"},
        ),
    ]
    monkeypatch.setattr(audit_module, "Session", lambda engine: _lineage_session(meeting, previous_run, events))

    previous_run_id, completed = audit_module.meeting_processing_audit.previous_resume_lineage(
        456, 300, transcript_text=transcript
    )

    assert previous_run_id == 99
    assert set(completed) == {"stage_infer_title", "stage_extract_highlights"}


def test_previous_resume_lineage_rejects_an_owner_mismatch_or_missing_previous(monkeypatch):
    import app.services.meeting_recovery as recovery
    import app.services.meeting_processing_audit as audit_module

    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text="words")
    foreign_run = SimpleNamespace(id=99, owner_id=8, status="failed")
    monkeypatch.setattr(
        audit_module, "Session", lambda engine: _lineage_session(meeting, foreign_run, [])
    )

    assert audit_module.meeting_processing_audit.previous_resume_lineage(
        456, 300, transcript_text="words"
    ) == (None, ())

    monkeypatch.setattr(
        audit_module, "Session", lambda engine: _lineage_session(meeting, None, [])
    )
    assert recovery.transcript_fingerprint(None) is None


def test_carry_forward_copies_valid_completed_markers_as_reused(monkeypatch):
    import app.services.meeting_recovery as recovery
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = recovery.transcript_fingerprint(transcript)
    meeting = SimpleNamespace(
        id=456,
        owner_id=7,
        transcript_text=transcript,
        current_processing_run_id=300,
    )
    new_run = SimpleNamespace(
        id=300, meeting_id=456, owner_id=7, status="running", dispatch_attempt_id="attempt-c"
    )
    source_run = SimpleNamespace(
        id=99, meeting_id=456, owner_id=7, status="failed", dispatch_attempt_id="attempt-p"
    )
    source_events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-p",
            metadata_={"transcript_fingerprint": fingerprint, "resume_origin_run_id": 50},
        ),
        SimpleNamespace(
            id=2,
            stage="stage_extract_highlights",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-p",
            metadata_={"transcript_fingerprint": "other"},
        ),
    ]
    added = []

    class FakeSession:
        def __enter__(self):
            self._state = {"firsts": [meeting, new_run], "all_calls": 0}
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            added.append(item)

        def commit(self):
            pass

        def exec(self, statement):
            state = self._state
            def first():
                return state["firsts"].pop(0) if state["firsts"] else None
            def all():
                state["all_calls"] += 1
                return list(source_events) if state["all_calls"] == 1 else []
            return SimpleNamespace(first=first, all=all)

        def get(self, model, object_id):
            if object_id == 99:
                return source_run
            return None

    monkeypatch.setattr(audit_module, "Session", lambda engine: FakeSession())

    carried = audit_module.meeting_processing_audit.carry_forward_completed_stages(
        meeting_id=456,
        source_run_id=99,
        stages=("stage_infer_title", "stage_extract_highlights"),
    )

    assert carried == ("stage_infer_title",)
    assert len(added) == 1, [getattr(item, "stage", item) for item in added]
    copied = added[0]
    assert copied.run_id == 300
    assert copied.meeting_id == 456
    assert copied.dispatch_attempt_id == "attempt-c"
    assert copied.stage == "stage_infer_title"
    assert copied.event_type == "completed"
    assert copied.metadata_["reused"] is True
    assert copied.metadata_["resume_origin_run_id"] == 50
    assert copied.metadata_["transcript_fingerprint"] == fingerprint


def test_stale_active_visual_recovery_with_durable_summary_reinfers_the_title():
    """A durable summary with a stale active visual run keeps its summary but
    never assumes the split title stage was already covered."""
    meeting = _meeting(
        transcript_text="durable transcript",
        summary_text="durable summary",
        structured_output_status="pending",
        visual_breakdown_status="processing",
    )

    plan = build_recovery_plan(meeting)
    dispatchers = _dispatchers()
    _dispatch(meeting.id, plan, dispatchers)

    assert plan.action == "tail_pipeline"
    assert plan.include_summary is False
    assert plan.include_infer_title is True
    assert plan.include_visual is True
    dispatchers["stages"].assert_called_once_with(
        meeting.id,
        (
            "stage_optional_visual_breakdown",
            "stage_infer_title",
            "stage_extract_highlights",
            "stage_extract_structured_output",
            "stage_embedding",
        ),
    )


def test_valid_resume_completed_stages_rejects_non_completed_events_themselves():
    """Markers are trusted only as genuine completed events; a caller that
    forgets to pre-filter cannot turn a failed/pending event into a marker."""
    from app.services.meeting_recovery import (
        transcript_fingerprint,
        valid_resume_completed_stages,
    )

    transcript = "durable transcript"
    fingerprint = transcript_fingerprint(transcript)
    events = [
        SimpleNamespace(
            stage="stage_infer_title",
            event_type="failed",
            status="failed",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            stage="stage_extract_highlights",
            event_type=None,
            status="pending",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            stage="stage_extract_structured_output",
            event_type="completed",
            status="completed",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
    ]

    assert valid_resume_completed_stages(events, transcript_text=transcript) == (
        "stage_extract_structured_output",
    )


def _lineage_source_run(status, attempt_id):
    return SimpleNamespace(
        id=98,
        owner_id=7,
        status=status,
        dispatch_attempt_id=attempt_id,
    )


def _lineage_session_for_run(meeting, source_run, events):
    class FakeSession:
        def __init__(self):
            self._run_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, object_id):
            return meeting

        def exec(self, statement):
            if statement.is_select and "meetingprocessingrun" in str(
                statement.compile()
            ):
                self._run_calls += 1
                return SimpleNamespace(
                    first=lambda: source_run if self._run_calls == 1 else None,
                    all=lambda: [],
                )
            return SimpleNamespace(first=lambda: None, all=lambda: list(events))

    return FakeSession()


def test_previous_resume_lineage_selects_the_immediately_preceding_run_without_status_filters(monkeypatch):
    """The selection returns whatever run preceded the current one, and a
    cancelled neighbor is rejected instead of crossed into older markers."""
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = __import__(
        "app.services.meeting_recovery", fromlist=["x"]
    ).transcript_fingerprint(transcript)
    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text=transcript)
    source_run = _lineage_source_run("cancelled", "attempt-b")
    stale_events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-b",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
    ]
    monkeypatch.setattr(
        audit_module,
        "Session",
        lambda engine: _lineage_session_for_run(meeting, source_run, stale_events),
    )

    previous_run_id, completed = audit_module.meeting_processing_audit.previous_resume_lineage(
        456, 300, transcript_text=transcript
    )

    assert previous_run_id is None
    assert completed == ()


def test_previous_resume_lineage_never_prefilters_the_neighbor_status_in_sql(monkeypatch):
    """The run selection SQL carries no status predicate, so the database can
    never skip over a cancelled/active run to an older reusable one."""
    import app.services.meeting_processing_audit as audit_module

    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text="t")
    source_run = _lineage_source_run("cancelled", "attempt-b")
    fake_session = _lineage_session_for_run(meeting, source_run, [])
    monkeypatch.setattr(audit_module, "Session", lambda engine: fake_session)

    captured: list[object] = []

    original_exec = fake_session.exec

    def exec_capture(statement):
        captured.append(statement)
        return original_exec(statement)

    fake_session.exec = exec_capture  # type: ignore[method-assign]
    audit_module.meeting_processing_audit.previous_resume_lineage(
        456, 300, transcript_text="t"
    )

    compiled_selection = str(captured[0].compile())
    # The status column may appear in the SELECT list, but never as a WHERE
    # predicate on the neighbor selection.
    where_clause = compiled_selection.split("WHERE", 1)[-1]
    assert "meetingprocessingrun.status" not in where_clause, compiled_selection


def test_previous_resume_lineage_rejects_active_and_cancelled_neighbors(monkeypatch):
    """An active or cancelled immediately previous run is lineage poison: no
    markers from it, and the planner never skips it into older runs."""
    import app.services.meeting_processing_audit as audit_module

    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text="t")
    for status in ("queued", "running", "cancelled"):
        source_run = _lineage_source_run(status, "attempt-b")
        monkeypatch.setattr(
            audit_module,
            "Session",
            lambda engine: _lineage_session_for_run(meeting, source_run, []),
        )

        result = audit_module.meeting_processing_audit.previous_resume_lineage(
            456, 300, transcript_text="t"
        )

        assert result == (None, ()), status


def test_previous_resume_lineage_rejects_stale_attempt_events(monkeypatch):
    """Completed markers recorded under a different dispatch attempt than the
    selected previous run are not carried as lineage markers."""
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = __import__(
        "app.services.meeting_recovery", fromlist=["x"]
    ).transcript_fingerprint(transcript)
    meeting = SimpleNamespace(id=456, owner_id=7, transcript_text=transcript)
    source_run = _lineage_source_run("failed", "attempt-b")
    events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-a",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            id=2,
            stage="stage_extract_highlights",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-b",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
    ]
    monkeypatch.setattr(
        audit_module, "Session", lambda engine: _lineage_session_for_run(meeting, source_run, events)
    )

    previous_run_id, completed = audit_module.meeting_processing_audit.previous_resume_lineage(
        456, 300, transcript_text=transcript
    )

    assert previous_run_id == 98
    assert completed == ("stage_extract_highlights",)


def test_carry_forward_rejects_an_active_or_cancelled_source_run(monkeypatch):
    """Markers are copied only from a terminally failed/completed source run."""
    import app.services.meeting_recovery as recovery
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = recovery.transcript_fingerprint(transcript)
    meeting = SimpleNamespace(
        id=456,
        owner_id=7,
        transcript_text=transcript,
        current_processing_run_id=300,
    )
    new_run = SimpleNamespace(
        id=300, meeting_id=456, owner_id=7, status="running", dispatch_attempt_id="attempt-c"
    )
    source_events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-p",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
    ]

    class FakeSession:
        def __init__(self, source_run):
            self.source_run = source_run
            self.firsts = [meeting, new_run]
            self.added = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            pass

        def get(self, model, object_id):
            return self.source_run if object_id == 99 else None

        def exec(self, statement):
            def first():
                return self.firsts.pop(0) if self.firsts else None

            return SimpleNamespace(first=first, all=lambda: [])

    for status in ("queued", "running", "cancelled"):
        source_run = SimpleNamespace(
            id=99, meeting_id=456, owner_id=7, status=status, dispatch_attempt_id="attempt-p"
        )
        fake = FakeSession(source_run)
        monkeypatch.setattr(audit_module, "Session", lambda engine: fake)

        carried = audit_module.meeting_processing_audit.carry_forward_completed_stages(
            meeting_id=456,
            source_run_id=99,
            stages=("stage_infer_title",),
        )

        assert carried == (), status
        assert fake.added == [], status


def test_carry_forward_ignores_source_events_from_a_stale_attempt(monkeypatch):
    """Only markers recorded under the source run's own dispatch attempt are
    valid carry sources."""
    import app.services.meeting_recovery as recovery
    import app.services.meeting_processing_audit as audit_module

    transcript = "same durable transcript"
    fingerprint = recovery.transcript_fingerprint(transcript)
    meeting = SimpleNamespace(
        id=456,
        owner_id=7,
        transcript_text=transcript,
        current_processing_run_id=300,
    )
    new_run = SimpleNamespace(
        id=300, meeting_id=456, owner_id=7, status="running", dispatch_attempt_id="attempt-c"
    )
    source_run = SimpleNamespace(
        id=99, meeting_id=456, owner_id=7, status="failed", dispatch_attempt_id="attempt-p"
    )
    source_events = [
        SimpleNamespace(
            id=1,
            stage="stage_infer_title",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-old",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
        SimpleNamespace(
            id=2,
            stage="stage_extract_highlights",
            event_type="completed",
            status="completed",
            dispatch_attempt_id="attempt-p",
            metadata_={"transcript_fingerprint": fingerprint},
        ),
    ]
    added = []

    class FakeSession:
        def __enter__(self):
            self._state = {"firsts": [meeting, new_run], "all_calls": 0}
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            added.append(item)

        def commit(self):
            pass

        def exec(self, statement):
            state = self._state

            def first():
                return state["firsts"].pop(0) if state["firsts"] else None

            def all():
                state["all_calls"] += 1
                return list(source_events) if state["all_calls"] == 1 else []

            return SimpleNamespace(first=first, all=all)

        def get(self, model, object_id):
            return source_run if object_id == 99 else None

    monkeypatch.setattr(audit_module, "Session", lambda engine: FakeSession())

    carried = audit_module.meeting_processing_audit.carry_forward_completed_stages(
        meeting_id=456,
        source_run_id=99,
        stages=("stage_infer_title", "stage_extract_highlights"),
    )

    assert carried == ("stage_extract_highlights",)
    assert [item.stage for item in added] == ["stage_extract_highlights"]
