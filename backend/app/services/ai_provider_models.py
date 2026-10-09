# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Transient model-catalog discovery for user-owned AI providers."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.models.ai_provider import AIProviderConfiguration, CustomAIProvider
from app.services.ai_provider import (
    MAX_PROVIDER_RETRIES,
    PROVIDER_TIMEOUT_SECONDS,
)
from app.services.ai_provider_configuration import (
    DEFAULT_BASE_URLS,
    MAX_MODEL_LENGTH,
    AIProviderConfigurationService,
    AIProviderConfigurationValidationError,
    ai_provider_configuration_service,
    normalize_api_key,
    normalize_base_url,
    normalize_provider,
    validate_api_key_transport,
    validate_runtime_provider_endpoint,
)
from app.services.integration import IntegrationService, integration_service


MAX_MODEL_DISCOVERY_MODELS = 100
MAX_MODEL_DISCOVERY_RESPONSE_BYTES = 256 * 1024

ModelCatalogSource = Literal[
    "openai-compatible",
    "anthropic",
    "ollama-compatible",
    "ollama-tags",
]


class AIProviderModelDiscoveryError(RuntimeError):
    """Raised with a safe message when a provider catalog cannot be loaded."""


@dataclass(frozen=True)
class AIProviderModelCatalog:
    models: list[str]
    source: ModelCatalogSource


class _CatalogRequestError(RuntimeError):
    def __init__(self, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__()
        self.status_code = status_code
        self.retryable = retryable


def _safe_model_ids(candidates: list[Any]) -> list[str]:
    identifiers: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        identifier = candidate.strip()
        if not identifier or len(identifier) > MAX_MODEL_LENGTH:
            continue
        if any(ord(character) < 32 or ord(character) == 127 for character in identifier):
            continue
        identifiers.add(identifier)

    return sorted(identifiers, key=lambda value: (value.casefold(), value))[
        :MAX_MODEL_DISCOVERY_MODELS
    ]


def parse_openai_compatible_models(payload: object) -> list[str]:
    """Extract model IDs from the OpenAI-compatible list response."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        return []
    return _safe_model_ids(
        [
            item.get("id")
            for item in payload["data"]
            if isinstance(item, Mapping)
        ]
    )


def parse_anthropic_models(payload: object) -> list[str]:
    """Extract model IDs from Anthropic's paginated list response."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        return []
    return _safe_model_ids(
        [
            item.get("id")
            for item in payload["data"]
            if isinstance(item, Mapping)
        ]
    )


def parse_ollama_tags_models(payload: object) -> list[str]:
    """Extract model names from Ollama's native /api/tags response."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("models"), list):
        return []
    return _safe_model_ids(
        [
            item.get("name") or item.get("model")
            for item in payload["models"]
            if isinstance(item, Mapping)
        ]
    )


def _append_path(base_url: str, suffix: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{path}/{suffix.lstrip('/')}",
            "",
            "",
        )
    )


def _anthropic_models_url(base_url: str) -> str:
    path = urlsplit(base_url).path.rstrip("/").casefold()
    return _append_path(base_url, "models" if path.endswith("/v1") else "v1/models")


def _ollama_tags_url(base_url: str) -> str | None:
    """Return the native endpoint only for the documented root or /v1 setup."""
    path = urlsplit(base_url).path.rstrip("/").casefold()
    if path not in {"", "/v1"}:
        return None
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/tags", "", ""))


def _read_response_body(response: Any) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            raise _CatalogRequestError() from None
        if declared_length < 0 or declared_length > MAX_MODEL_DISCOVERY_RESPONSE_BYTES:
            raise _CatalogRequestError()

    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_MODEL_DISCOVERY_RESPONSE_BYTES:
            raise _CatalogRequestError()
    return bytes(body)


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str],
    client_factory: Callable[..., Any],
) -> object:
    for attempt in range(MAX_PROVIDER_RETRIES + 1):
        try:
            with client_factory(
                timeout=PROVIDER_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                with client.stream("GET", url, headers=dict(headers)) as response:
                    status_code = int(response.status_code)
                    if not 200 <= status_code < 300:
                        raise _CatalogRequestError(
                            status_code=status_code,
                            retryable=status_code >= 500,
                        )
                    body = _read_response_body(response)
            return json.loads(body)
        except _CatalogRequestError as exc:
            if exc.retryable and attempt < MAX_PROVIDER_RETRIES:
                continue
            raise AIProviderModelDiscoveryError(
                "The provider model catalog is unavailable. Check the base URL, credentials, and provider availability."
            ) from None
        except httpx.RequestError:
            if attempt < MAX_PROVIDER_RETRIES:
                continue
            raise AIProviderModelDiscoveryError(
                "The provider model catalog is unavailable. Check the base URL, credentials, and provider availability."
            ) from None
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise AIProviderModelDiscoveryError(
                "The provider returned an invalid model catalog. Enter a model ID manually or check the provider."
            ) from None
        except Exception:
            raise AIProviderModelDiscoveryError(
                "The provider model catalog is unavailable. Check the base URL, credentials, and provider availability."
            ) from None

    raise AIProviderModelDiscoveryError(
        "The provider model catalog is unavailable. Check the base URL, credentials, and provider availability."
    )


def _authorization_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def discover_model_catalog(
    *,
    provider: CustomAIProvider | str,
    base_url: str,
    api_key: str | None,
    client_factory: Callable[..., Any] | None = None,
    endpoint_validator: Callable[..., None] | None = None,
) -> AIProviderModelCatalog:
    """Load a bounded, provider-specific model catalog without persisting secrets."""
    selected_provider = normalize_provider(provider)
    normalized_base_url = normalize_base_url(base_url, provider=selected_provider)
    if not normalized_base_url:
        raise AIProviderConfigurationValidationError("base_url is required")

    normalized_api_key = normalize_api_key(api_key)
    validate_api_key_transport(
        normalized_base_url,
        provider=selected_provider,
        has_api_key=bool(normalized_api_key),
    )
    if selected_provider in {CustomAIProvider.OPENAI, CustomAIProvider.ANTHROPIC} and not normalized_api_key:
        raise AIProviderModelDiscoveryError(
            "An API key is required to load models for this provider."
        )

    (endpoint_validator or validate_runtime_provider_endpoint)(
        normalized_base_url,
        provider=selected_provider,
    )

    request_client = client_factory or httpx.Client
    if selected_provider == CustomAIProvider.ANTHROPIC:
        body = _request_json(
            _anthropic_models_url(normalized_base_url),
            headers={
                "x-api-key": normalized_api_key or "",
                "anthropic-version": "2023-06-01",
                "accept": "application/json",
            },
            client_factory=request_client,
        )
        return AIProviderModelCatalog(
            models=parse_anthropic_models(body),
            source="anthropic",
        )

    headers = _authorization_headers(normalized_api_key)
    if selected_provider == CustomAIProvider.OPENAI:
        body = _request_json(
            _append_path(normalized_base_url, "models"),
            headers=headers,
            client_factory=request_client,
        )
        return AIProviderModelCatalog(
            models=parse_openai_compatible_models(body),
            source="openai-compatible",
        )

    compatible_error: AIProviderModelDiscoveryError | None = None
    try:
        body = _request_json(
            _append_path(normalized_base_url, "models"),
            headers=headers,
            client_factory=request_client,
        )
        compatible_models = parse_openai_compatible_models(body)
        if compatible_models:
            return AIProviderModelCatalog(
                models=compatible_models,
                source="ollama-compatible",
            )
    except AIProviderModelDiscoveryError as exc:
        compatible_error = exc

    tags_url = _ollama_tags_url(normalized_base_url)
    if tags_url is None:
        if compatible_error is not None:
            raise compatible_error
        return AIProviderModelCatalog(models=[], source="ollama-compatible")

    try:
        body = _request_json(
            tags_url,
            headers=headers,
            client_factory=request_client,
        )
    except AIProviderModelDiscoveryError:
        raise AIProviderModelDiscoveryError(
            "The Ollama model catalog is unavailable. Check that Ollama is running and try again."
        ) from None
    return AIProviderModelCatalog(
        models=parse_ollama_tags_models(body),
        source="ollama-tags",
    )


class AIProviderModelDiscoveryService:
    """Resolve transient credentials inside the server-side discovery boundary."""

    def __init__(
        self,
        *,
        configuration_service: AIProviderConfigurationService | None = None,
        encryption_service: IntegrationService | None = None,
    ) -> None:
        self._configuration_service = (
            configuration_service or ai_provider_configuration_service
        )
        self._encryption_service = encryption_service or integration_service

    def discover_for_user(
        self,
        user_id: int,
        *,
        provider: CustomAIProvider | str | None,
        base_url: str | None,
        api_key: str | None,
        fields_set: set[str],
    ) -> AIProviderModelCatalog:
        stored = self._configuration_service.get_for_user(user_id)
        stored_provider = (
            normalize_provider(stored.provider) if stored is not None else None
        )
        selected_provider = (
            normalize_provider(provider) if provider is not None else stored_provider
        )
        if selected_provider is None:
            raise AIProviderConfigurationValidationError(
                "provider is required when loading models"
            )

        provider_changed = stored_provider is not None and selected_provider != stored_provider
        selected_base_url = base_url
        if selected_base_url is None and not provider_changed and stored is not None:
            selected_base_url = stored.base_url
        selected_base_url = normalize_base_url(
            selected_base_url or DEFAULT_BASE_URLS[selected_provider],
            provider=selected_provider,
        )
        if not selected_base_url:
            raise AIProviderConfigurationValidationError("base_url is required")

        if "api_key" in fields_set:
            selected_api_key = normalize_api_key(api_key)
        else:
            selected_api_key = self._stored_api_key_if_same_endpoint(
                stored,
                stored_provider=stored_provider,
                selected_provider=selected_provider,
                selected_base_url=selected_base_url,
            )

        return discover_model_catalog(
            provider=selected_provider,
            base_url=selected_base_url,
            api_key=selected_api_key,
        )

    def _stored_api_key_if_same_endpoint(
        self,
        configuration: AIProviderConfiguration | None,
        *,
        stored_provider: CustomAIProvider | None,
        selected_provider: CustomAIProvider,
        selected_base_url: str,
    ) -> str | None:
        if (
            configuration is None
            or stored_provider != selected_provider
            or not configuration.encrypted_api_key
        ):
            return None

        stored_base_url = normalize_base_url(
            configuration.base_url or DEFAULT_BASE_URLS[selected_provider],
            provider=selected_provider,
        )
        if stored_base_url != selected_base_url:
            return None

        try:
            return normalize_api_key(
                self._encryption_service.decrypt_token(configuration.encrypted_api_key)
            )
        except Exception:
            raise AIProviderModelDiscoveryError(
                "The stored provider credential is unavailable. Enter it again."
            ) from None


ai_provider_model_discovery_service = AIProviderModelDiscoveryService()
