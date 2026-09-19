# SPDX-License-Identifier: AGPL-3.0-only
"""PR3a retrieval service tests with fakes only."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_search_authorizes_before_provider_or_vector_calls(monkeypatch):
    from app.services import retrieval as retrieval_module

    events: list[tuple[str, object]] = []

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            events.append(("authorize", (group_id, user_id)))
            return SimpleNamespace(id=group_id, owner_id=user_id)

    class FakeProvider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            events.append(("embed", texts))
            return [[0.1, 0.2]]

    class FakeStore:
        def search_filtered(self, embedding, *, owner_id, group_id, kinds=None, limit=10):
            events.append(("search", {"embedding": embedding, "owner_id": owner_id, "group_id": group_id, "kinds": kinds, "limit": limit}))
            return [
                {
                    "score": 0.9,
                    "payload": {
                        "meeting_id": 11,
                        "kind": "summary",
                        "chunk_index": 0,
                        "text": "safe result",
                    },
                }
            ]

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: FakeProvider())
    monkeypatch.setattr(retrieval_module, "get_vector_store", lambda: FakeStore())

    results = retrieval_module.GroupRetrievalService().search(
        group_id=7,
        user_id=3,
        query="find this",
        limit=5,
        kinds=["summary"],
    )

    assert [event[0] for event in events] == ["authorize", "embed", "search"]
    assert events[2][1]["owner_id"] == 3
    assert events[2][1]["group_id"] == 7
    assert results == [
        {
            "meeting_id": 11,
            "kind": "summary",
            "chunk_index": 0,
            "score": 0.9,
            "text": "safe result",
        }
    ]


def test_search_preserves_authorization_errors_and_skips_provider(monkeypatch):
    from app.services import retrieval as retrieval_module

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            raise HTTPException(status_code=403, detail="Access denied.")

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(
        retrieval_module,
        "get_embedding_provider",
        lambda: pytest.fail("provider must not be called before authorization"),
    )

    with pytest.raises(HTTPException) as exc_info:
        retrieval_module.GroupRetrievalService().search(7, 3, "query")

    assert exc_info.value.status_code == 403


def test_search_authorizes_then_returns_503_when_indexing_disabled(monkeypatch):
    from app.services import retrieval as retrieval_module

    events = []

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            events.append((group_id, user_id))
            return SimpleNamespace(id=group_id, owner_id=user_id)

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", False)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())

    with pytest.raises(HTTPException) as exc_info:
        retrieval_module.GroupRetrievalService().search(7, 3, "query")

    assert events == [(7, 3)]
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "retrieval unavailable"


@pytest.mark.parametrize("failure", ["provider", "vector"])
def test_search_translates_provider_or_vector_failures_to_503(monkeypatch, failure: str):
    from app.services import retrieval as retrieval_module

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            return SimpleNamespace(id=group_id, owner_id=user_id)

    class Provider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            if failure == "provider":
                raise RuntimeError("provider down")
            return [[1.0]]

    class Store:
        def search_filtered(self, *args, **kwargs):
            raise RuntimeError("qdrant down")

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: Provider())
    monkeypatch.setattr(retrieval_module, "get_vector_store", lambda: Store())

    with pytest.raises(HTTPException) as exc_info:
        retrieval_module.GroupRetrievalService().search(7, 3, "query")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "retrieval unavailable"
