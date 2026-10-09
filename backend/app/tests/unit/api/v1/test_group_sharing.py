# SPDX-License-Identifier: AGPL-3.0-only
"""Endpoint coverage for shared group visibility and member management."""

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.api import deps
from app.models import Group, GroupMembership, User
from app.services import base as base_module
from app.services import group as group_module


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


@pytest.fixture(name="test_client")
def fixture_test_client(sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(group_module, "engine", sqlite_engine)
    monkeypatch.setattr(base_module, "engine", sqlite_engine)
    from app.api.v1.endpoints import groups

    app = FastAPI(title="Group sharing API")
    app.include_router(groups.router, prefix="/groups")
    state = {"user_id": 1}

    def current_user() -> User:
        return User(id=state["user_id"], email=f"user{state['user_id']}@example.com")

    app.dependency_overrides[deps.get_current_active_user] = current_user
    with TestClient(app) as client:
        client.app_state = state  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="group_id")
def fixture_group_id(sqlite_engine) -> int:
    with Session(sqlite_engine) as session:
        session.add_all(
            [
                User(id=1, email="owner@example.com", full_name="Owner"),
                User(id=2, email="viewer@example.com", full_name="Viewer"),
                User(id=3, email="editor@example.com", full_name="Editor"),
                User(id=4, email="alice@example.com", full_name="Alice Example"),
            ]
        )
        group = Group(name="Shared", owner_id=1)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add(GroupMembership(group_id=group.id, user_id=2, role="viewer"))
        session.commit()
        return group.id


def test_owner_response_has_capabilities_and_shared_user_can_read_group(
    test_client: TestClient, group_id: int
) -> None:
    owner_response = test_client.get(f"/groups/{group_id}")
    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["access_role"] == "owner"
    assert owner_response.json()["can_manage_members"] is True

    test_client.app_state["user_id"] = 2  # type: ignore[attr-defined]
    shared_response = test_client.get(f"/groups/{group_id}")
    assert shared_response.status_code == 200, shared_response.text
    assert shared_response.json()["access_role"] == "viewer"
    assert shared_response.json()["can_edit"] is False
    assert shared_response.json()["can_delete"] is False


def test_owner_can_search_add_change_and_remove_members(
    test_client: TestClient, group_id: int
) -> None:
    search = test_client.get(f"/groups/{group_id}/members/search", params={"q": "alice"})
    assert search.status_code == 200, search.text
    assert search.json() == [
        {"user_id": 4, "email": "alice@example.com", "full_name": "Alice Example"}
    ]
    assert "password_hash" not in search.text

    added = test_client.post(
        f"/groups/{group_id}/members",
        json={"user_id": 3, "role": "editor"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["role"] == "editor"

    duplicate = test_client.post(
        f"/groups/{group_id}/members",
        json={"user_id": 3, "role": "viewer"},
    )
    assert duplicate.status_code == 409, duplicate.text

    changed = test_client.patch(
        f"/groups/{group_id}/members/3",
        json={"role": "viewer"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role"] == "viewer"

    removed = test_client.delete(f"/groups/{group_id}/members/3")
    assert removed.status_code == 204, removed.text


def test_shared_user_cannot_manage_members_or_reindex(
    test_client: TestClient, group_id: int
) -> None:
    test_client.app_state["user_id"] = 2  # type: ignore[attr-defined]

    members = test_client.get(f"/groups/{group_id}/members")
    assert members.status_code == 403, members.text

    add_attempt = test_client.post(
        f"/groups/{group_id}/members",
        json={"user_id": 4, "role": "viewer"},
    )
    assert add_attempt.status_code == 403, add_attempt.text

    reindex_attempt = test_client.post(f"/groups/{group_id}/reindex")
    assert reindex_attempt.status_code == 403, reindex_attempt.text
