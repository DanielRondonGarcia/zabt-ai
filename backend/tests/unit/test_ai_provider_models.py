# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused model-catalog discovery tests without live provider calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.api.v1.endpoints import users
from app.models.ai_provider import CustomAIProvider
from app.services import ai_provider_models
from app.services.ai_provider_configuration import (
    AIProviderConfigurationValidationError,
)


class _FakeResponse:
    def __init__(self, body: bytes, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers = {"content-length": str(len(body))}
        self._body = body

    def iter_bytes(self):
        yield self._body


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *args):
        return False


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse], requests: list[dict], **kwargs) -> None:
        self.responses = responses
        self.requests = requests
        self.options = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method: str, url: str, *, headers: dict[str, str]):
        self.requests.append({"method": method, "url": url, "headers": headers})
        return _FakeStream(self.responses[url])


def _client_factory(payloads: dict[str, dict], requests: list[dict]):
    def factory(**kwargs):
        responses = {
            url: _FakeResponse(json.dumps(payload).encode())
            for url, payload in payloads.items()
        }
        return _FakeClient(responses, requests, **kwargs)

    return factory


def _allow_runtime_endpoint(*args, **kwargs) -> None:
    return None


def test_openai_compatible_discovery_uses_models_endpoint_and_sorts_ids() -> None:
    requests: list[dict] = []
    base_url = "https://api.example.com/v1"
    catalog = ai_provider_models.discover_model_catalog(
        provider=CustomAIProvider.OPENAI,
        base_url=base_url,
        api_key="sk-test",
        client_factory=_client_factory(
            {
                f"{base_url}/models": {
                    "object": "list",
                    "data": [{"id": "zeta"}, {"id": "Alpha"}, {"id": "alpha"}],
                }
            },
            requests,
        ),
        endpoint_validator=_allow_runtime_endpoint,
    )

    assert catalog.models == ["Alpha", "alpha", "zeta"]
    assert catalog.source == "openai-compatible"
    assert requests[0]["url"] == "https://api.example.com/v1/models"
    assert requests[0]["headers"] == {"Authorization": "Bearer sk-test"}
    assert requests[0]["method"] == "GET"


def test_anthropic_discovery_uses_documented_models_headers() -> None:
    requests: list[dict] = []
    catalog = ai_provider_models.discover_model_catalog(
        provider=CustomAIProvider.ANTHROPIC,
        base_url="https://api.anthropic.com",
        api_key="anthropic-test",
        client_factory=_client_factory(
            {
                "https://api.anthropic.com/v1/models": {
                    "data": [{"id": "claude-3"}],
                    "has_more": False,
                }
            },
            requests,
        ),
        endpoint_validator=_allow_runtime_endpoint,
    )

    assert catalog.models == ["claude-3"]
    assert catalog.source == "anthropic"
    assert requests[0]["url"] == "https://api.anthropic.com/v1/models"
    assert requests[0]["headers"] == {
        "x-api-key": "anthropic-test",
        "anthropic-version": "2023-06-01",
        "accept": "application/json",
    }


def test_ollama_falls_back_to_native_tags_for_local_v1_setup() -> None:
    requests: list[dict] = []
    base_url = "http://localhost:11434/v1"
    catalog = ai_provider_models.discover_model_catalog(
        provider=CustomAIProvider.OLLAMA,
        base_url=base_url,
        api_key=None,
        client_factory=_client_factory(
            {
                f"{base_url}/models": {"data": []},
                "http://localhost:11434/api/tags": {
                    "models": [{"name": "qwen3:8b"}, {"model": "llama3.2:3b"}]
                },
            },
            requests,
        ),
        endpoint_validator=_allow_runtime_endpoint,
    )

    assert catalog.models == ["llama3.2:3b", "qwen3:8b"]
    assert catalog.source == "ollama-tags"
    assert [request["url"] for request in requests] == [
        "http://localhost:11434/v1/models",
        "http://localhost:11434/api/tags",
    ]
    assert requests[0]["headers"] == {}


def test_model_ids_are_bounded_sorted_deduplicated_and_safe() -> None:
    payload = {
        "data": [
            {"id": f"model-{index:03d}"}
            for index in range(ai_provider_models.MAX_MODEL_DISCOVERY_MODELS + 20)
        ]
        + [{"id": "model-001"}, {"id": "bad\nmodel"}, {"id": "x" * 129}],
    }

    models = ai_provider_models.parse_openai_compatible_models(payload)

    assert len(models) == ai_provider_models.MAX_MODEL_DISCOVERY_MODELS
    assert models == sorted(models)
    assert models[0] == "model-000"
    assert models[-1] == "model-099"
    assert "bad\nmodel" not in models


def test_provider_url_policy_is_reused_before_discovery_request() -> None:
    with pytest.raises(AIProviderConfigurationValidationError):
        ai_provider_models.discover_model_catalog(
            provider=CustomAIProvider.OPENAI,
            base_url="http://api.example.com/v1",
            api_key="sk-test",
            endpoint_validator=_allow_runtime_endpoint,
        )

    with pytest.raises(AIProviderConfigurationValidationError):
        ai_provider_models.discover_model_catalog(
            provider=CustomAIProvider.OLLAMA,
            base_url="http://10.0.0.5:11434/v1",
            api_key=None,
            endpoint_validator=_allow_runtime_endpoint,
        )


def test_provider_errors_never_echo_credentials() -> None:
    secret = "sk-do-not-echo"

    def failing_client(**kwargs):
        raise RuntimeError(f"provider rejected {secret}")

    with pytest.raises(ai_provider_models.AIProviderModelDiscoveryError) as exc_info:
        ai_provider_models.discover_model_catalog(
            provider=CustomAIProvider.OPENAI,
            base_url="https://api.example.com/v1",
            api_key=secret,
            client_factory=failing_client,
            endpoint_validator=_allow_runtime_endpoint,
        )

    assert secret not in str(exc_info.value)


def test_response_body_is_bounded_before_json_parsing() -> None:
    base_url = "https://api.example.com/v1"
    requests: list[dict] = []

    def too_large_client(**kwargs):
        return _FakeClient(
            {
                f"{base_url}/models": _FakeResponse(
                    b"x" * (ai_provider_models.MAX_MODEL_DISCOVERY_RESPONSE_BYTES + 1)
                )
            },
            requests,
            **kwargs,
        )

    with pytest.raises(ai_provider_models.AIProviderModelDiscoveryError) as exc_info:
        ai_provider_models.discover_model_catalog(
            provider=CustomAIProvider.OPENAI,
            base_url=base_url,
            api_key="sk-test",
            client_factory=too_large_client,
            endpoint_validator=_allow_runtime_endpoint,
        )

    assert "response" not in str(exc_info.value).lower()


def test_models_endpoint_returns_only_safe_catalog_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-endpoint-secret"
    monkeypatch.setattr(
        users.ai_provider_model_discovery_service,
        "discover_for_user",
        lambda user_id, **kwargs: ai_provider_models.AIProviderModelCatalog(
            ["gpt-test"], "openai-compatible"
        ),
    )
    payload = users.AIProviderModelsRequest(
        provider=CustomAIProvider.OPENAI,
        base_url="https://api.example.com/v1",
        api_key=secret,
    )
    response = Response()

    result = users.discover_ai_provider_models(payload, response, SimpleNamespace(id=41))
    serialized = result.model_dump()

    assert serialized == {"models": ["gpt-test"], "source": "openai-compatible"}
    assert secret not in repr(serialized)
    assert response.headers["cache-control"] == "no-store"


def test_stored_key_is_used_only_for_the_same_saved_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = SimpleNamespace(
        provider=CustomAIProvider.OPENAI,
        base_url="https://api.example.com/v1",
        encrypted_api_key="ciphertext",
    )
    captured: list[str | None] = []
    service = ai_provider_models.AIProviderModelDiscoveryService(
        configuration_service=SimpleNamespace(get_for_user=lambda user_id: configuration),
        encryption_service=SimpleNamespace(decrypt_token=lambda value: "stored-secret"),
    )
    monkeypatch.setattr(
        ai_provider_models,
        "discover_model_catalog",
        lambda **kwargs: captured.append(kwargs["api_key"])
        or ai_provider_models.AIProviderModelCatalog([], "openai-compatible"),
    )

    service.discover_for_user(
        7,
        provider=CustomAIProvider.OPENAI,
        base_url="https://api.example.com/v1",
        api_key=None,
        fields_set={"provider", "base_url"},
    )
    service.discover_for_user(
        7,
        provider=CustomAIProvider.OPENAI,
        base_url="https://other.example.com/v1",
        api_key=None,
        fields_set={"provider", "base_url"},
    )

    assert captured == ["stored-secret", None]
