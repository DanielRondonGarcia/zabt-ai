# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Qdrant vector store client for group-scoped embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings

try:  # Import-time optional until dependency is installed in every test environment.
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
except Exception:  # pragma: no cover - exercised only when dependency is absent.
    QdrantClient = None  # type: ignore[assignment]
    qdrant_models = None  # type: ignore[assignment]


@dataclass(frozen=True)
class EmbeddingPoint:
    id: str
    vector: list[float]
    text: str
    owner_id: int
    group_id: int
    meeting_id: int
    kind: str
    chunk_index: int
    chunk_count: int
    source_type: str
    model: str


class QdrantVectorStoreClient:
    """Thin wrapper around Qdrant collection management and filtered operations."""

    def __init__(self, client: Any | None = None) -> None:
        self.url = settings.QDRANT_URL
        self.api_key = str(getattr(settings, "QDRANT_API_KEY", "") or "").strip()
        self.collection_prefix = settings.QDRANT_COLLECTION_PREFIX
        self._client = client
        self._collection_ready = False

    @property
    def client(self) -> Any:
        if self._client is None:
            if QdrantClient is None:
                raise RuntimeError("qdrant-client is required for vector store operations")
            self._client = QdrantClient(
                url=self.url,
                api_key=self.api_key or None,
                timeout=30.0,
            )
        return self._client

    @property
    def active_collection(self) -> str:
        return f"{self.collection_prefix}_{settings.EMBEDDING_PROVIDER}_{settings.EMBEDDING_DIMENSION}"

    def ensure_collection(self) -> None:
        """Create the active collection and payload indexes if missing."""
        if self._collection_ready:
            return
        if qdrant_models is None:
            raise RuntimeError("qdrant-client is required for vector store operations")
        exists = False
        if hasattr(self.client, "collection_exists"):
            exists = bool(self.client.collection_exists(collection_name=self.active_collection))
        else:
            try:
                self.client.get_collection(self.active_collection)
                exists = True
            except Exception:
                exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.active_collection,
                vectors_config=qdrant_models.VectorParams(
                    size=settings.EMBEDDING_DIMENSION,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
        for field_name, schema in {
            "owner_id": qdrant_models.PayloadSchemaType.INTEGER,
            "group_id": qdrant_models.PayloadSchemaType.INTEGER,
            "meeting_id": qdrant_models.PayloadSchemaType.INTEGER,
            "kind": qdrant_models.PayloadSchemaType.KEYWORD,
        }.items():
            try:
                self.client.create_payload_index(
                    collection_name=self.active_collection,
                    field_name=field_name,
                    field_schema=schema,
                )
            except Exception:
                # Index creation is idempotent across Qdrant versions; existing indexes may raise.
                pass
        self._collection_ready = True

    def upsert_points(self, points: list[EmbeddingPoint]) -> None:
        """Upsert embedding points into the active collection."""
        if not points:
            return
        self.ensure_collection()
        qdrant_points = [
            qdrant_models.PointStruct(
                id=point.id,
                vector=point.vector,
                payload={
                    "text": point.text,
                    "owner_id": point.owner_id,
                    "group_id": point.group_id,
                    "meeting_id": point.meeting_id,
                    "kind": point.kind,
                    "chunk_index": point.chunk_index,
                    "chunk_count": point.chunk_count,
                    "source_type": point.source_type,
                    "model": point.model,
                },
            )
            for point in points
        ]
        self.client.upsert(collection_name=self.active_collection, points=qdrant_points)

    def delete_by_filter(
        self,
        *,
        owner_id: int | None = None,
        group_id: int | None = None,
        meeting_id: int | None = None,
    ) -> None:
        """Delete by owner/group/meeting filters. At least one filter is required."""
        if owner_id is None and group_id is None and meeting_id is None:
            raise ValueError("delete_by_filter requires at least one filter")
        self.ensure_collection()
        self.client.delete(
            collection_name=self.active_collection,
            points_selector=qdrant_models.FilterSelector(filter=self._filter(owner_id, group_id, meeting_id)),
        )

    def search_filtered(
        self,
        embedding: list[float],
        *,
        owner_id: int,
        group_id: int,
        kinds: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search with server-side owner + group filters and optional kind filter."""
        self.ensure_collection()
        query_filter = self._filter(owner_id, group_id, None, kinds=kinds)
        if hasattr(self.client, "search"):
            results = self.client.search(
                collection_name=self.active_collection,
                query_vector=embedding,
                query_filter=query_filter,
                limit=limit,
            )
        else:
            results = self.client.query_points(
                collection_name=self.active_collection,
                query=embedding,
                query_filter=query_filter,
                limit=limit,
            ).points
        return [
            {
                "id": point.id,
                "score": point.score,
                "payload": point.payload,
                "vector": getattr(point, "vector", None),
            }
            for point in results
        ]

    def _filter(
        self,
        owner_id: int | None,
        group_id: int | None,
        meeting_id: int | None,
        *,
        kinds: list[str] | None = None,
    ) -> Any:
        must = []
        if owner_id is not None:
            must.append(qdrant_models.FieldCondition(key="owner_id", match=qdrant_models.MatchValue(value=owner_id)))
        if group_id is not None:
            must.append(qdrant_models.FieldCondition(key="group_id", match=qdrant_models.MatchValue(value=group_id)))
        if meeting_id is not None:
            must.append(qdrant_models.FieldCondition(key="meeting_id", match=qdrant_models.MatchValue(value=meeting_id)))
        if kinds:
            must.append(qdrant_models.FieldCondition(key="kind", match=qdrant_models.MatchAny(any=kinds)))
        return qdrant_models.Filter(must=must)


def get_vector_store() -> QdrantVectorStoreClient:
    """Factory used by worker tasks and tests."""
    return QdrantVectorStoreClient()
