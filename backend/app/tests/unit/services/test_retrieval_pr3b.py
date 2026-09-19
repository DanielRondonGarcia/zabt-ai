# SPDX-License-Identifier: AGPL-3.0-only
"""PR3b release-blocking retrieval isolation tests with local fakes only."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_foreign_group_returns_403_before_provider_or_vector_calls(monkeypatch):
    from app.services import retrieval as retrieval_module

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            raise HTTPException(status_code=403, detail="Access denied.")

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(
        retrieval_module,
        "get_embedding_provider",
        lambda: pytest.fail("foreign group must not call embedding provider"),
    )
    monkeypatch.setattr(
        retrieval_module,
        "get_vector_store",
        lambda: pytest.fail("foreign group must not call vector store"),
    )

    with pytest.raises(HTTPException) as exc_info:
        retrieval_module.GroupRetrievalService().search(99, 1, "private query")

    assert exc_info.value.status_code == 403


def test_cross_owner_and_cross_group_results_are_filtered_server_side(monkeypatch):
    from app.services import retrieval as retrieval_module

    corpus = [
        {"score": 0.99, "payload": {"owner_id": 1, "group_id": 10, "meeting_id": 101, "kind": "summary", "chunk_index": 0, "text": "allowed"}},
        {"score": 0.98, "payload": {"owner_id": 2, "group_id": 10, "meeting_id": 201, "kind": "summary", "chunk_index": 0, "text": "foreign owner"}},
        {"score": 0.97, "payload": {"owner_id": 1, "group_id": 11, "meeting_id": 102, "kind": "summary", "chunk_index": 0, "text": "foreign group"}},
    ]
    observed_filters = []

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            return SimpleNamespace(id=group_id, owner_id=user_id)

    class FakeProvider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3]]

    class FilteringStore:
        def search_filtered(self, embedding, *, owner_id, group_id, kinds=None, limit=10):
            observed_filters.append({"owner_id": owner_id, "group_id": group_id, "kinds": kinds, "limit": limit})
            return [
                item
                for item in corpus
                if item["payload"]["owner_id"] == owner_id and item["payload"]["group_id"] == group_id
            ][:limit]

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: FakeProvider())
    monkeypatch.setattr(retrieval_module, "get_vector_store", lambda: FilteringStore())

    results = retrieval_module.GroupRetrievalService().search(group_id=10, user_id=1, query="allowed")

    assert observed_filters == [{"owner_id": 1, "group_id": 10, "kinds": None, "limit": 10}]
    assert results == [
        {"meeting_id": 101, "kind": "summary", "chunk_index": 0, "score": 0.99, "text": "allowed"}
    ]


def test_cross_group_search_returns_empty_when_no_authorized_group_vectors(monkeypatch):
    from app.services import retrieval as retrieval_module

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            return SimpleNamespace(id=group_id, owner_id=user_id)

    class FakeProvider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.2, 0.3, 0.4]]

    class FilteringStore:
        def search_filtered(self, embedding, *, owner_id, group_id, kinds=None, limit=10):
            assert owner_id == 1
            assert group_id == 20
            return []

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: FakeProvider())
    monkeypatch.setattr(retrieval_module, "get_vector_store", lambda: FilteringStore())

    assert retrieval_module.GroupRetrievalService().search(group_id=20, user_id=1, query="other group") == []


@pytest.mark.parametrize("failure_point", ["provider", "qdrant"])
def test_provider_or_qdrant_unavailable_returns_503_with_no_partial_results(monkeypatch, failure_point: str):
    from app.services import retrieval as retrieval_module

    class FakeGroupService:
        def get_accessible(self, group_id: int, user_id: int):
            return SimpleNamespace(id=group_id, owner_id=user_id)

    class Provider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            if failure_point == "provider":
                raise RuntimeError("embedding provider unavailable")
            return [[0.1, 0.2, 0.3]]

    class Store:
        def search_filtered(self, embedding, *, owner_id, group_id, kinds=None, limit=10):
            raise TimeoutError("qdrant unavailable")

    monkeypatch.setattr(retrieval_module.settings, "INDEXING_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "group_service", FakeGroupService())
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: Provider())
    monkeypatch.setattr(retrieval_module, "get_vector_store", lambda: Store())

    with pytest.raises(HTTPException) as exc_info:
        retrieval_module.GroupRetrievalService().search(group_id=7, user_id=3, query="query")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "retrieval unavailable"


def _filter_conditions(query_filter):
    return {condition.key: condition.match for condition in query_filter.must}


def test_vector_store_builds_hard_owner_group_kind_server_side_filter(monkeypatch):
    from app.services.vector_store import QdrantVectorStoreClient

    captured = {}

    class FakeQdrant:
        def collection_exists(self, collection_name):
            return True

        def create_payload_index(self, collection_name, field_name, field_schema):
            return None

        def search(self, collection_name, query_vector, query_filter, limit):
            captured["filter"] = query_filter
            captured["limit"] = limit
            return []

    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.vector_store.settings.QDRANT_COLLECTION_PREFIX", "meeting_embeddings")

    client = QdrantVectorStoreClient(client=FakeQdrant())
    assert client.search_filtered([0.1, 0.2, 0.3], owner_id=44, group_id=55, kinds=["summary"], limit=4) == []

    conditions = _filter_conditions(captured["filter"])
    assert set(conditions) == {"owner_id", "group_id", "kind"}
    assert conditions["owner_id"].value == 44
    assert conditions["group_id"].value == 55
    assert conditions["kind"].any == ["summary"]
    assert captured["limit"] == 4
