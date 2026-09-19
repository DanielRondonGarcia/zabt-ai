# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused VisualSegmentService coverage with isolated fake sessions."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.models import TranscriptSegment, VisualSegment
from app.services import visual_segments as visual_segments_module
from app.services.visual_segments import (
    TranscriptLineOut,
    VisualSegmentService,
    VisualSegmentWithTranscript,
)


class FakeStatement:
    def __init__(self, kind: str, model: type):
        self.kind = kind
        self.model = model
        self.where_args: list[object] = []
        self.order_by_args: list[object] = []

    def where(self, *args: object) -> "FakeStatement":
        self.where_args.extend(args)
        return self

    def order_by(self, *args: object) -> "FakeStatement":
        self.order_by_args.extend(args)
        return self


class FakeSession:
    def __init__(self, exec_results: list[list[object]] | None = None):
        self.exec_results = list(exec_results or [])
        self.exec_statements: list[FakeStatement] = []
        self.added: list[object] = []
        self.commits = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def exec(self, statement: FakeStatement) -> list[object]:
        self.exec_statements.append(statement)
        if self.exec_results:
            return self.exec_results.pop(0)
        return []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture(name="statements")
def fixture_statements(monkeypatch: pytest.MonkeyPatch) -> list[FakeStatement]:
    built: list[FakeStatement] = []

    def fake_select(model: type) -> FakeStatement:
        statement = FakeStatement("select", model)
        built.append(statement)
        return statement

    def fake_delete(model: type) -> FakeStatement:
        statement = FakeStatement("delete", model)
        built.append(statement)
        return statement

    monkeypatch.setattr(visual_segments_module, "select", fake_select)
    monkeypatch.setattr(visual_segments_module, "delete", fake_delete)
    return built


@pytest.fixture(name="service")
def fixture_service() -> VisualSegmentService:
    return VisualSegmentService()


def make_visual_segment(
    *,
    segment_id: int | None,
    meeting_id: int = 1,
    sequence: int,
    start_time: float,
    end_time: float,
    caption: str | None = None,
) -> VisualSegment:
    return VisualSegment(
        id=segment_id,
        meeting_id=meeting_id,
        sequence=sequence,
        start_time=start_time,
        end_time=end_time,
        screenshot_s3_key=f"screen-{sequence}.jpg",
        caption=caption or f"caption {sequence}",
        confidence=0.75 + (sequence / 100),
    )


def make_transcript(
    *,
    start_time: float,
    end_time: float,
    text: str,
    speaker: str | None = "Speaker 1",
    meeting_id: int = 1,
) -> TranscriptSegment:
    return TranscriptSegment(
        meeting_id=meeting_id,
        start_time=start_time,
        end_time=end_time,
        text=text,
        speaker=speaker,
    )


def patch_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    monkeypatch.setattr(visual_segments_module, "Session", lambda _engine: session)


def test_list_for_meeting_uses_sequence_ordering_query_and_returns_rows(
    monkeypatch: pytest.MonkeyPatch,
    service: VisualSegmentService,
    statements: list[FakeStatement],
) -> None:
    ordered_rows = [
        make_visual_segment(segment_id=11, sequence=1, start_time=0, end_time=5),
        make_visual_segment(segment_id=12, sequence=2, start_time=5, end_time=10),
    ]
    session = FakeSession(exec_results=[ordered_rows])
    patch_session(monkeypatch, session)

    rows = service.list_for_meeting(123)

    assert rows == ordered_rows
    assert len(statements) == 1
    statement = statements[0]
    assert statement.kind == "select"
    assert statement.model is VisualSegment
    assert statement.where_args
    assert statement.order_by_args == [VisualSegment.sequence]


def test_replace_for_meeting_deletes_existing_segments_detaches_ids_sets_meeting_and_commits(
    monkeypatch: pytest.MonkeyPatch,
    service: VisualSegmentService,
    statements: list[FakeStatement],
) -> None:
    session = FakeSession()
    patch_session(monkeypatch, session)
    segments = [
        make_visual_segment(segment_id=100, meeting_id=9, sequence=1, start_time=0, end_time=10),
        make_visual_segment(segment_id=101, meeting_id=9, sequence=2, start_time=10, end_time=20),
    ]

    service.replace_for_meeting(42, segments)

    assert statements[0].kind == "delete"
    assert statements[0].model is VisualSegment
    assert statements[0].where_args
    assert session.added == segments
    assert session.commits == 1
    assert [segment.id for segment in segments] == [None, None]
    assert [segment.meeting_id for segment in segments] == [42, 42]


def test_replace_for_meeting_in_session_uses_caller_owned_transaction_without_commit(
    statements: list[FakeStatement],
) -> None:
    session = FakeSession()
    segments = [
        make_visual_segment(segment_id=200, meeting_id=1, sequence=1, start_time=0, end_time=1)
    ]

    VisualSegmentService.replace_for_meeting_in_session(session, 77, segments)

    assert statements[0].kind == "delete"
    assert session.added == segments
    assert session.commits == 0
    assert segments[0].id is None
    assert segments[0].meeting_id == 77


def test_get_with_transcript_alignment_returns_empty_without_transcript_query(
    monkeypatch: pytest.MonkeyPatch,
    service: VisualSegmentService,
    statements: list[FakeStatement],
) -> None:
    session = FakeSession(exec_results=[[]])
    patch_session(monkeypatch, session)

    assert service.get_with_transcript_alignment(55) == []

    assert len(session.exec_statements) == 1
    assert len(statements) == 1
    assert statements[0].model is VisualSegment


def test_get_with_transcript_alignment_uses_two_pointer_half_open_boundaries_and_straddles(
    monkeypatch: pytest.MonkeyPatch,
    service: VisualSegmentService,
    statements: list[FakeStatement],
) -> None:
    segments = [
        make_visual_segment(segment_id=1, sequence=1, start_time=0.0, end_time=10.0),
        make_visual_segment(segment_id=2, sequence=2, start_time=10.0, end_time=20.0),
        make_visual_segment(segment_id=3, sequence=3, start_time=20.0, end_time=30.0),
    ]
    transcript = [
        make_transcript(start_time=-1.0, end_time=1.0, text="before"),
        make_transcript(start_time=0.0, end_time=2.0, text="starts at segment start"),
        make_transcript(start_time=9.5, end_time=10.5, text="straddles boundary"),
        make_transcript(start_time=10.0, end_time=11.0, text="starts at next segment"),
        make_transcript(start_time=19.9, end_time=21.0, text="straddles second boundary"),
        make_transcript(start_time=20.0, end_time=22.0, text="third segment start"),
        make_transcript(start_time=30.0, end_time=31.0, text="after half open end"),
    ]
    session = FakeSession(exec_results=[segments, transcript])
    patch_session(monkeypatch, session)

    aligned = service.get_with_transcript_alignment(1)

    assert [statement.model for statement in statements] == [VisualSegment, TranscriptSegment]
    assert statements[0].order_by_args == [VisualSegment.sequence]
    assert statements[1].order_by_args == [TranscriptSegment.start_time]
    assert [line.text for line in aligned[0].transcript_lines] == [
        "starts at segment start",
        "straddles boundary",
    ]
    assert [line.text for line in aligned[1].transcript_lines] == [
        "starts at next segment",
        "straddles second boundary",
    ]
    assert [line.text for line in aligned[2].transcript_lines] == ["third segment start"]


def test_get_with_transcript_alignment_returns_dataclass_output(
    monkeypatch: pytest.MonkeyPatch,
    service: VisualSegmentService,
    statements: list[FakeStatement],
) -> None:
    segment = make_visual_segment(
        segment_id=9,
        sequence=4,
        start_time=3.0,
        end_time=6.0,
        caption="slide title",
    )
    line = make_transcript(
        start_time=3.5,
        end_time=4.0,
        text="hello",
        speaker=None,
    )
    session = FakeSession(exec_results=[[segment], [line]])
    patch_session(monkeypatch, session)

    [output] = service.get_with_transcript_alignment(1)

    assert isinstance(output, VisualSegmentWithTranscript)
    assert output == VisualSegmentWithTranscript(
        id=9,
        sequence=4,
        start_time=3.0,
        end_time=6.0,
        screenshot_s3_key="screen-4.jpg",
        caption="slide title",
        confidence=0.79,
        transcript_lines=[
            TranscriptLineOut(speaker=None, text="hello", start=3.5, end=4.0)
        ],
    )
    assert output.transcript_lines[0] == TranscriptLineOut(
        speaker=None, text="hello", start=3.5, end=4.0
    )
