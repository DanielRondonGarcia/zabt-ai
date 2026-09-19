# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused Qdrant client configuration tests without network calls."""

from __future__ import annotations

from unittest.mock import Mock

from app.services import vector_store


def test_qdrant_client_passes_configured_api_key(monkeypatch) -> None:
    monkeypatch.setattr(vector_store.settings, "QDRANT_URL", "https://qdrant.example")
    monkeypatch.setattr(vector_store.settings, "QDRANT_API_KEY", " qdrant-secret ", raising=False)
    constructor = Mock(return_value=object())
    monkeypatch.setattr(vector_store, "QdrantClient", constructor)

    client = vector_store.QdrantVectorStoreClient()

    assert client.client is constructor.return_value
    constructor.assert_called_once_with(
        url="https://qdrant.example",
        api_key="qdrant-secret",
        timeout=30.0,
    )


def test_qdrant_client_omits_empty_api_key(monkeypatch) -> None:
    monkeypatch.setattr(vector_store.settings, "QDRANT_URL", "https://qdrant.example")
    monkeypatch.setattr(vector_store.settings, "QDRANT_API_KEY", "", raising=False)
    constructor = Mock(return_value=object())
    monkeypatch.setattr(vector_store, "QdrantClient", constructor)

    _ = vector_store.QdrantVectorStoreClient().client

    constructor.assert_called_once_with(
        url="https://qdrant.example",
        api_key=None,
        timeout=30.0,
    )
