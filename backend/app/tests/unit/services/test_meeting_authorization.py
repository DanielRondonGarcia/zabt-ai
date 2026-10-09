# SPDX-License-Identifier: AGPL-3.0-only
"""Current group authorization at shared meeting mutation boundaries."""

from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Group, GroupMemberRole, GroupMembership, Meeting, TranscriptSegment, User
from app.services import meeting as meeting_module
from app.services.meeting import MeetingService


@pytest.fixture(name="sqlite_engine")
def fixture_sqlite_engine() -> Iterator:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    language_preferences = User.__table__.c.language_preferences
    json_columns = [
        language_preferences,
        Meeting.__table__.c.structured_output,
        Meeting.__table__.c.visual_breakdown_params,
        TranscriptSegment.__table__.c.words,
    ]
    original_types = [column.type for column in json_columns]
    for column in json_columns:
        column.type = JSON()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Group.__table__,
            GroupMembership.__table__,
            Meeting.__table__,
            TranscriptSegment.__table__,
        ],
    )
    try:
        yield engine
    finally:
        for column, original_type in zip(json_columns, original_types):
            column.type = original_type


@pytest.fixture(name="meeting_data")
def fixture_meeting_data(sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(meeting_module, "engine", sqlite_engine)
    with Session(sqlite_engine) as session:
        session.add_all(
            [
                User(id=1, email="owner@example.com"),
                User(id=2, email="editor@example.com"),
                User(id=3, email="viewer@example.com"),
            ]
        )
        group = Group(name="Shared", owner_id=1)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add_all(
            [
                GroupMembership(group_id=group.id, user_id=2, role=GroupMemberRole.EDITOR),
                GroupMembership(group_id=group.id, user_id=3, role=GroupMemberRole.VIEWER),
            ]
        )
        meeting = Meeting(
            title="Shared meeting",
            owner_id=1,
            group_id=group.id,
            status="completed",
        )
        session.add(meeting)
        session.commit()
        session.refresh(meeting)
        return meeting.id


def test_realtime_segment_mutation_rechecks_editor_membership(
    sqlite_engine, meeting_data: int
) -> None:
    service = MeetingService()

    segment = service.add_segment_for_user(meeting_data, 2, 0.0, 1.0, "allowed")
    assert segment.text == "allowed"

    with pytest.raises(HTTPException) as viewer_error:
        service.add_segment_for_user(meeting_data, 3, 1.0, 2.0, "viewer")
    assert viewer_error.value.status_code == 403

    with Session(sqlite_engine) as session:
        # The editor is downgraded in the same database used by the mutation check.
        row = session.exec(
            select(GroupMembership).where(GroupMembership.user_id == 2)
        ).first()
        row.role = GroupMemberRole.VIEWER
        session.add(row)
        session.commit()

    with pytest.raises(HTTPException) as downgraded_error:
        service.add_segment_for_user(meeting_data, 2, 2.0, 3.0, "downgraded")
    assert downgraded_error.value.status_code == 403
