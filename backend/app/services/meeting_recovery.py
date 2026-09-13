# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Pure recovery planning for orphaned meeting pipeline work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable


VISUAL_BREAKDOWN_ACTIVE_STATUSES = frozenset({"queued", "processing"})
VISUAL_BREAKDOWN_TERMINAL_STATUSES = frozenset({"completed", "skipped", "fallback"})


@dataclass(frozen=True)
class MeetingRecoveryPlan:
    """The earliest safe pipeline action for one stale meeting row."""

    action: str
    source_type: str | None = None
    include_transliteration: bool = False
    include_visual: bool = False
    include_summary: bool = False
    reset_visual_epoch: bool = False


def is_stale_meeting(
    meeting: Any,
    *,
    now: datetime,
    grace_seconds: int,
) -> bool:
    """Return whether a queued/processing row has exceeded its liveness grace."""
    if getattr(meeting, "status", None) not in {"queued", "processing"}:
        return False

    last_seen = getattr(meeting, "processing_heartbeat_at", None) or getattr(
        meeting, "created_at", None
    )
    if last_seen is None:
        return True
    return last_seen < now - timedelta(seconds=grace_seconds)


def build_recovery_plan(meeting: Any) -> MeetingRecoveryPlan | None:
    """Select a durable-output-based recovery path without adding a state."""
    status = getattr(meeting, "status", None)
    if status not in {"queued", "processing"}:
        return None

    visual_status = getattr(meeting, "visual_breakdown_status", None)
    visual_active = visual_status in VISUAL_BREAKDOWN_ACTIVE_STATUSES
    visual_needed = visual_status not in VISUAL_BREAKDOWN_TERMINAL_STATUSES
    reset_visual = visual_status == "processing"

    # A queued row always means re-transcription semantics, even if a previous
    # attempt left transcript text behind.
    if status == "queued":
        return MeetingRecoveryPlan(
            action="full_pipeline",
            source_type=getattr(meeting, "source_type", None),
            reset_visual_epoch=reset_visual,
        )

    structured_complete = (
        getattr(meeting, "structured_output_status", None) == "completed"
        and getattr(meeting, "structured_output", None) is not None
    )
    if structured_complete:
        if visual_active:
            return MeetingRecoveryPlan(
                action="visual_then_finalize",
                include_visual=True,
                reset_visual_epoch=reset_visual,
            )
        return MeetingRecoveryPlan(action="finalize")

    if not getattr(meeting, "transcript_text", None):
        return MeetingRecoveryPlan(
            action="full_pipeline",
            source_type=getattr(meeting, "source_type", None),
            reset_visual_epoch=reset_visual,
        )

    # Transliteration is cheap and idempotent, but only needs to be included
    # when its durable output is absent. The stage itself no-ops when the
    # requested language has no transliteration pair.
    include_transliteration = bool(getattr(meeting, "requested_language", None)) and not bool(
        getattr(meeting, "transliterated_text", None)
    )

    if not getattr(meeting, "summary_text", None):
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_transliteration=include_transliteration,
            include_visual=visual_needed,
            include_summary=True,
            reset_visual_epoch=reset_visual,
        )

    # A stale active visual run must be fenced and completed before intelligence
    # is finalized, but an already durable summary must not be regenerated.
    if visual_active:
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_visual=True,
            include_summary=False,
            reset_visual_epoch=reset_visual,
        )

    return MeetingRecoveryPlan(action="intelligence")


def claim_recovery_plan(
    meeting: Any,
    *,
    now: datetime,
    grace_seconds: int,
    acquire_claim: Callable[[], bool],
) -> MeetingRecoveryPlan | None:
    """Return a plan only after liveness and the external claim both succeed."""
    if not is_stale_meeting(
        meeting,
        now=now,
        grace_seconds=grace_seconds,
    ):
        return None
    if not acquire_claim():
        return None
    return build_recovery_plan(meeting)


def dispatch_recovery_plan(
    meeting_id: int,
    plan: MeetingRecoveryPlan,
    *,
    dispatch_full_pipeline: Callable[[int], None],
    dispatch_youtube_pipeline: Callable[[int], None],
    dispatch_stages: Callable[[int, tuple[str, ...]], None],
    finalize: Callable[[int], None],
) -> None:
    """Translate a plan into dispatch intents without depending on Celery."""
    if plan.action == "full_pipeline":
        if plan.source_type == "youtube":
            dispatch_youtube_pipeline(meeting_id)
        else:
            dispatch_full_pipeline(meeting_id)
        return

    if plan.action == "tail_pipeline":
        stages: list[str] = []
        if plan.include_transliteration:
            stages.append("stage_transliterate")
        if plan.include_visual:
            stages.append("stage_optional_visual_breakdown")
        if plan.include_summary:
            stages.append("stage_summarize")
        stages.append("stage_extract_intelligence")
        dispatch_stages(meeting_id, tuple(stages))
        return

    if plan.action == "intelligence":
        dispatch_stages(meeting_id, ("stage_extract_intelligence",))
        return

    if plan.action == "visual_then_finalize":
        dispatch_stages(
            meeting_id,
            ("stage_optional_visual_breakdown", "finalize_recovered_meeting"),
        )
        return

    if plan.action == "finalize":
        finalize(meeting_id)
        return

    raise ValueError(f"Unsupported meeting recovery action: {plan.action}")
