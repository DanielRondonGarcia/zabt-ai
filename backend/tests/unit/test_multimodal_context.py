# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Unit contracts for bounded multimodal context construction."""

from dataclasses import FrozenInstanceError

import pytest

from app.services.multimodal_context import (
    ContextBuilder,
    ContextItem,
    build_context,
    estimate_input_tokens,
    intervals_overlap,
)


def _spoken(
    source_id: int,
    start: float,
    end: float,
    text: str,
    **extra,
) -> dict:
    return {
        "id": source_id,
        "start_time": start,
        "end_time": end,
        "text": text,
        **extra,
    }


def _visual(
    source_id: int,
    start: float,
    end: float,
    caption: str,
    **extra,
) -> dict:
    return {
        "id": source_id,
        "start_time": start,
        "end_time": end,
        "caption": caption,
        "confidence": 0.9,
        **extra,
    }


def test_overlap_is_bidirectional_and_half_open():
    assert intervals_overlap(2.0, 4.0, 1.0, 10.0)
    assert intervals_overlap(1.0, 10.0, 2.0, 4.0)
    assert not intervals_overlap(0.0, 1.0, 1.0, 2.0)
    assert not intervals_overlap(1.0, 2.0, 0.0, 1.0)


def test_context_items_are_frozen_and_keep_provenance_and_references():
    result = build_context(
        [_spoken(7, 1.0, 4.0, "The owner will review the dashboard.", speaker="A")],
        [
            _visual(
                12,
                2.0,
                3.5,
                "Dashboard with an unresolved warning",
                provenance="inferred",
                uncertainty="The owner is not visible.",
            )
        ],
    )

    items = [item for chunk in result.chunks for item in chunk.items]
    spoken = next(item for item in items if item.source == "spoken")
    visual = next(item for item in items if item.source == "visual")

    assert spoken.source_id == 7
    assert (spoken.start, spoken.end) == (1.0, 4.0)
    assert spoken.speaker == "A"
    assert spoken.provenance == "observed"
    assert spoken.evidence_ref == "spoken:7"
    assert visual.source_id == 12
    assert visual.provenance == "inferred"
    assert visual.uncertainty == "The owner is not visible."
    assert visual.evidence_ref == "visual:12"
    assert visual.relevance == "high"

    with pytest.raises(FrozenInstanceError):
        spoken.content = "mutated"


def test_repeated_visual_evidence_is_emitted_once_across_adjacent_windows():
    result = build_context(
        [],
        [
            _visual(1, 1.0, 3.0, "Camera view of the speaker"),
            _visual(2, 61.0, 63.0, "Camera view of the speaker"),
            _visual(3, 121.0, 123.0, "Camera view of the speaker"),
        ],
        chunk_seconds=60,
    )

    visuals = [
        item
        for chunk in result.chunks
        for item in chunk.items
        if item.source == "visual"
    ]
    assert [item.source_id for item in visuals] == [1]
    assert visuals[0].relevance == "low"
    assert result.source_item_count == 3
    assert result.assigned_item_count == 1


def test_meaningful_visual_change_is_not_deduplicated():
    result = build_context(
        [],
        [
            _visual(1, 1.0, 3.0, "Dashboard"),
            _visual(2, 61.0, 63.0, "Dashboard", meaningful_change=True),
        ],
        chunk_seconds=60,
    )

    visuals = [
        item
        for chunk in result.chunks
        for item in chunk.items
        if item.source == "visual"
    ]
    assert [item.source_id for item in visuals] == [1, 2]


def test_hierarchical_budget_splits_spoken_items_without_losing_source_identity():
    spoken = _spoken(
        21,
        0.0,
        20.0,
        "one two three four five six seven eight nine ten eleven twelve",
    )
    result = ContextBuilder(chunk_seconds=60, max_input_tokens=2).build([spoken], [])
    items = [item for chunk in result.chunks for item in chunk.items]

    fragments = [item for item in items if item.source_id == 21]
    assert len(fragments) > 1
    assert [item.fragment_index for item in fragments] == list(range(len(fragments)))
    assert all(estimate_input_tokens(item.content) <= 2 for item in fragments)
    assert all((item.start, item.end) == (0.0, 20.0) for item in fragments)
    assert result.completeness == "complete"
    assert result.unassigned_items == ()
    assert result.source_item_count == 1
    assert result.assigned_item_count == 1


def test_oversized_visual_is_reported_as_explicit_overflow():
    result = build_context(
        [],
        [_visual(31, 0.0, 10.0, "A very long visual caption that cannot fit")],
        max_input_tokens=1,
    )

    assert result.completeness == "incomplete"
    assert result.unassigned_items
    assert result.unassigned_items[0].source_id == 31
    assert any("overflow" in warning for warning in result.warning_codes)
