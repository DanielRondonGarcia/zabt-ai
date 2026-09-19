# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Unit tests for GroupService CRUD, validation, and owner scoping."""

from collections.abc import Iterator

from fastapi import HTTPException
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.models import Group
from app.services import base as base_module
from app.services import group as group_module
from app.services.group import GroupService


@pytest.fixture(name="sqlite_engine")
def fixture_sqlite_engine():
    """Create isolated Group-only SQLite metadata for each test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=[Group.__table__])
    return engine


@pytest.fixture(autouse=True)
def patch_group_service_engine(sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the engine symbols used by GroupService and BaseService.save."""
    monkeypatch.setattr(group_module, "engine", sqlite_engine)
    monkeypatch.setattr(base_module, "engine", sqlite_engine)


@pytest.fixture(name="service")
def fixture_service() -> GroupService:
    return GroupService()


@pytest.fixture(name="seed_group")
def fixture_seed_group(sqlite_engine) -> Iterator[Group]:
    with Session(sqlite_engine) as session:
        group = Group(name="Owned Group", description="Owned", owner_id=1)
        session.add(group)
        session.commit()
        session.refresh(group)
        yield group


def test_create_group_persists_owner_scoped_record(service: GroupService) -> None:
    group = service.create(1, "Test Group", "Test Description")

    assert group.id is not None
    assert group.owner_id == 1
    assert group.name == "Test Group"
    assert group.description == "Test Description"


def test_list_for_user_returns_only_owned_groups(service: GroupService, sqlite_engine) -> None:
    with Session(sqlite_engine) as session:
        session.add(Group(name="Owner 1", owner_id=1))
        session.add(Group(name="Owner 2", owner_id=2))
        session.commit()

    groups = service.list_for_user(1)

    assert [group.name for group in groups] == ["Owner 1"]


def test_get_accessible_returns_owned_group(service: GroupService, seed_group: Group) -> None:
    group = service.get_accessible(seed_group.id, 1)

    assert group.id == seed_group.id
    assert group.owner_id == 1


def test_get_accessible_missing_group_raises_404(service: GroupService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.get_accessible(999, 1)

    assert exc_info.value.status_code == 404


def test_get_accessible_foreign_group_raises_403(
    service: GroupService, seed_group: Group
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.get_accessible(seed_group.id, 2)

    assert exc_info.value.status_code == 403


def test_update_group_persists_changes(service: GroupService, seed_group: Group) -> None:
    updated = service.update(seed_group.id, 1, name="Updated", description="Changed")

    assert updated.name == "Updated"
    assert updated.description == "Changed"


def test_update_group_partial_preserves_existing_description(
    service: GroupService, seed_group: Group
) -> None:
    updated = service.update(seed_group.id, 1, name="Renamed")

    assert updated.name == "Renamed"
    assert updated.description == "Owned"


def test_update_missing_group_raises_404(service: GroupService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.update(999, 1, name="Updated")

    assert exc_info.value.status_code == 404


def test_update_foreign_group_raises_403(service: GroupService, seed_group: Group) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.update(seed_group.id, 2, name="Updated")

    assert exc_info.value.status_code == 403


def test_delete_group_removes_record(
    service: GroupService, seed_group: Group, sqlite_engine
) -> None:
    service.delete(seed_group.id, 1)

    with Session(sqlite_engine) as session:
        assert session.get(Group, seed_group.id) is None


def test_delete_missing_group_raises_404(service: GroupService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.delete(999, 1)

    assert exc_info.value.status_code == 404


def test_delete_foreign_group_raises_403(service: GroupService, seed_group: Group) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.delete(seed_group.id, 2)

    assert exc_info.value.status_code == 403


def test_validate_name_empty_raises_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        GroupService._validate_name("")

    assert exc_info.value.status_code == 400
    assert "cannot be empty" in exc_info.value.detail


def test_validate_name_none_raises_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        GroupService._validate_name(None)

    assert exc_info.value.status_code == 400
    assert "cannot be empty" in exc_info.value.detail


def test_validate_name_whitespace_only_raises_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        GroupService._validate_name("   ")

    assert exc_info.value.status_code == 400
    assert "cannot be empty" in exc_info.value.detail


def test_validate_name_too_long_raises_400() -> None:
    long_name = "A" * 101
    with pytest.raises(HTTPException) as exc_info:
        GroupService._validate_name(long_name)

    assert exc_info.value.status_code == 400
    assert "must not exceed 100 characters" in exc_info.value.detail


def test_validate_name_valid() -> None:
    GroupService._validate_name("Valid Name")
    GroupService._validate_name("A")
    GroupService._validate_name("A" * 100)


def test_validate_description_too_long_raises_400() -> None:
    long_desc = "A" * 501
    with pytest.raises(HTTPException) as exc_info:
        GroupService._validate_description(long_desc)

    assert exc_info.value.status_code == 400
    assert "must not exceed 500 characters" in exc_info.value.detail


def test_validate_description_valid() -> None:
    GroupService._validate_description(None)
    GroupService._validate_description("")
    GroupService._validate_description("A" * 500)
