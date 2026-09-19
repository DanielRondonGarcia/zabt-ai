# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Authorized group-scoped retrieval over indexed meeting chunks."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embeddings import get_embedding_provider
from app.services.group import group_service
from app.services.vector_store import get_vector_store

logger = get_logger(__name__)

_RETRIEVAL_UNAVAILABLE = "retrieval unavailable"


class GroupRetrievalService:
    """Search only vectors authorized by server-side group ownership checks."""

    def search(
        self,
        group_id: int,
        user_id: int,
        query: str,
        limit: int = 10,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ranked group results after authorization and hard filters.

        Authorization is resolved before touching the embedding provider or vector
        store. Runtime provider/vector failures are translated to one 503 shape;
        403/404 authorization exceptions are preserved.
        """
        group_service.get_accessible(group_id, user_id)

        if not settings.INDEXING_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RETRIEVAL_UNAVAILABLE,
            )

        try:
            vectors = get_embedding_provider().embed([query])
            if not vectors or not vectors[0]:
                raise RuntimeError("embedding provider returned no query vector")
            raw_results = get_vector_store().search_filtered(
                vectors[0],
                owner_id=user_id,
                group_id=group_id,
                kinds=kinds,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "group retrieval unavailable group_id=%s user_id=%s kinds=%s limit=%s",
                group_id,
                user_id,
                kinds,
                limit,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RETRIEVAL_UNAVAILABLE,
            ) from None

        return [self._to_result(item) for item in raw_results]

    @staticmethod
    def _to_result(item: dict[str, Any]) -> dict[str, Any]:
        payload = item.get("payload") or {}
        return {
            "meeting_id": payload.get("meeting_id"),
            "kind": payload.get("kind"),
            "chunk_index": payload.get("chunk_index"),
            "score": item.get("score"),
            "text": payload.get("text") or "",
        }


retrieval_service = GroupRetrievalService()
