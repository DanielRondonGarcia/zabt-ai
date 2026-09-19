# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Unit tests for Group model."""

from datetime import datetime

import pytest
from sqlmodel import Session, create_engine, SQLModel
from app.models import Group


@pytest.fixture(name="sqlite_engine", scope="function")
def fixture_sqlite_engine() -> None:
    """Create isolated in-memory database per test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Create only the Group table (not full metadata to avoid JSONB issues)
    SQLModel.metadata.create_all(engine, tables=[Group.__table__])
    return engine


def test_group_model_structure(sqlite_engine) -> None:
    """Verify Group model has required fields matching design."""
    group = Group(
        name="Test Group",
        description="Test Description",
        owner_id=1,
    )
    assert group.name == "Test Group"
    assert group.description == "Test Description"
    assert group.owner_id == 1
    assert group.id is None  # Not yet persisted


def test_group_model_timestamps(sqlite_engine) -> None:
    """Verify Group has created_at and updated_at timestamps."""
    group = Group(
        name="Test Group",
        owner_id=1,
    )
    assert isinstance(group.created_at, datetime)
    assert isinstance(group.updated_at, datetime)


def test_group_with_meeting_fk(sqlite_engine) -> None:
    """Verify Meeting.group_id is nullable FK to Group."""
    from app.models import Meeting

    meeting = Meeting(
        title="Test Meeting",
        group_id=None,  # Should be nullable
    )
    assert meeting.group_id is None


def test_group_relationship_to_meeting(sqlite_engine) -> None:
    """Verify Group has relationship to meetings."""
    group = Group(
        name="Test Group",
        owner_id=1,
    )
    # Should have meetings relationship
    assert hasattr(group, "meetings")
