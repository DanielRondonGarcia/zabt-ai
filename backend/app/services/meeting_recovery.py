# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Pure recovery planning for orphaned meeting pipeline work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from app.services.transcript_fingerprint import transcript_fingerprint


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
    include_infer_title: bool = False
    include_highlights: bool = False
    resume_lineage: bool = False
    previous_run_id: int | None = None
    previous_completed_stages: tuple[str, ...] = ()

    def dispatch_stage_names(self) -> tuple[str, ...]:
        """Ordered split-stage names this plan dispatches after its output head."""
        if self.action == "tail_pipeline":
            names: list[str] = []
            if self.include_transliteration:
                names.append("stage_transliterate")
            if self.include_visual:
                names.append("stage_optional_visual_breakdown")
            if self.include_summary:
                names.append("stage_summarize")
            if self.include_infer_title:
                names.append("stage_infer_title")
            if self.include_highlights:
                names.append("stage_extract_highlights")
            names.extend(("stage_extract_structured_output", "stage_embedding"))
            return tuple(names)
        if self.action == "intelligence":
            names = []
            if self.include_infer_title:
                names.append("stage_infer_title")
            if self.include_highlights:
                names.append("stage_extract_highlights")
            names.extend(("stage_extract_structured_output", "stage_embedding"))
            return tuple(names)
        if self.action == "visual_then_finalize":
            return ("stage_optional_visual_breakdown", "stage_embedding")
        return ()

    def reused_stages(self) -> frozenset[str]:
        """Resumable stages whose completed work this plan reuses, not repeats."""
        dispatched = set(self.dispatch_stage_names())
        return frozenset(
            stage
            for stage in RESUME_CARRYABLE_STAGES
            if stage in self.previous_completed_stages and stage not in dispatched
        )



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
        # A regenerated summary is untitled: re-infer the title with it.
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_transliteration=include_transliteration,
            include_visual=visual_needed,
            include_summary=True,
            include_infer_title=True,
            include_highlights=True,
            reset_visual_epoch=reset_visual,
        )

    # A stale active visual run must be fenced and completed before intelligence
    # is finalized, and an already durable summary must not be regenerated for
    # it. The title is not assumed done inside the summary: on this recovery
    # path no completed title marker is proven, so the split title stage runs
    # with the fenced visual work instead.
    if visual_active:
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_visual=True,
            include_summary=False,
            include_infer_title=True,
            include_highlights=True,
            reset_visual_epoch=reset_visual,
        )

    return MeetingRecoveryPlan(action="intelligence", include_highlights=True)


PLACEHOLDER_SUMMARIES = frozenset({"summary could not be generated."})
# A stage that already failed is not re-run by a resume: the user can request
# that work explicitly, and repeating it is exactly what resume must avoid.
RESUME_VISUAL_CLOSED_STATUSES = VISUAL_BREAKDOWN_TERMINAL_STATUSES | {
    "failed",
    "cancelled",
}


def summary_is_usable(summary_text: Any) -> bool:
    """Return whether a stored summary is real content, not a failure marker."""
    if not isinstance(summary_text, str):
        return False
    normalized = summary_text.strip().lower()
    if not normalized or normalized in PLACEHOLDER_SUMMARIES:
        return False
    return not normalized.startswith("[error:")


# Split model stages whose durable completed markers a resume may carry into a
# new run. Marker reuse is gated on the transcript epoch fingerprint recorded
# at completion time.
RESUME_CARRYABLE_STAGES = frozenset({
    "stage_summarize",
    "stage_infer_title",
    "stage_extract_highlights",
    "stage_extract_structured_output",
})


def valid_resume_completed_stages(
    events: Iterable[Any],
    *,
    transcript_text: Any,
) -> tuple[str, ...]:
    """Completed resumable markers that still match the current transcript epoch.

    An event is trusted only when this function itself sees it as a genuine
    completed marker (``event_type`` and ``status`` both "completed"), it
    names a resumable stage, it describes a genuine saved output (not a
    no-source skip), and it was produced from the exact current transcript
    content. Callers may still pre-filter by ``event_type``, but a
    hand-constructed iterable is never trusted on that basis alone.
    """
    fingerprint = transcript_fingerprint(transcript_text)
    if fingerprint is None:
        return ()
    trusted: set[str] = set()
    for event in events:
        if getattr(event, "event_type", None) != "completed":
            continue
        if getattr(event, "status", None) != "completed":
            continue
        stage = getattr(event, "stage", None)
        if stage not in RESUME_CARRYABLE_STAGES:
            continue
        metadata = getattr(event, "metadata_", None) or {}
        if metadata.get("transcript_fingerprint") != fingerprint:
            continue
        if metadata.get("skipped_no_source") is True:
            continue
        trusted.add(stage)
    return tuple(sorted(trusted))


def build_resume_plan(
    meeting: Any,
    *,
    previous_run_id: int | None = None,
    previous_completed_stages: tuple[str, ...] = (),
) -> MeetingRecoveryPlan:
    """Plan a failed-meeting resume from the outputs that are already durable.

    ``previous_completed_stages`` must come from the immediately previous
    same-owner/same-source run of this resume lineage (see
    ``valid_resume_completed_stages``); markers from any other run are never
    trusted here. Explicit full-pipeline restarts and resummarize runs start
    fresh generations, so they naturally do not carry older markers.
    """
    lineage = {
        "resume_lineage": True,
        "previous_run_id": previous_run_id,
        "previous_completed_stages": tuple(previous_completed_stages),
    }
    visual_status = getattr(meeting, "visual_breakdown_status", None)
    visual_needed = visual_status not in RESUME_VISUAL_CLOSED_STATUSES
    reset_visual = visual_status == "processing"
    source_type = getattr(meeting, "source_type", None)

    if not getattr(meeting, "transcript_text", None):
        return MeetingRecoveryPlan(
            action="full_pipeline",
            source_type=source_type,
            reset_visual_epoch=reset_visual,
            **lineage,
        )

    structured_complete = (
        getattr(meeting, "structured_output_status", None) == "completed"
        and getattr(meeting, "structured_output", None) is not None
    )
    if structured_complete:
        if visual_needed:
            return MeetingRecoveryPlan(
                action="visual_then_finalize",
                include_visual=True,
                reset_visual_epoch=reset_visual,
                **lineage,
            )
        return MeetingRecoveryPlan(action="finalize", **lineage)

    include_transliteration = bool(getattr(meeting, "requested_language", None)) and not bool(
        getattr(meeting, "transliterated_text", None)
    )

    if not summary_is_usable(getattr(meeting, "summary_text", None)):
        # A regenerated summary has no title yet: re-run the title stage with
        # it, but do not repeat highlights already saved from this transcript.
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_transliteration=include_transliteration,
            include_visual=visual_needed,
            include_summary=True,
            include_infer_title=True,
            include_highlights="stage_extract_highlights"
            not in previous_completed_stages,
            reset_visual_epoch=reset_visual,
            **lineage,
        )

    kept_completed = {
        "stage_infer_title",
        "stage_extract_highlights",
        "stage_extract_structured_output",
    } & set(previous_completed_stages)
    if visual_needed:
        return MeetingRecoveryPlan(
            action="tail_pipeline",
            include_transliteration=include_transliteration,
            include_visual=True,
            include_summary=False,
            include_infer_title="stage_infer_title" not in kept_completed,
            include_highlights="stage_extract_highlights" not in kept_completed,
            reset_visual_epoch=reset_visual,
            **lineage,
        )

    return MeetingRecoveryPlan(
        action="intelligence",
        include_infer_title="stage_infer_title" not in kept_completed,
        include_highlights="stage_extract_highlights" not in kept_completed,
        **lineage,
    )


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
        dispatch_stages(meeting_id, plan.dispatch_stage_names())
        return

    if plan.action == "intelligence":
        dispatch_stages(meeting_id, plan.dispatch_stage_names())
        return

    if plan.action == "visual_then_finalize":
        dispatch_stages(meeting_id, plan.dispatch_stage_names())
        return

    if plan.action == "finalize":
        finalize(meeting_id)
        return

    raise ValueError(f"Unsupported meeting recovery action: {plan.action}")
