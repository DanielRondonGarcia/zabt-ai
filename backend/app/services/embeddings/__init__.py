# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Embedding provider abstraction and registry."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Provider interface for embedding text into vector representations."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vector representations."""
        ...

    @property
    def dimension(self) -> int:
        """Return the configured vector dimension."""
        ...


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider, with no provider fallback."""
    from app.core.config import settings
    from app.services.embeddings.ollama import OllamaEmbeddingProvider
    from app.services.embeddings.openai import OpenAIEmbeddingProvider

    provider_name = (settings.EMBEDDING_PROVIDER or "").strip().lower()
    if provider_name == "ollama":
        return OllamaEmbeddingProvider()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider()
    raise ValueError(
        f"EMBEDDING_PROVIDER must be 'ollama' or 'openai', got {settings.EMBEDDING_PROVIDER!r}"
    )
