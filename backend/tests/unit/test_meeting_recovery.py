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
    claim_recovery_plan,
    dispatch_recovery_plan,
    is_stale_meeting,
)


NOW = datetime(2026, 9, 12, 12, 0, 0)
GRACE_SECONDS = 900


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
            "stage_extract_intelligence",
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
        ("stage_summarize", "stage_extract_intelligence"),
    )


def test_summary_with_incomplete_intelligence_dispatches_intelligence_only():
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
        ("stage_extract_intelligence",),
    )


def test_completed_structured_output_is_finalized_without_dispatch():
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
            "stage_extract_intelligence",
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
