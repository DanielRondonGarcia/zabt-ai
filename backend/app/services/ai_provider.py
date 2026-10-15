# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider-neutral completion clients for summaries and AI Chat."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from langfuse.openai import OpenAI

from app.core.config import settings
from app.models.ai_provider import AIProviderConfiguration, CustomAIProvider
from app.services.ai_provider_configuration import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    ai_provider_configuration_service,
    normalize_api_key,
    normalize_base_url,
    validate_api_key_transport,
    validate_runtime_provider_endpoint,
)
from app.services.integration import integration_service

MAX_OUTPUT_TOKENS = 4096
PROVIDER_TIMEOUT_SECONDS = 120.0
MAX_PROVIDER_RETRIES = 0
CompletionPurpose = Literal["summary", "chat"]
CompletionMessage = Mapping[str, str]
_SAFE_DIAGNOSTIC_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
_SECRET_LIKE_TOKEN = re.compile(
    r"(?:sk|rk|pk|api[_-]?key|token|secret|bearer)[_-]|[A-Za-z0-9_-]{32,}",
    re.IGNORECASE,
)
_SAFE_REQUEST_ID = re.compile(
    r"(?:req|request)[_-][A-Za-z0-9_-]{8,96}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_REQUEST_ID_SECRET_MARKER = re.compile(
    r"(?:sk|rk|pk|api[_-]?key|token|secret|bearer)[_-]", re.IGNORECASE
)


def _bounded_output_tokens(max_tokens: int | None) -> int:
    return min(
        MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens,
        MAX_OUTPUT_TOKENS,
    )


def build_openai_client(
    *,
    base_url: str,
    api_key: str | None,
    client_factory: Any | None = None,
) -> Any:
    """Build an application OpenAI-compatible client with bounded defaults."""
    factory = client_factory if client_factory is not None else OpenAI
    return factory(
        base_url=base_url,
        api_key=api_key,
        timeout=PROVIDER_TIMEOUT_SECONDS,
        max_retries=MAX_PROVIDER_RETRIES,
    )


@dataclass(frozen=True)
class ProviderErrorDiagnostics:
    exception_class: str
    cause_class: str | None = None
    http_status: int | None = None
    provider_error_type: str | None = None
    provider_error_code: str | None = None
    request_id: str | None = None


def _safe_diagnostic_token(value: object) -> str | None:
    if not isinstance(value, str) or not _SAFE_DIAGNOSTIC_TOKEN.fullmatch(value):
        return None
    if _SECRET_LIKE_TOKEN.search(value):
        return None
    return value


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    if not _SAFE_REQUEST_ID.fullmatch(value):
        return None
    if _REQUEST_ID_SECRET_MARKER.search(value):
        return None
    return value


def _safe_attribute(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _provider_error_diagnostics(error: Exception) -> ProviderErrorDiagnostics:
    """Extract bounded metadata only; never inspect exception text or response bodies."""
    exception_class = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", exception_class):
        exception_class = "ProviderError"

    cause = _safe_attribute(error, "__cause__") or _safe_attribute(error, "__context__")
    cause_class = type(cause).__name__ if cause is not None else None
    if cause_class and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", cause_class):
        cause_class = "ProviderError"

    response = _safe_attribute(error, "response")
    status_candidates = (
        _safe_attribute(error, "status_code"),
        _safe_attribute(error, "status"),
        _safe_attribute(response, "status_code") if response is not None else None,
    )
    http_status = next(
        (
            status
            for status in status_candidates
            if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599
        ),
        None,
    )

    headers = _safe_attribute(response, "headers") if response is not None else None
    request_id = _safe_request_id(_safe_attribute(error, "request_id"))
    if request_id is None and isinstance(headers, Mapping):
        for header_name in ("x-request-id", "request-id", "anthropic-request-id"):
            try:
                request_id = _safe_request_id(headers.get(header_name))
            except Exception:
                request_id = None
            if request_id is not None:
                break

    return ProviderErrorDiagnostics(
        exception_class=exception_class,
        cause_class=cause_class,
        http_status=http_status,
        provider_error_type=_safe_diagnostic_token(_safe_attribute(error, "type")),
        provider_error_code=_safe_diagnostic_token(_safe_attribute(error, "code")),
        request_id=request_id,
    )


class AIProviderError(RuntimeError):
    """Credential-free provider failure suitable for application boundaries."""

    def __init__(
        self,
        message: str,
        *,
        configured: bool = False,
        diagnostics: ProviderErrorDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.configured = configured
        self.diagnostics = diagnostics


class CompletionClient(Protocol):
    provider: CustomAIProvider
    model: str

    def complete(
        self,
        messages: Sequence[CompletionMessage],
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str: ...


class OpenAICompatibleCompletionClient:
    """Adapt an OpenAI-compatible SDK client to the shared completion contract."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        provider: CustomAIProvider = CustomAIProvider.OPENAI,
        configured: bool = False,
        endpoint_url: str | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self.provider = provider
        self.configured = configured
        self._endpoint_url = endpoint_url

    def complete(
        self,
        messages: Sequence[CompletionMessage],
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": _bounded_output_tokens(max_tokens),
        }
        if self._endpoint_url:
            try:
                validate_runtime_provider_endpoint(
                    self._endpoint_url,
                    provider=self.provider,
                )
            except ValueError:
                raise AIProviderError(
                    "configured AI provider is unavailable",
                    configured=self.configured,
                ) from None
        try:
            response = self._client.chat.completions.create(**request)
            content = response.choices[0].message.content
        except Exception as exc:
            raise AIProviderError(
                f"{self.provider.value} provider request failed",
                configured=self.configured,
                diagnostics=_provider_error_diagnostics(exc),
            ) from None
        return str(content or "")


class AnthropicCompletionClient:
    """Call the Anthropic Messages API without adding an SDK dependency."""

    provider = CustomAIProvider.ANTHROPIC

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        try:
            validated_base_url = normalize_base_url(
                base_url,
                provider=CustomAIProvider.ANTHROPIC,
            )
        except ValueError:
            raise AIProviderError(
                "configured AI provider is unavailable",
                configured=True,
            ) from None
        if not validated_base_url:
            raise AIProviderError(
                "configured AI provider is unavailable",
                configured=True,
            )
        self._base_url = validated_base_url
        self._api_key = api_key
        self.model = model
        self.configured = True

    def complete(
        self,
        messages: Sequence[CompletionMessage],
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        system_parts: list[str] = []
        mapped_messages: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role", "")
            content = str(message.get("content", ""))
            if role == "system":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                raise AIProviderError(
                    "anthropic provider received an unsupported message role",
                    configured=True,
                )
            if mapped_messages and mapped_messages[-1]["role"] == role:
                mapped_messages[-1]["content"] += f"\n\n{content}"
            else:
                mapped_messages.append({"role": role, "content": content})

        if not mapped_messages or mapped_messages[0]["role"] != "user":
            raise AIProviderError("anthropic provider requires a user message", configured=True)

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": _bounded_output_tokens(max_tokens),
            "messages": mapped_messages,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        try:
            validate_runtime_provider_endpoint(
                self._base_url,
                provider=self.provider,
            )
        except ValueError:
            raise AIProviderError(
                "configured AI provider is unavailable",
                configured=True,
            ) from None

        try:
            endpoint = (
                f"{self._base_url}/messages"
                if self._base_url.casefold().endswith("/v1")
                else f"{self._base_url}/v1/messages"
            )
            with httpx.Client(timeout=PROVIDER_TIMEOUT_SECONDS) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            content_blocks = body.get("content", [])
            text = "".join(
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(
                "anthropic provider request failed",
                configured=True,
                diagnostics=_provider_error_diagnostics(exc),
            ) from None

        if not text:
            raise AIProviderError("anthropic provider returned no text", configured=True)
        return text


@dataclass(frozen=True)
class _RuntimeProviderConfiguration:
    provider: CustomAIProvider
    model: str
    base_url: str
    api_key: str | None


def _fallback_client(
    *,
    purpose: CompletionPurpose,
    client: Any | None,
    model: str | None,
) -> CompletionClient:
    if client is None:
        base_url = (
            settings.AI_CHAT_BASE_URL
            if purpose == "chat"
            else settings.OPENAI_BASE_URL
        )
        api_key = (
            (settings.AI_CHAT_API_KEY or settings.OPENAI_API_KEY)
            if purpose == "chat"
            else settings.OPENAI_API_KEY
        )
        try:
            client = build_openai_client(base_url=base_url, api_key=api_key)
        except Exception:
            raise AIProviderError("application default AI provider is unavailable") from None
    fallback_model = model or (
        settings.AI_CHAT_MODEL if purpose == "chat" else settings.OPENAI_MODEL
    )
    return OpenAICompatibleCompletionClient(
        client,
        model=fallback_model,
        provider=CustomAIProvider.OPENAI,
    )


def _runtime_configuration(
    configuration: AIProviderConfiguration,
) -> _RuntimeProviderConfiguration:
    try:
        provider = CustomAIProvider(configuration.provider)
        base_url = normalize_base_url(
            configuration.base_url or DEFAULT_BASE_URLS[provider],
            provider=provider,
        )
        if not base_url:
            raise ValueError("missing provider base URL")
        api_key = (
            normalize_api_key(
                integration_service.decrypt_token(configuration.encrypted_api_key)
            )
            if configuration.encrypted_api_key
            else None
        )
        if provider in {CustomAIProvider.OPENAI, CustomAIProvider.ANTHROPIC} and not api_key:
            raise AIProviderError("configured AI provider is unavailable", configured=True)
        validate_api_key_transport(
            base_url,
            provider=provider,
            has_api_key=bool((api_key or "").strip()),
        )
        return _RuntimeProviderConfiguration(
            provider=provider,
            model=configuration.model or DEFAULT_MODELS[provider],
            base_url=base_url,
            api_key=api_key,
        )
    except AIProviderError:
        raise
    except Exception:
        raise AIProviderError("configured AI provider is unavailable", configured=True) from None


def _build_custom_client(
    runtime: _RuntimeProviderConfiguration,
    *,
    openai_factory=None,
) -> CompletionClient:
    try:
        base_url = normalize_base_url(runtime.base_url, provider=runtime.provider)
    except ValueError:
        raise AIProviderError(
            "configured AI provider is unavailable",
            configured=True,
        ) from None
    if not base_url:
        raise AIProviderError("configured AI provider is unavailable", configured=True)
    api_key = (runtime.api_key or "").strip() or None
    try:
        validate_api_key_transport(
            base_url,
            provider=runtime.provider,
            has_api_key=bool(api_key),
        )
    except ValueError:
        raise AIProviderError(
            "configured AI provider is unavailable",
            configured=True,
        ) from None

    if runtime.provider == CustomAIProvider.ANTHROPIC:
        if not api_key:
            raise AIProviderError("configured AI provider is unavailable", configured=True)
        return AnthropicCompletionClient(
            base_url=base_url,
            api_key=api_key,
            model=runtime.model,
        )

    if runtime.provider in {CustomAIProvider.OPENAI, CustomAIProvider.OLLAMA}:
        if runtime.provider == CustomAIProvider.OPENAI and not api_key:
            raise AIProviderError("configured AI provider is unavailable", configured=True)
        try:
            factory = openai_factory if openai_factory is not None else OpenAI
            client = factory(
                base_url=base_url,
                # An empty string prevents the SDK from loading OPENAI_API_KEY and
                # emitting a bearer header for keyless local Ollama.
                api_key=api_key or "",
                timeout=PROVIDER_TIMEOUT_SECONDS,
                max_retries=MAX_PROVIDER_RETRIES,
                **(
                    {"_enforce_credentials": False}
                    if runtime.provider == CustomAIProvider.OLLAMA and not api_key
                    else {}
                ),
            )
        except Exception:
            raise AIProviderError("configured AI provider is unavailable", configured=True) from None
        return OpenAICompatibleCompletionClient(
            client,
            model=runtime.model,
            provider=runtime.provider,
            configured=True,
            endpoint_url=base_url,
        )

    raise AIProviderError("configured AI provider is unavailable", configured=True)


def get_completion_client(
    user_id: int | None,
    *,
    purpose: CompletionPurpose,
    fallback_client: Any | None = None,
    fallback_model: str | None = None,
) -> CompletionClient:
    """Resolve one request's provider without ever falling back after selection."""
    if user_id is None:
        return _fallback_client(
            purpose=purpose,
            client=fallback_client,
            model=fallback_model,
        )

    try:
        configuration = ai_provider_configuration_service.get_for_user(user_id)
    except Exception:
        raise AIProviderError("AI provider settings are unavailable", configured=True) from None

    if (
        configuration is None
        or not configuration.enabled
        or not getattr(configuration, f"use_for_{purpose}")
    ):
        return _fallback_client(
            purpose=purpose,
            client=fallback_client,
            model=fallback_model,
        )

    runtime = _runtime_configuration(configuration)
    return _build_custom_client(runtime)
