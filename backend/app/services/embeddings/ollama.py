# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Ollama embedding provider using an OpenAI-compatible endpoint."""

import httpx

from app.core.config import settings
from app.services.embeddings import EmbeddingProvider


class OllamaEmbeddingProvider(EmbeddingProvider):
    """HTTP adapter for Ollama's OpenAI-compatible /v1/embeddings route."""

    def __init__(self) -> None:
        self.base_url = settings.EMBEDDING_BASE_URL.rstrip("/")
        self.model = settings.EMBEDDING_MODEL.strip()
        self.api_key = settings.EMBEDDING_API_KEY.strip()
        self.max_batch = settings.EMBEDDING_MAX_BATCH
        if not self.base_url:
            raise ValueError("EMBEDDING_BASE_URL must not be empty")
        if not self.model:
            raise ValueError("EMBEDDING_MODEL must not be empty")
        if self.dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.max_batch <= 0:
            raise ValueError("EMBEDDING_MAX_BATCH must be positive")

    @property
    def dimension(self) -> int:
        """Return the configured vector dimension."""
        return settings.EMBEDDING_DIMENSION

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and fail if the returned vector size differs from config."""
        if not texts:
            return []
        if len(texts) > self.max_batch:
            raise ValueError(f"Batch size {len(texts)} exceeds maximum {self.max_batch}")

        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": texts},
                    headers=self._auth_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama embeddings API returned status {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Failed to connect to Ollama embeddings API: {exc}") from exc

        embeddings = self._parse_embeddings(response.json(), expected_count=len(texts))
        self._assert_dimensions(embeddings)
        return embeddings

    def _parse_embeddings(self, payload: dict, *, expected_count: int) -> list[list[float]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("Embeddings response must contain a data list")
        embeddings: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                raise ValueError("Embeddings response item is missing embedding list")
            embeddings.append([float(value) for value in embedding])
        if len(embeddings) != expected_count:
            raise ValueError(f"Embeddings response count mismatch: expected {expected_count}, got {len(embeddings)}")
        return embeddings

    def _assert_dimensions(self, embeddings: list[list[float]]) -> None:
        for embedding in embeddings:
            if len(embedding) != self.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(embedding)} "
                    f"for model {self.model!r}"
                )

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
