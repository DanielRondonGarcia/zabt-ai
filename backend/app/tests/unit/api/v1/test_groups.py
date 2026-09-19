# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Endpoint tests for groups CRUD operations."""

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.api import deps
from app.models import Group, User
from app.services import base as base_module
from app.services import group as group_module


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


@pytest.fixture(name="test_client")
def fixture_test_client(sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Create a minimal FastAPI app with only the groups router."""
    monkeypatch.setattr(group_module, "engine", sqlite_engine)
    monkeypatch.setattr(base_module, "engine", sqlite_engine)

    from app.api.v1.endpoints import groups

    app = FastAPI(title="Test Groups API")
    app.include_router(groups.router, prefix="/groups", tags=["groups"])

    def override_get_current_active_user() -> User:
        return User(id=1, email="test@example.com", full_name="Test User")

    app.dependency_overrides[
        deps.get_current_active_user
    ] = override_get_current_active_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture(name="foreign_group_id")
def fixture_foreign_group_id(sqlite_engine) -> int:
    with Session(sqlite_engine) as session:
        group = Group(name="Foreign Group", owner_id=2)
        session.add(group)
        session.commit()
        session.refresh(group)
        return group.id


def test_create_group(test_client: TestClient) -> None:
    response = test_client.post(
        "/groups/",
        json={"name": "Test Group", "description": "Test Description"},
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "Test Group"
    assert data["description"] == "Test Description"
    assert data["owner_id"] == 1
    assert "id" in data


def test_list_groups_returns_only_current_user_groups(
    test_client: TestClient, sqlite_engine
) -> None:
    with Session(sqlite_engine) as session:
        session.add(Group(name="Group 1", description="Desc 1", owner_id=1))
        session.add(Group(name="Foreign Group", owner_id=2))
        session.commit()

    response = test_client.get("/groups/")

    assert response.status_code == 200, response.text
    data = response.json()
    assert [group["name"] for group in data] == ["Group 1"]


def test_get_group(test_client: TestClient) -> None:
    create_resp = test_client.post("/groups/", json={"name": "Group 1"})
    group_id = create_resp.json()["id"]

    response = test_client.get(f"/groups/{group_id}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Group 1"


def test_get_group_not_found(test_client: TestClient) -> None:
    response = test_client.get("/groups/999")

    assert response.status_code == 404, response.text


def test_get_foreign_group_returns_403(
    test_client: TestClient, foreign_group_id: int
) -> None:
    response = test_client.get(f"/groups/{foreign_group_id}")

    assert response.status_code == 403, response.text


def test_update_group_with_patch(test_client: TestClient) -> None:
    create_resp = test_client.post(
        "/groups/",
        json={"name": "Original Name", "description": "Original Desc"},
    )
    group_id = create_resp.json()["id"]

    response = test_client.patch(
        f"/groups/{group_id}",
        json={"name": "Updated Name", "description": "Updated Desc"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "Updated Desc"


def test_update_group_rejects_put(test_client: TestClient) -> None:
    create_resp = test_client.post("/groups/", json={"name": "Original Name"})
    group_id = create_resp.json()["id"]

    response = test_client.put(f"/groups/{group_id}", json={"name": "Updated"})

    assert response.status_code == 405, response.text


def test_update_group_partial(test_client: TestClient) -> None:
    create_resp = test_client.post(
        "/groups/",
        json={"name": "Original Name", "description": "Original Desc"},
    )
    group_id = create_resp.json()["id"]

    response = test_client.patch(
        f"/groups/{group_id}",
        json={"name": "Updated Name Only"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Updated Name Only"
    assert data["description"] == "Original Desc"


def test_update_group_not_found(test_client: TestClient) -> None:
    response = test_client.patch("/groups/999", json={"name": "New Name"})

    assert response.status_code == 404, response.text


def test_update_foreign_group_returns_403(
    test_client: TestClient, foreign_group_id: int
) -> None:
    response = test_client.patch(
        f"/groups/{foreign_group_id}", json={"name": "New Name"}
    )

    assert response.status_code == 403, response.text


def test_delete_group(test_client: TestClient) -> None:
    create_resp = test_client.post("/groups/", json={"name": "To Delete"})
    group_id = create_resp.json()["id"]

    response = test_client.delete(f"/groups/{group_id}")

    assert response.status_code == 204, response.text
    assert test_client.get(f"/groups/{group_id}").status_code == 404


def test_delete_group_not_found(test_client: TestClient) -> None:
    response = test_client.delete("/groups/999")

    assert response.status_code == 404, response.text


def test_delete_foreign_group_returns_403(
    test_client: TestClient, foreign_group_id: int
) -> None:
    response = test_client.delete(f"/groups/{foreign_group_id}")

    assert response.status_code == 403, response.text


def test_search_group_returns_ranked_results(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.endpoints import groups

    create_resp = test_client.post("/groups/", json={"name": "Searchable"})
    group_id = create_resp.json()["id"]

    def fake_search(group_id: int, user_id: int, query: str, limit: int = 10, kinds=None):
        assert group_id == create_resp.json()["id"]
        assert user_id == 1
        assert query == "find evidence"
        assert limit == 3
        assert kinds == ["summary"]
        return [
            {
                "meeting_id": 9,
                "kind": "summary",
                "chunk_index": 0,
                "score": 0.88,
                "text": "matching chunk",
            }
        ]

    monkeypatch.setattr(groups.retrieval_service, "search", fake_search)

    response = test_client.post(
        f"/groups/{group_id}/search",
        json={"query": "find evidence", "limit": 3, "kinds": ["summary"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "group_id": group_id,
        "results": [
            {
                "meeting_id": 9,
                "kind": "summary",
                "chunk_index": 0,
                "score": 0.88,
                "text": "matching chunk",
            }
        ],
    }


def test_search_foreign_group_returns_403_without_provider_or_vector_call(
    test_client: TestClient, foreign_group_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import groups

    monkeypatch.setattr(
        groups.retrieval_service,
        "search",
        lambda *args, **kwargs: pytest.fail("foreign group search must be denied before retrieval"),
    )

    response = test_client.post(f"/groups/{foreign_group_id}/search", json={"query": "private"})

    assert response.status_code == 403, response.text


def test_reindex_group_authorizes_then_enqueues(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.endpoints import groups

    create_resp = test_client.post("/groups/", json={"name": "Reindexable"})
    group_id = create_resp.json()["id"]
    events = []

    class FakeTask:
        def delay(self, group_id_arg: int):
            events.append(("delay", group_id_arg))
            return type("AsyncResult", (), {"id": "task-123"})()

    monkeypatch.setattr(groups, "reindex_group", FakeTask())

    response = test_client.post(f"/groups/{group_id}/reindex")

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted", "task_id": "task-123"}
    assert events == [("delay", group_id)]


def test_reindex_foreign_group_returns_403_without_enqueue(
    test_client: TestClient, foreign_group_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import groups

    monkeypatch.setattr(
        groups,
        "reindex_group",
        type("FakeTask", (), {"delay": lambda self, group_id: pytest.fail("must not enqueue")})(),
    )

    response = test_client.post(f"/groups/{foreign_group_id}/reindex")

    assert response.status_code == 403, response.text
