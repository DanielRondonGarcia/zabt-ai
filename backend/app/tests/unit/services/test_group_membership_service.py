# SPDX-License-Identifier: AGPL-3.0-only
"""Focused tests for shared group access and member management."""

from collections.abc import Iterator

from fastapi import HTTPException
import pytest
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.models import Group, GroupMemberRole, GroupMembership, User
from app.services import base as base_module
from app.services import group as group_module
from app.services.group import GroupReindexEnqueueError, GroupService


@pytest.fixture(name="sqlite_engine")
def fixture_sqlite_engine() -> Iterator:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    language_preferences = User.__table__.c.language_preferences
    original_type = language_preferences.type
    language_preferences.type = JSON()
    SQLModel.metadata.create_all(
        engine,
        tables=[User.__table__, Group.__table__, GroupMembership.__table__],
    )
    try:
        yield engine
    finally:
        language_preferences.type = original_type


@pytest.fixture(name="service")
def fixture_service(sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> GroupService:
    monkeypatch.setattr(group_module, "engine", sqlite_engine)
    monkeypatch.setattr(base_module, "engine", sqlite_engine)
    return GroupService()


@pytest.fixture(name="seed_data")
def fixture_seed_data(sqlite_engine) -> dict[str, int]:
    with Session(sqlite_engine) as session:
        session.add_all(
            [
                User(id=1, email="owner@example.com", full_name="Group Owner"),
                User(id=2, email="viewer@example.com", full_name="View Member"),
                User(id=3, email="editor@example.com", full_name="Edit Member"),
                User(id=4, email="inactive@example.com", full_name="Inactive User", is_active=False),
                User(id=5, email="searchable@example.com", full_name="Searchable User"),
                User(id=6, email="literal%match@example.com", full_name="Percent Match"),
                User(id=7, email="literal_match@example.com", full_name="Underscore Match"),
                User(id=8, email=r"literal\match@example.com", full_name="Escape Match"),
                User(id=9, email="plainmatch@example.com", full_name="Plain Match"),
            ]
        )
        group = Group(name="Shared Group", description="Initial context", owner_id=1)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add_all(
            [
                GroupMembership(group_id=group.id, user_id=2, role=GroupMemberRole.VIEWER),
                GroupMembership(group_id=group.id, user_id=3, role=GroupMemberRole.EDITOR),
            ]
        )
        session.commit()
        return {"group_id": group.id}


def test_list_and_access_roles_are_computed_from_owner_or_membership(
    service: GroupService, seed_data: dict[str, int]
) -> None:
    group_id = seed_data["group_id"]

    owner_access = service.get_access(group_id, 1)
    editor_access = service.get_access(group_id, 3)
    viewer_access = service.get_access(group_id, 2)

    assert owner_access.role == "owner"
    assert editor_access.role == "editor"
    assert viewer_access.role == "viewer"
    assert [group.id for group in service.list_for_user(2)] == [group_id]
    assert service.to_read(editor_access).can_edit is True
    assert service.to_read(editor_access).can_manage_members is False
    assert service.to_read(viewer_access).can_edit is False
    assert service.to_read(owner_access).can_delete is True


def test_membership_model_has_unique_pair_and_cascading_foreign_keys() -> None:
    constraints = GroupMembership.__table__.constraints
    unique_pairs = [
        constraint
        for constraint in constraints
        if getattr(constraint, "columns", None)
        and {column.name for column in constraint.columns} == {"group_id", "user_id"}
    ]

    assert unique_pairs
    assert {
        foreign_key.ondelete
        for column in GroupMembership.__table__.columns
        for foreign_key in column.foreign_keys
    } == {"CASCADE"}


def test_viewer_cannot_mutate_group_or_reindex_but_editor_can(
    service: GroupService, seed_data: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    group_id = seed_data["group_id"]

    with pytest.raises(HTTPException) as viewer_error:
        service.require_editor(group_id, 2)
    assert viewer_error.value.status_code == 403

    reindexed: list[int] = []
    monkeypatch.setattr(service, "_enqueue_group_reindex", lambda value: reindexed.append(value))
    updated = service.update(
        group_id,
        3,
        name="Edited Group",
        description="Updated AI context",
        description_provided=True,
    )

    assert updated.name == "Edited Group"
    assert updated.description == "Updated AI context"
    assert reindexed == [group_id]


def test_description_can_be_cleared_and_only_introduction_changes_reindex(
    service: GroupService, seed_data: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    group_id = seed_data["group_id"]
    reindexed: list[int] = []
    monkeypatch.setattr(service, "_enqueue_group_reindex", lambda value: reindexed.append(value))

    service.update(group_id, 3, name="Renamed")
    assert reindexed == []

    updated = service.update(
        group_id,
        3,
        description=None,
        description_provided=True,
    )
    assert updated.description is None
    assert reindexed == [group_id]


def test_user_search_is_bounded_active_and_excludes_owner_and_members(
    service: GroupService, seed_data: dict[str, int]
) -> None:
    results = service.search_users(seed_data["group_id"], 1, "user", limit=50)

    assert [(user_id, email) for user_id, email, _ in results] == [
        (5, "searchable@example.com")
    ]

    with pytest.raises(HTTPException) as short_query:
        service.search_users(seed_data["group_id"], 1, "x")
    assert short_query.value.status_code == 400


def test_user_search_treats_wildcards_and_escape_characters_as_literals(
    service: GroupService, seed_data: dict[str, int]
) -> None:
    group_id = seed_data["group_id"]

    assert [row[0] for row in service.search_users(group_id, 1, "%match")] == [6]
    assert [row[0] for row in service.search_users(group_id, 1, "_match")] == [7]
    assert [row[0] for row in service.search_users(group_id, 1, r"\match")] == [8]


def test_group_update_reports_pending_reindex_after_durable_commit(
    service: GroupService, seed_data: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    group_id = seed_data["group_id"]

    def fail_enqueue(value: int) -> None:
        raise GroupReindexEnqueueError(value)

    monkeypatch.setattr(service, "_enqueue_group_reindex", fail_enqueue)

    with pytest.raises(GroupReindexEnqueueError):
        service.update(
            group_id,
            3,
            description="Durable but pending",
            description_provided=True,
        )

    assert service.get_access(group_id, 1).group.description == "Durable but pending"


def test_member_lifecycle_rejects_self_and_duplicates(
    service: GroupService, seed_data: dict[str, int]
) -> None:
    group_id = seed_data["group_id"]

    with pytest.raises(HTTPException) as self_share:
        service.add_member(group_id, 1, 1, "viewer")
    assert self_share.value.status_code == 400

    with pytest.raises(HTTPException) as duplicate:
        service.add_member(group_id, 1, 2, "editor")
    assert duplicate.value.status_code == 409

    added = service.add_member(group_id, 1, 5, "viewer")
    assert added.role == "viewer"
    changed = service.update_member(group_id, 1, 5, "editor")
    assert changed.role == "editor"
    service.remove_member(group_id, 1, 5)
    assert all(record.user.id != 5 for record in service.list_members(group_id, 1))


def test_non_owner_cannot_manage_members(
    service: GroupService, seed_data: dict[str, int]
) -> None:
    group_id = seed_data["group_id"]

    with pytest.raises(HTTPException) as editor_error:
        service.list_members(group_id, 3)
    assert editor_error.value.status_code == 403

    with pytest.raises(HTTPException) as viewer_error:
        service.remove_member(group_id, 2, 3)
    assert viewer_error.value.status_code == 403
