# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Hierarchical synthesis: bounded small groups of partials before the final pass.

When completed partial windows exceed the documented fixed group size
(`SYNTHESIS_GROUP_SIZE`, 4), the partials are merged deterministically, in
window order, into groups of at most 4 (last group may be smaller); each group
runs one bounded intermediate synthesis call plus one final synthesis over the
ordered intermediates. Partial counts at or below the group size keep the
single-synthesis flow byte-for-byte. Only the final output is returned;
intermediate text is never persisted and never returned.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.services.ai_agent import SYNTHESIS_GROUP_SIZE, summarize_context
from app.services.ai_provider import AIProviderError, OpenAICompatibleCompletionClient
from app.services.multimodal_context import ContextBuildResult, ContextChunk, ContextItem

_STYLE_EXAMPLE = "STYLE_EXAMPLE_PREFERRED_LAYOUT"
_TEMPLATE_BODY = "TEMPLATE_BODY_CUSTOM_LAYOUT"
_UPLOAD_DATE = "2026-01-15"


def _chunk_ns(content, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class StreamingSequencedStream:
    """One synthetic stream per summary call; the fake stays a single request."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return self

    def __next__(self):
        if not self._chunks:
            raise StopIteration
        event = self._chunks.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self):
        self.closed = True


class StreamingSequencedCompletions:
    """Return one scripted (content, finish_reason) stream per provider call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(dict(request))
        content, finish_reason = self.outcomes.pop(0)
        if isinstance(content, BaseException):
            raise content
        return StreamingSequencedStream(
            ([_chunk_ns(content)] if content else []) + [_chunk_ns(None, finish_reason)]
        )


def _client(outcomes):
    """Sequenced summary streams for the standard OpenAI summary transport."""
    completions = StreamingSequencedCompletions(outcomes)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAICompatibleCompletionClient(sdk, model="oc/glm-5.3-flash"), completions


def _window_outcomes(count: int) -> list:
    return [(f"Window {index} notes", "stop") for index in range(1, count + 1)]


def _context(chunk_count: int) -> ContextBuildResult:
    chunks = tuple(
        ContextChunk(
            index=index,
            start=float(index * 60),
            end=float((index + 1) * 60),
            items=(
                ContextItem(
                    source="spoken",
                    source_id=index + 1,
                    start=float(index * 60),
                    end=float((index + 1) * 60),
                    content=f"Spoken fact {index + 1}.",
                    relevance="high",
                    provenance="observed",
                    evidence_ref=f"spoken:{index + 1}",
                ),
            ),
            estimated_input_tokens=10,
            complete=True,
        )
        for index in range(chunk_count)
    )
    return ContextBuildResult(
        chunks=chunks,
        source_item_count=chunk_count,
        assigned_item_count=chunk_count,
        unassigned_items=(),
        completeness="complete",
        warning_codes=(),
    )


def _user(request: dict) -> str:
    return request["messages"][1]["content"]


def _system(request: dict) -> str:
    return request["messages"][0]["content"]


def _run(context, client, params: dict | None = None):
    progress: list[dict] = []
    result = summarize_context(
        context,
        completion_client=client,
        progress_callback=lambda **event: progress.append(event),
        **(params or {}),
    )
    return result, progress


def test_partials_above_the_group_size_run_deterministic_small_groups_then_one_final():
    """9 windows → fixed groups (4, 4, 1): three group calls, then the final."""
    outcomes = [
        *_window_outcomes(9),
        ("Intermediate group 1", "stop"),
        ("Intermediate group 2", "stop"),
        ("Intermediate group 3", "stop"),
        ("FINAL NOTES", "stop"),
    ]
    client, completions = _client(outcomes)

    result, progress = _run(_context(9), client)

    # Only the final synthesis output is returned; intermediate text is not.
    assert result == "FINAL NOTES"
    assert "Intermediate group" not in result
    assert len(completions.requests) == 9 + 3 + 1
    # Exactly one streamed request per call, no Zabt token cap.
    assert all(request["stream"] is True for request in completions.requests)
    assert all("max_tokens" not in request for request in completions.requests)
    # Group 1: windows 1-4; group 2: windows 5-8; group 3: window 9.
    for user, present, absent in (
        (_user(completions.requests[9]), ("PARTIAL WINDOW 1", "PARTIAL WINDOW 4"), ("PARTIAL WINDOW 5",)),
        (_user(completions.requests[10]), ("PARTIAL WINDOW 5", "PARTIAL WINDOW 8"), ("PARTIAL WINDOW 4", "PARTIAL WINDOW 9")),
        (_user(completions.requests[11]), ("PARTIAL WINDOW 9",), ("PARTIAL WINDOW 8",)),
    ):
        assert all(fragment in user for fragment in present)
        assert all(fragment not in user for fragment in absent)
    final_user = _user(completions.requests[12])
    for group_number in ("INTERMEDIATE GROUP 1", "INTERMEDIATE GROUP 2", "INTERMEDIATE GROUP 3"):
        assert group_number in final_user
    assert "PARTIAL WINDOW" not in final_user

    # Progress keeps the started/terminal shape with the new group phase.
    started = [(event["phase"], event["block_index"], event["block_total"]) for event in progress if event["outcome"] == "started"]
    assert started[:9] == [("partial_chunk_summary", index, 9) for index in range(1, 10)]
    assert started[9:] == [
        ("group_synthesis", 1, 3),
        ("group_synthesis", 2, 3),
        ("group_synthesis", 3, 3),
        ("summary_synthesis", 4, 4),
    ]
    terminal = [(event["phase"], event["outcome"]) for event in progress if event["outcome"] != "started"]
    assert terminal == [
        ("partial_chunk_summary", "completed")] * 9 + [
        ("group_synthesis", "completed")] * 3 + [("summary_synthesis", "completed")]
    correlations = {event["correlation_id"] for event in progress if event["phase"] != "partial_chunk_summary"}
    assert len(correlations) == 4
    assert all(len(value) == 32 for value in correlations)


def test_partial_count_at_the_group_threshold_keeps_the_single_synthesis():
    outcomes = [*_window_outcomes(SYNTHESIS_GROUP_SIZE), ("FINAL NOTES", "stop")]
    client, completions = _client(outcomes)

    result, progress = _run(_context(SYNTHESIS_GROUP_SIZE), client)

    assert result == "FINAL NOTES"
    assert len(completions.requests) == SYNTHESIS_GROUP_SIZE + 1
    assert not any(event["phase"] == "group_synthesis" for event in progress)
    final = progress[-1]
    assert final["phase"] == "summary_synthesis"
    assert final["block_index"] == SYNTHESIS_GROUP_SIZE + 1
    assert final["block_total"] == SYNTHESIS_GROUP_SIZE + 1
    final_user = _user(completions.requests[-1])
    assert "PARTIAL WINDOW 1" in final_user
    assert "PARTIAL WINDOW 4" in final_user
    assert "INTERMEDIATE GROUP" not in final_user


def test_partials_just_above_the_threshold_run_small_groups_then_the_final():
    outcomes = [
        *_window_outcomes(SYNTHESIS_GROUP_SIZE + 1),
        ("Intermediate group 1", "stop"),
        ("Intermediate group 2", "stop"),
        ("FINAL NOTES", "stop"),
    ]
    client, completions = _client(outcomes)

    result, progress = _run(_context(SYNTHESIS_GROUP_SIZE + 1), client)

    assert result == "FINAL NOTES"
    # Five windows → groups (4, 1): two group calls, then the final.
    assert len(completions.requests) == SYNTHESIS_GROUP_SIZE + 4
    final_user = _user(completions.requests[-1])
    assert "INTERMEDIATE GROUP 1" in final_user
    assert "PARTIAL WINDOW" not in final_user
    group_events = [event for event in progress if event["phase"] == "group_synthesis"]
    assert [(event["block_index"], event["block_total"], event["outcome"]) for event in group_events] == [
        (1, 2, "started"),
        (1, 2, "completed"),
        (2, 2, "started"),
        (2, 2, "completed"),
    ]
    final = progress[-1]
    assert (final["block_index"], final["block_total"]) == (3, 3)


def test_missing_window_warning_reaches_the_first_group_and_final_exactly_once():
    outcomes = [
        ("Window 1 notes", "stop"),
        ("", "length"),  # window 2 fails after its one streamed request
        ("Window 3 notes", "stop"),
        ("Window 4 notes", "stop"),
        ("Window 5 notes", "stop"),
        ("Window 6 notes", "stop"),
        ("Intermediate group 1", "stop"),
        ("Intermediate group 2", "stop"),
        ("FINAL NOTES", "stop"),
    ]
    client, completions = _client(outcomes)

    result, progress = _run(_context(6), client)

    assert result == "FINAL NOTES"
    # Five surviving partials → groups (4, 1); the failed window cost one request.
    assert len(completions.requests) == 6 + 2 + 1
    first_group_user = _user(completions.requests[6])
    assert first_group_user.count("MISSING TIME WINDOWS") == 1
    assert "window(s) 2" in first_group_user
    later_group_user = _user(completions.requests[7])
    assert "MISSING TIME WINDOWS" not in later_group_user
    final_user = _user(completions.requests[8])
    assert final_user.count("MISSING TIME WINDOWS") == 1
    assert "window(s) 2" in final_user


def test_failed_group_discloses_its_gap_once_and_the_rest_still_synthesize():
    outcomes = [
        *_window_outcomes(6),
        ("Intermediate group 1", "stop"),
        ("", "length"),  # group 2 fails after its one streamed request
        ("FINAL NOTES", "stop"),
    ]
    client, completions = _client(outcomes)

    result, progress = _run(_context(6), client)

    assert result == "FINAL NOTES"
    assert len(completions.requests) == 6 + 2 + 1
    final_user = _user(completions.requests[-1])
    assert final_user.count("MISSING SYNTHESIS GROUPS") == 1
    assert "intermediate group(s) 2" in final_user
    assert "INTERMEDIATE GROUP 2" not in final_user
    failed = [
        event
        for event in progress
        if event["phase"] == "group_synthesis" and event["outcome"] == "failed"
    ]
    assert [(event["block_index"], event["error_class"]) for event in failed] == [
        (2, "AIProviderError")
    ]


def test_all_groups_failing_fails_closed_without_a_final_synthesis():
    outcomes = [*_window_outcomes(5), ("", "length"), ("", "length")]
    client, completions = _client(outcomes)

    with pytest.raises(AIProviderError):
        _run(_context(5), client)

    # Five windows plus the two failed group calls; no final synthesis ran.
    assert len(completions.requests) == 7


def test_style_template_and_date_stay_off_the_group_calls_and_on_the_final():
    outcomes = [
        *_window_outcomes(6),
        ("Intermediate group 1", "stop"),
        ("Intermediate group 2", "stop"),
        ("FINAL NOTES", "stop"),
    ]
    client, completions = _client(outcomes)

    _run(
        _context(6),
        client,
        {
            "style_examples": [_STYLE_EXAMPLE],
            "template_body": _TEMPLATE_BODY,
            "upload_date": _UPLOAD_DATE,
            "output_language": "es",
        },
    )

    for request in completions.requests[6:8]:
        group_system = _system(request)
        # The final report prompt and selected formatting stay synthesis-only.
        assert "expert meeting note-taker" not in group_system
        assert _STYLE_EXAMPLE not in group_system
        assert _TEMPLATE_BODY not in group_system
        # Evidence rules, the upload date, and the requested language still ride
        # each intermediate call, like every partial call does.
        assert "## Evidence rules" in group_system
        assert _UPLOAD_DATE in group_system
        assert "MANDATORY OUTPUT LANGUAGE" in group_system
        assert "Spanish" in group_system
    final_system = _system(completions.requests[-1])
    assert _STYLE_EXAMPLE in final_system
    assert _TEMPLATE_BODY in final_system
    assert _UPLOAD_DATE in final_system
    assert "MANDATORY OUTPUT LANGUAGE" in final_system
