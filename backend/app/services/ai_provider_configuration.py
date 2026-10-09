# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistence and validation for user-owned AI provider settings."""

import ipaddress
import re
import socket
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from sqlmodel import Session, select

from app.db.engine import engine
from app.models.ai_provider import AIProviderConfiguration, CustomAIProvider
from app.services.integration import IntegrationService, integration_service

MAX_MODEL_LENGTH = 128
MAX_BASE_URL_LENGTH = 2048
MAX_API_KEY_LENGTH = 4096

_DNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_HOSTNAME_RE = re.compile(rf"(?=.{{1,253}}\Z){_DNS_LABEL}(?:\.{_DNS_LABEL})*\Z")
_OLLAMA_LOCAL_HOSTS = frozenset(
    {"127.0.0.1", "::1", "localhost", "host.docker.internal"}
)
_UNSAFE_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.google",
    }
)
_UNSAFE_HOST_SUFFIXES = (
    ".home.arpa",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localhost",
    ".test",
)

DEFAULT_MODELS: dict[CustomAIProvider, str] = {
    CustomAIProvider.OPENAI: "gpt-4o-mini",
    CustomAIProvider.ANTHROPIC: "claude-3-5-haiku-latest",
    CustomAIProvider.OLLAMA: "gemma4:31b",
}

DEFAULT_BASE_URLS: dict[CustomAIProvider, str] = {
    CustomAIProvider.OPENAI: "https://api.openai.com/v1",
    CustomAIProvider.ANTHROPIC: "https://api.anthropic.com",
    CustomAIProvider.OLLAMA: "https://ollama.com/v1",
}


class AIProviderConfigurationValidationError(ValueError):
    """Raised when a provider configuration is invalid."""


class AIProviderSecretStorageError(RuntimeError):
    """Raised when a supplied secret cannot be encrypted."""


def normalize_provider(value: CustomAIProvider | str) -> CustomAIProvider:
    try:
        return CustomAIProvider(value)
    except (TypeError, ValueError):
        raise AIProviderConfigurationValidationError(
            "provider must be one of: openai, anthropic, ollama"
        ) from None


def normalize_model(value: str) -> str:
    if not isinstance(value, str):
        raise AIProviderConfigurationValidationError("model must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_MODEL_LENGTH:
        raise AIProviderConfigurationValidationError(
            f"model must contain 1 to {MAX_MODEL_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AIProviderConfigurationValidationError("model must not contain control characters")
    return normalized


def _normalize_hostname(hostname: str) -> str:
    try:
        return hostname.encode("ascii").decode("ascii").casefold()
    except UnicodeError:
        raise AIProviderConfigurationValidationError(
            "base_url host must use a DNS-safe ASCII hostname"
        ) from None


def _validate_dns_hostname(hostname: str) -> None:
    if (
        not _DNS_HOSTNAME_RE.fullmatch(hostname)
        or all(character.isdigit() or character == "." for character in hostname)
        or ("." not in hostname and hostname.isdigit())
        or (
            "." not in hostname
            and hostname.startswith("0x")
            and len(hostname) > 2
            and all(character in "0123456789abcdef" for character in hostname[2:])
        )
    ):
        raise AIProviderConfigurationValidationError(
            "base_url host must use a DNS-safe hostname"
        )


def _validate_provider_base_url(
    parsed,
    hostname: str,
    provider: CustomAIProvider,
) -> None:
    normalized_hostname = _normalize_hostname(hostname)
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
        _validate_dns_hostname(normalized_hostname)

    if provider == CustomAIProvider.OLLAMA:
        # Ollama is the only user-configured provider allowed to use local HTTP.
        # Public endpoints still use the same HTTPS and public-host policy as the
        # other hosted providers; runtime DNS validation remains the final guard.
        if normalized_hostname in _OLLAMA_LOCAL_HOSTS:
            return

    if parsed.scheme.casefold() != "https":
        if provider == CustomAIProvider.OLLAMA:
            raise AIProviderConfigurationValidationError(
                "ollama public endpoints must use HTTPS; keyless HTTP is limited to localhost"
            )
        raise AIProviderConfigurationValidationError(
            "base_url must use HTTPS for OpenAI and Anthropic providers"
        )

    if (
        normalized_hostname in _UNSAFE_HOSTNAMES
        or normalized_hostname.endswith(_UNSAFE_HOST_SUFFIXES)
    ):
        raise AIProviderConfigurationValidationError(
            "base_url host must be a public DNS hostname"
        )

    if address is not None and not address.is_global:
        raise AIProviderConfigurationValidationError(
            "base_url host must not be private, reserved, loopback, or link-local"
        )

    if address is None and "." not in normalized_hostname:
        raise AIProviderConfigurationValidationError(
            "base_url host must be a public DNS hostname"
        )


def _validate_unscoped_base_url(hostname: str) -> None:
    normalized_hostname = _normalize_hostname(hostname)
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
        _validate_dns_hostname(normalized_hostname)

    if normalized_hostname in _OLLAMA_LOCAL_HOSTS:
        return
    if address is not None and not address.is_global:
        raise AIProviderConfigurationValidationError(
            "base_url host must not be private, reserved, loopback, or link-local"
        )
    if (
        address is None
        and (
            normalized_hostname in _UNSAFE_HOSTNAMES
            or normalized_hostname.endswith(_UNSAFE_HOST_SUFFIXES)
            or "." not in normalized_hostname
        )
    ):
        raise AIProviderConfigurationValidationError(
            "base_url host must be a public DNS hostname"
        )


def normalize_base_url(
    value: str | None,
    *,
    provider: CustomAIProvider | str | None = None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AIProviderConfigurationValidationError("base_url must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_BASE_URL_LENGTH:
        raise AIProviderConfigurationValidationError(
            f"base_url must contain at most {MAX_BASE_URL_LENGTH} characters"
        )
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in normalized
    ) or "\\" in normalized:
        raise AIProviderConfigurationValidationError(
            "base_url must be an http(s) URL without whitespace"
        )
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        parsed = None
        hostname = None
        port = None
    if (
        parsed is None
        or parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or "@" in parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or "?" in normalized
        or "#" in normalized
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise AIProviderConfigurationValidationError(
            "base_url must be an http(s) URL without credentials, query parameters, or fragments"
        )
    if provider is not None:
        _validate_provider_base_url(parsed, hostname, normalize_provider(provider))
    else:
        _validate_unscoped_base_url(hostname)
    return normalized.rstrip("/")


def normalize_api_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AIProviderConfigurationValidationError("api_key must be a string")
    normalized = value.strip()
    if len(normalized) > MAX_API_KEY_LENGTH:
        raise AIProviderConfigurationValidationError(
            f"api_key must contain at most {MAX_API_KEY_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AIProviderConfigurationValidationError("api_key must not contain control characters")
    return normalized or None


def validate_api_key_transport(
    base_url: str,
    *,
    provider: CustomAIProvider | str,
    has_api_key: bool,
) -> None:
    """Enforce HTTPS credentials and keep keyless Ollama local-only."""
    selected_provider = normalize_provider(provider)
    if selected_provider == CustomAIProvider.OLLAMA:
        hostname = (urlsplit(base_url).hostname or "").casefold()
        if not has_api_key and hostname not in _OLLAMA_LOCAL_HOSTS:
            raise AIProviderConfigurationValidationError(
                "Ollama cloud endpoints require an API key; keyless Ollama is limited to localhost"
            )
    if has_api_key and urlsplit(base_url).scheme.casefold() != "https":
        if selected_provider == CustomAIProvider.OLLAMA:
            raise AIProviderConfigurationValidationError(
                "Ollama API keys require an HTTPS base URL; local HTTP Ollama endpoints must not include an API key"
            )
        raise AIProviderConfigurationValidationError(
            "API keys require an HTTPS base URL"
        )


def _is_public_resolved_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def validate_runtime_provider_endpoint(
    base_url: str,
    *,
    provider: CustomAIProvider | str,
    resolver: Callable[..., list[tuple]] | None = None,
) -> None:
    """Reject custom endpoints whose current DNS answers are not public."""
    selected_provider = normalize_provider(provider)
    normalized = normalize_base_url(base_url, provider=selected_provider)
    if not normalized:
        raise AIProviderConfigurationValidationError("base_url is required")

    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise AIProviderConfigurationValidationError("base_url host is required")
    normalized_hostname = _normalize_hostname(hostname)

    # These names are the deliberate local-development exception for Ollama.
    if (
        selected_provider == CustomAIProvider.OLLAMA
        and normalized_hostname in _OLLAMA_LOCAL_HOSTS
    ):
        return

    resolve = resolver or socket.getaddrinfo
    port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    try:
        resolved_addresses = resolve(
            normalized_hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        raise AIProviderConfigurationValidationError(
            "base_url host could not be resolved to a public address"
        ) from None

    if not resolved_addresses:
        raise AIProviderConfigurationValidationError(
            "base_url host did not resolve to a public address"
        )

    for result in resolved_addresses:
        try:
            address = result[4][0]
        except (IndexError, KeyError, TypeError):
            raise AIProviderConfigurationValidationError(
                "base_url host resolved to an invalid address"
            ) from None
        if not _is_public_resolved_address(address):
            raise AIProviderConfigurationValidationError(
                "base_url host resolves to a private or otherwise non-public address"
            )


SessionFactory = Callable[[], Session]


class AIProviderConfigurationService:
    """Persist settings while keeping encryption and ownership in one boundary."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        encryption_service: IntegrationService | None = None,
    ) -> None:
        self._session_factory = session_factory or (lambda: Session(engine))
        self._encryption_service = encryption_service or integration_service

    def get_for_user(self, user_id: int) -> AIProviderConfiguration | None:
        with self._session_factory() as session:
            return session.exec(
                select(AIProviderConfiguration).where(
                    AIProviderConfiguration.user_id == user_id
                )
            ).first()

    def upsert(
        self,
        user_id: int,
        *,
        provider: CustomAIProvider | None,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        enabled: bool | None,
        use_for_summary: bool | None,
        use_for_chat: bool | None,
        fields_set: set[str],
    ) -> AIProviderConfiguration:
        with self._session_factory() as session:
            existing = session.exec(
                select(AIProviderConfiguration).where(
                    AIProviderConfiguration.user_id == user_id
                )
            ).first()

            provider_provided = "provider" in fields_set
            model_provided = "model" in fields_set
            base_url_provided = "base_url" in fields_set
            api_key_provided = "api_key" in fields_set

            existing_provider = (
                normalize_provider(existing.provider) if existing is not None else None
            )
            provided_provider = (
                normalize_provider(provider)
                if provider_provided and provider is not None
                else None
            )

            if existing is None and provided_provider is None:
                raise AIProviderConfigurationValidationError(
                    "provider is required when configuring a custom provider"
                )

            selected_provider = provided_provider if provider_provided else existing_provider
            if selected_provider is None:
                raise AIProviderConfigurationValidationError("provider is required")

            provider_changed = existing is not None and selected_provider != existing_provider
            if model_provided or existing is None or provider_changed:
                selected_model = normalize_model(model or DEFAULT_MODELS[selected_provider])
            else:
                selected_model = normalize_model(existing.model)

            if base_url_provided or existing is None or provider_changed:
                selected_base_url = normalize_base_url(base_url) or DEFAULT_BASE_URLS[selected_provider]
            else:
                selected_base_url = normalize_base_url(existing.base_url) or DEFAULT_BASE_URLS[selected_provider]
            selected_base_url = normalize_base_url(
                selected_base_url,
                provider=selected_provider,
            )

            normalized_api_key = normalize_api_key(api_key) if api_key_provided else None
            has_api_key = (
                bool(normalized_api_key)
                if api_key_provided
                else bool(existing is not None and not provider_changed and existing.encrypted_api_key)
            )
            validate_api_key_transport(
                selected_base_url,
                provider=selected_provider,
                has_api_key=has_api_key,
            )
            if api_key_provided:
                encrypted_api_key = self._encrypt_api_key(normalized_api_key)
            elif provider_changed:
                encrypted_api_key = None
            else:
                encrypted_api_key = existing.encrypted_api_key if existing is not None else None

            if selected_provider in {CustomAIProvider.OPENAI, CustomAIProvider.ANTHROPIC} and not encrypted_api_key:
                raise AIProviderConfigurationValidationError(
                    f"api_key is required for provider '{selected_provider.value}'"
                )

            now = datetime.utcnow()
            if existing is None:
                configuration = AIProviderConfiguration(
                    user_id=user_id,
                    provider=selected_provider,
                    model=selected_model,
                    base_url=selected_base_url,
                    encrypted_api_key=encrypted_api_key,
                    enabled=True if enabled is None else enabled,
                    use_for_summary=True if use_for_summary is None else use_for_summary,
                    use_for_chat=True if use_for_chat is None else use_for_chat,
                    created_at=now,
                    updated_at=now,
                )
            else:
                configuration = existing
                configuration.provider = selected_provider
                configuration.model = selected_model
                configuration.base_url = selected_base_url
                configuration.encrypted_api_key = encrypted_api_key
                if enabled is not None:
                    configuration.enabled = enabled
                if use_for_summary is not None:
                    configuration.use_for_summary = use_for_summary
                if use_for_chat is not None:
                    configuration.use_for_chat = use_for_chat
                configuration.updated_at = now

            session.add(configuration)
            session.commit()
            session.refresh(configuration)
            return configuration

    def delete_for_user(self, user_id: int) -> bool:
        with self._session_factory() as session:
            configuration = session.exec(
                select(AIProviderConfiguration).where(
                    AIProviderConfiguration.user_id == user_id
                )
            ).first()
            if configuration is None:
                return False
            session.delete(configuration)
            session.commit()
            return True

    def _encrypt_api_key(self, api_key: str | None) -> str | None:
        if api_key is None:
            return None
        try:
            return self._encryption_service.encrypt_token(api_key)
        except Exception:
            # Never preserve or format the supplied secret while reporting storage failure.
            raise AIProviderSecretStorageError(
                "AI provider secret storage is unavailable; configure TOKEN_ENCRYPTION_KEY."
            ) from None


ai_provider_configuration_service = AIProviderConfigurationService()
