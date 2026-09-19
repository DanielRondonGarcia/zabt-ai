# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for multimodal context assembly helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.multimodal_context import (
    ContextBuilder,
    build_context,
    build_multimodal_context,
    correlate_evidence,
    estimate_input_tokens,
    intervals_overlap,
)


def test_interval_and_token_helpers_use_half_open_overlap_and_minimum_token():
    assert intervals_overlap(0, 10, 9.99, 12)
    assert not intervals_overlap(0, 10, 10, 12)
    assert estimate_input_tokens("") == 1
    assert estimate_input_tokens("abcd efgh") == 3


def test_normalization_maps_attributes_defaults_relevance_provenance_and_confidence():
    spoken = [SimpleNamespace(id="7", start_time=1, end_time=2, text="hello", speaker="Ada")]
    visual = [
        {"id": "9", "start": 1.5, "end": 3, "caption": "Error chart", "confidence": 0.4},
        {"source_id": 10, "start": 4, "end": 5, "content": "webcam face", "provenance": "inferred"},
    ]

    result = build_context(spoken, visual, chunk_seconds=10, max_input_tokens=100)
    items = [item for chunk in result.chunks for item in chunk.items]

    assert result.source_item_count == 3
    assert items[0].source == "spoken"
    assert items[0].source_id == 7
    assert items[0].speaker == "Ada"
    assert items[0].relevance == "high"
    assert items[1].relevance == "high"
    assert items[1].uncertainty == "low confidence visual evidence"
    assert items[1].evidence_ref == "visual:9"
    assert items[2].relevance == "low"
    assert items[2].provenance == "inferred"


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"id": "bad", "start": 0, "end": 1, "text": "x"}, "context source IDs"),
        ({"id": 1, "start": 2, "end": 2, "text": "x"}, "finite end > start"),
        ({"id": 1, "start": 0, "end": 1, "text": "x", "provenance": "guessed"}, "provenance"),
    ],
)
def test_normalization_rejects_invalid_ids_intervals_and_provenance(record, message):
    with pytest.raises(ValueError, match=message):
        build_context([record], [], chunk_seconds=10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_seconds": 0},
        {"max_input_tokens": 0},
    ],
)
def test_builder_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        ContextBuilder(**kwargs)


def test_visual_deduplication_keeps_meaningful_changes_and_correlation_overlaps_only():
    spoken = [{"id": 1, "start": 10, "end": 20, "text": "discuss screen"}]
    visuals = [
        {"id": 1, "start": 9, "end": 15, "caption": "Shared dashboard overview"},
        {"id": 2, "start": 12, "end": 18, "caption": "Shared dashboard overview "},
        {"id": 3, "start": 12, "end": 18, "caption": "Shared dashboard overview", "meaningful_change": True},
        {"id": 4, "start": 20, "end": 25, "caption": "Shared dashboard overview"},
    ]

    pairs = correlate_evidence(spoken, visuals, chunk_seconds=10)
    assert pairs[0][0].source_id == 1
    assert [item.source_id for item in pairs[0][1]] == [1, 3]


def test_context_chunking_splits_by_bucket_and_budget_and_marks_visual_overflow_unassigned():
    spoken = [
        {"id": 1, "start": 0, "end": 5, "text": "one two three four"},
        {"id": 2, "start": 5, "end": 9, "text": "five six seven eight"},
        {"id": 3, "start": 25, "end": 29, "text": "later bucket"},
    ]
    visual = [{"id": 9, "start": 1, "end": 2, "caption": "x" * 40}]

    result = ContextBuilder(chunk_seconds=10, max_input_tokens=5).build(spoken, visual)

    assert [chunk.index for chunk in result.chunks] == [0, 1, 2]
    assert [chunk.start for chunk in result.chunks] == [0, 0, 20]
    assert result.unassigned_items[0].source == "visual"
    assert result.completeness == "incomplete"
    assert result.warning_codes == ("context_budget_overflow", "unassigned_context_items")
    assert all(not chunk.complete for chunk in result.chunks)


def test_long_spoken_text_is_split_into_fragments_including_long_words_and_alias_matches():
    long_word = "a" * 13
    result = build_multimodal_context(
        [{"id": 1, "start": 0, "end": 5, "text": f"alpha beta {long_word} gamma"}],
        [],
        chunk_seconds=10,
        max_input_tokens=2,
    )

    fragments = [item for chunk in result.chunks for item in chunk.items]
    assert [item.fragment_index for item in fragments] == [0, 1, 2, 3, 4]
    assert [item.content for item in fragments] == ["alpha", "beta", "aaaaaaaa", "aaaaa", "gamma"]
    assert result.assigned_item_count == 1
    assert result.completeness == "complete"
