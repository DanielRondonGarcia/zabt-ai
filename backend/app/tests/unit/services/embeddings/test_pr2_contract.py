# SPDX-License-Identifier: AGPL-3.0-only
"""PR2 embedding/indexing contract tests."""

from types import SimpleNamespace
from uuid import uuid5, NAMESPACE_OID

import httpx
import pytest

from app.services.embeddings.canonicalize import canonicalize_text, flatten_structured_output
from app.services.embeddings.chunk import build_meeting_chunks, point_id_for
from app.services.embeddings.ollama import OllamaEmbeddingProvider
from app.services.embeddings.openai import OpenAIEmbeddingProvider
from app.services.vector_store import EmbeddingPoint, QdrantVectorStoreClient


def test_canonicalize_and_flatten_are_deterministic():
    assert canonicalize_text("  alpha\n\t beta   gamma  ") == "alpha beta gamma"
    payload = {"b": [2, {"d": " four "}], "a": {"c": 3}}
    assert flatten_structured_output(payload) == "a.c: 3 b.0: 2 b.1.d: four"


def test_chunking_uses_overlap_and_stable_ids():
    text = " ".join(f"w{i}" for i in range(900))
    chunks = build_meeting_chunks(
        meeting_id=123,
        transcript_text=text,
        summary_text="short summary",
    )

    transcript_chunks = [chunk for chunk in chunks if chunk.kind == "transcript"]
    assert [chunk.chunk_index for chunk in transcript_chunks] == [0, 1, 2]
    assert transcript_chunks[0].text.split()[-200:] == transcript_chunks[1].text.split()[:200]
    assert chunks[0].id == str(uuid5(NAMESPACE_OID, "123:summary:0"))
    assert point_id_for(123, "transcript", 2) == str(uuid5(NAMESPACE_OID, "123:transcript:2"))


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(self.status_code, text=self.text))


class _FakeHttpClient:
    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, json, headers, timeout):
        self.__class__.last_request = (url, json, headers, timeout)
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in json["input"]]})


def test_ollama_provider_enforces_configured_dimension_and_batch(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.ollama.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_BASE_URL", "http://ollama.test/v1")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MODEL", "nomic")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MAX_BATCH", 1)
    provider = OllamaEmbeddingProvider()

    assert provider.dimension == 3
    assert provider.embed(["hello"]) == [[0.1, 0.2, 0.3]]
    assert _FakeHttpClient.last_request[0] == "http://ollama.test/v1/embeddings"
    with pytest.raises(ValueError, match="Batch size"):
        provider.embed(["a", "b"])


def test_openai_provider_requires_key_and_dimension(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "")
    monkeypatch.setattr("app.services.embeddings.openai.settings.OPENAI_API_KEY", "")
    with pytest.raises(ValueError, match="required"):
        OpenAIEmbeddingProvider()

    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 4)
    provider = OpenAIEmbeddingProvider()
    with pytest.raises(ValueError, match="dimension mismatch"):
        provider.embed(["hello"])


def test_openai_provider_uses_actsis_key_for_custom_endpoint(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://ai.actsis.internal/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "nomic-embed")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "")
    monkeypatch.setattr("app.services.embeddings.openai.settings.ACTSIS_API_KEY", "actsis-test-key")
    monkeypatch.setattr("app.services.embeddings.openai.settings.OPENAI_API_KEY", "official-key-must-not-leak")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 3)

    provider = OpenAIEmbeddingProvider()
    assert provider.embed(["hello"]) == [[0.1, 0.2, 0.3]]
    assert _FakeHttpClient.last_request[0] == "https://ai.actsis.internal/v1/embeddings"
    assert _FakeHttpClient.last_request[2]["Authorization"] == "Bearer actsis-test-key"


class _FakeQdrant:
    def __init__(self):
        self.collections = set()
        self.upserts = []
        self.deletes = []
        self.searches = []
        self.indexes = []

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections.add(collection_name)
        self.vectors_config = vectors_config

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes.append((collection_name, field_name, field_schema))

    def upsert(self, collection_name, points):
        self.upserts.append((collection_name, points))

    def delete(self, collection_name, points_selector):
        self.deletes.append((collection_name, points_selector))

    def search(self, collection_name, query_vector, query_filter, limit):
        self.searches.append((collection_name, query_vector, query_filter, limit))
        return [SimpleNamespace(id="p1", score=0.9, payload={"meeting_id": 7}, vector=None)]


def test_vector_store_filters_owner_group_and_uses_deterministic_ids(monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.vector_store.settings.QDRANT_COLLECTION_PREFIX", "meeting_embeddings")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_MODEL", "nomic")
    client = QdrantVectorStoreClient(client=fake)

    client.upsert_points([
        EmbeddingPoint(
            id=point_id_for(7, "summary", 0),
            vector=[0.1, 0.2, 0.3],
            text="summary",
            owner_id=5,
            group_id=9,
            meeting_id=7,
            kind="summary",
            chunk_index=0,
            chunk_count=1,
            source_type="upload",
            model="nomic",
        )
    ])
    client.delete_by_filter(owner_id=5, group_id=9, meeting_id=7)
    result = client.search_filtered([0.1, 0.2, 0.3], owner_id=5, group_id=9, kinds=["summary"], limit=3)

    assert fake.upserts[0][0] == "meeting_embeddings_ollama_3"
    assert fake.upserts[0][1][0].id == point_id_for(7, "summary", 0)
    assert result[0]["payload"] == {"meeting_id": 7}
    search_filter = fake.searches[0][2]
    rendered = repr(search_filter)
    assert "owner_id" in rendered and "group_id" in rendered and "summary" in rendered
