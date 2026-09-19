# SPDX-License-Identifier: AGPL-3.0-only
"""Embedding provider smoke tests kept outside app/tests for legacy discovery."""

from app.services.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.embeddings.ollama import OllamaEmbeddingProvider


def test_provider_protocol_exposes_required_members():
    assert hasattr(EmbeddingProvider, "embed")
    assert isinstance(getattr(EmbeddingProvider, "dimension"), property)


def test_default_dispatch_returns_ollama_provider():
    assert isinstance(get_embedding_provider(), OllamaEmbeddingProvider)
