# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider-neutral completion clients for summaries and AI Chat."""

import logging
import re
import ssl
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
# Summary requests ride the standard Chat Completions SSE streaming transport
# (`complete_summary`) and send no Zabt-imposed output cap; the provider's own
# output ceiling applies. This budget remains the bounded allowance for the
# shared non-streaming `complete` request used by titles, chat, structured
# extraction, and any summary provider without a streaming capability.
SUMMARY_OUTPUT_TOKENS = 16384
MAX_OUTPUT_TOKENS_CEILING = 32768
_TRUNCATION_FINISH_REASONS = frozenset({"length", "max_tokens"})
PROVIDER_TIMEOUT_SECONDS = 120.0
# Summary-only per-operation read allowance. Reasoning models can idle between
# streamed text deltas, so summary stream reads may wait up to one hour while
# connect keeps the bounded provider default. A scalar timeout is not a total
# wall-clock deadline for the whole summary.
SUMMARY_PROVIDER_TIMEOUT_SECONDS = 3600.0
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
# Client-generated LiteLLM trace IDs are opaque lowercase 32-character hex
# tokens (uuid4().hex). Anything else is never sent as trace metadata.
_SAFE_CORRELATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_REQUEST_ID_SECRET_MARKER = re.compile(
    r"(?:sk|rk|pk|api[_-]?key|token|secret|bearer)[_-]", re.IGNORECASE
)
_TIMEOUT_EXCEPTION_CLASSES = frozenset(
    {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "Timeout",
        "TimeoutError",
        "TimeoutException",
        "WriteTimeout",
    }
)
_CONNECTION_EXCEPTION_CLASSES = frozenset(
    {
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "NameResolutionError",
        "NetworkError",
        "ProxyError",
        "RemoteProtocolError",
    }
)
_QUOTA_ERROR_CODES = frozenset(
    {
        "billing_hard_limit_reached",
        "billing_limit_exceeded",
        "billing_limit_reached",
        "insufficient_quota",
    }
)
_SAFE_INTERNAL_ERROR_MESSAGES = {
    "configured AI provider is unavailable": (
        "The configured AI provider is unavailable. Check its settings and credentials "
        "before retrying."
    ),
    "AI provider settings are unavailable": (
        "AI provider settings are unavailable. Check provider configuration and logs "
        "before retrying."
    ),
    "application default AI provider is unavailable": (
        "The application default AI provider is unavailable. Check application AI "
        "configuration and logs before retrying."
    ),
    "provider output was truncated before any content": (
        "The AI provider ran out of output tokens before producing any text. "
        "Retry, or reduce the meeting evidence sent to the model."
    ),
    "summary stream ended before the provider finished": (
        "The AI provider connection ended before the summary finished. No summary "
        "was saved; retry the meeting summary."
    ),
    "summary output reached the provider token limit": (
        "The AI provider stopped the summary at its output token limit before the "
        "notes were complete. Retry the meeting summary."
    ),
    "summary generation was refused by the provider": (
        "The AI provider refused to generate the summary content. Check provider "
        "content policies before retrying."
    ),
    "summary stream ended without completed content": (
        "The AI provider ended the summary without completed content. Check provider "
        "logs before retrying."
    ),
}


def _bounded_output_tokens(
    max_tokens: int | None, *, ceiling: int = MAX_OUTPUT_TOKENS
) -> int:
    return min(MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens, ceiling)


def _completion_content_and_finish(response: Any) -> tuple[str, str | None]:
    choice = response.choices[0]
    content = getattr(getattr(choice, "message", None), "content", None)
    finish_reason = getattr(choice, "finish_reason", None)
    return str(content or ""), finish_reason if isinstance(finish_reason, str) else None


def _stream_summary_chunk(chunk: Any) -> tuple[str, str | None, bool]:
    """Consume one streamed chunk's text delta, terminal reason, and refusal.

    Hidden reasoning deltas (``content`` is not a string) are ignored; trailing
    usage chunks with empty ``choices`` contribute nothing.
    """
    content = ""
    finish_reason: str | None = None
    refused = False
    for choice in getattr(chunk, "choices", None) or ():
        if getattr(choice, "index", 0) not in (None, 0):
            continue
        delta = getattr(choice, "delta", None)
        piece = getattr(delta, "content", None)
        if isinstance(piece, str):
            content += piece
        if getattr(delta, "refusal", None):
            refused = True
        reason = getattr(choice, "finish_reason", None)
        if isinstance(reason, str):
            finish_reason = reason
    return content, finish_reason, refused


def _was_truncated(finish_reason: str | None) -> bool:
    return finish_reason is not None and finish_reason in _TRUNCATION_FINISH_REASONS

def build_openai_client(
    *,
    base_url: str,
    api_key: str | None,
    client_factory: Any | None = None,
) -> Any:
    """Build an application OpenAI-compatible client with bounded defaults."""
    factory = client_factory if client_factory is not None else OpenAI
    client_kwargs: dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "timeout": PROVIDER_TIMEOUT_SECONDS,
        "max_retries": MAX_PROVIDER_RETRIES,
    }
    ca_bundle = str(settings.ACTSIS_CA_BUNDLE or "").strip()
    if ca_bundle:
        ssl_context = ssl.create_default_context()
        try:
            ssl_context.load_verify_locations(cafile=ca_bundle)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Could not load ACTSIS_CA_BUNDLE; keeping default TLS certificate "
                "verification enabled (%s)",
                type(exc).__name__,
            )
        else:
            client_kwargs["http_client"] = httpx.Client(
                verify=ssl_context,
                timeout=PROVIDER_TIMEOUT_SECONDS,
            )
    return factory(**client_kwargs)


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

    def __str__(self) -> str:
        """Return a user-safe explanation from diagnostics or fixed internal messages."""
        if self.diagnostics is None:
            message = self.args[0] if self.args and type(self.args[0]) is str else None
            if message is not None:
                safe_message = _SAFE_INTERNAL_ERROR_MESSAGES.get(message)
                if safe_message is not None:
                    return safe_message
        return _public_provider_error_message(self.diagnostics)


def _public_provider_error_message(
    diagnostics: ProviderErrorDiagnostics | None,
) -> str:
    if diagnostics is None:
        return (
            "The AI provider request failed. Check provider configuration and logs "
            "before retrying."
        )

    status = diagnostics.http_status
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 100 <= status <= 599
    ):
        status = None
    if status == 429:
        reported_codes = {
            value.casefold()
            for value in (diagnostics.provider_error_type, diagnostics.provider_error_code)
            if isinstance(value, str)
        }
        if reported_codes & _QUOTA_ERROR_CODES:
            return (
                "The AI provider reports exhausted quota or billing limits. Check "
                "quota and billing before retrying."
            )
        return (
            "The AI provider is rate-limiting requests (HTTP 429). Wait for the rate "
            "limit to reset or check provider rate limits before retrying."
        )
    if status == 401:
        return (
            "AI provider authentication failed (HTTP 401). Check the configured "
            "credentials before retrying."
        )
    if status == 403:
        return (
            "The AI provider denied access (HTTP 403). Check account permissions "
            "and model access before retrying."
        )
    if status == 408:
        return (
            "The AI provider request timed out (HTTP 408). Check provider availability "
            "and timeout settings before retrying."
        )
    if status is not None and 500 <= status <= 599:
        return (
            f"The AI provider returned a server error (HTTP {status}). Check provider "
            "status and logs before retrying."
        )
    if status is not None and 400 <= status <= 499:
        return (
            f"The AI provider rejected the request with HTTP {status}. Check provider "
            "configuration and request requirements before retrying."
        )
    if status is not None:
        return (
            f"The AI provider returned HTTP status {status}. Check the endpoint "
            "configuration and provider logs before retrying."
        )

    exception_classes = {
        value
        for value in (diagnostics.exception_class, diagnostics.cause_class)
        if isinstance(value, str)
    }
    if exception_classes & _TIMEOUT_EXCEPTION_CLASSES:
        return (
            "The AI provider request timed out. Check provider availability and timeout "
            "settings before retrying."
        )
    if exception_classes & _CONNECTION_EXCEPTION_CLASSES:
        return (
            "The AI provider could not be reached. No HTTP response was received, so "
            "quota status cannot be determined from this error. Check connectivity "
            "and the provider endpoint before retrying."
        )
    return (
        "The AI provider error could not be classified. Check provider logs before "
        "repeated retries."
    )


class CompletionClient(Protocol):
    provider: CustomAIProvider
    model: str

    def complete(
        self,
        messages: Sequence[CompletionMessage],
        *,
        temperature: float,
        max_tokens: int | None = None,
        correlation_id: str | None = None,
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
        correlation_id: str | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            # Callers that ask for more than the default cap (summaries) get the
            # higher ceiling; everyone else keeps the bounded default.
            "max_tokens": _bounded_output_tokens(
                max_tokens, ceiling=MAX_OUTPUT_TOKENS_CEILING
            ),
        }
        if correlation_id is not None and _SAFE_CORRELATION_ID.fullmatch(correlation_id):
            # LiteLLM stores `x-litellm-trace-id` in its request metadata, so an
            # operator can match this exact call in the gateway logs. The ID is
            # opaque and client-generated; request content never travels with it.
            request["extra_headers"] = {"x-litellm-trace-id": correlation_id}
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

        content, finish_reason = self._send(request)
        if _was_truncated(finish_reason) and not content.strip():
            # Reasoning-capable models can spend the whole budget on hidden
            # reasoning and return no text. Retry once with a larger budget.
            retry_budget = min(
                request["max_tokens"] * 2, MAX_OUTPUT_TOKENS_CEILING
            )
            if retry_budget > request["max_tokens"]:
                request = {**request, "max_tokens": retry_budget}
                content, finish_reason = self._send(request)
        if _was_truncated(finish_reason) and not content.strip():
            raise AIProviderError("provider output was truncated before any content")
        return content

    def _send(self, request: dict[str, Any]) -> tuple[str, str | None]:
        try:
            response = self._client.chat.completions.create(**request)
            return _completion_content_and_finish(response)
        except Exception as exc:
            raise AIProviderError(
                f"{self.provider.value} provider request failed",
                configured=self.configured,
                diagnostics=_provider_error_diagnostics(exc),
            ) from None

    def complete_summary(
        self,
        messages: Sequence[CompletionMessage],
        *,
        temperature: float,
        correlation_id: str | None = None,
    ) -> str:
        """Summarize with the standard Chat Completions streaming transport.

        Summary-only behavior: exactly one HTTP request per call (no truncation
        retry, no provider fallback), ``stream=True`` with no Zabt-imposed
        ``max_tokens``/``max_completion_tokens`` cap so the provider ceiling
        applies, and a summary-only one-hour per-read allowance while connect
        keeps the bounded default. The stream is consumed as text deltas only;
        a refusal delta, a ``length``/``max_tokens``/``content_filter`` terminal
        reason, or an interrupted stream with no terminal reason fails with a
        safe ``AIProviderError``. Blank content with a terminal ``stop`` is
        returned as an empty string; the summary dispatch layer turns it into
        the safe empty-summary failure so no truncated or missing result can be
        saved. The stream is closed on success and on failure. Non-summary
        callers and callers without a streaming capability keep the shared
        contract in ``complete``.
        """
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

        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "stream": True,
            "timeout": httpx.Timeout(
                SUMMARY_PROVIDER_TIMEOUT_SECONDS,
                connect=PROVIDER_TIMEOUT_SECONDS,
            ),
        }
        if correlation_id is not None and _SAFE_CORRELATION_ID.fullmatch(correlation_id):
            request["extra_headers"] = {"x-litellm-trace-id": correlation_id}

        try:
            stream = self._client.chat.completions.create(**request)
        except Exception as exc:
            raise AIProviderError(
                f"{self.provider.value} summary provider request failed",
                configured=self.configured,
                diagnostics=_provider_error_diagnostics(exc),
            ) from None

        content_parts: list[str] = []
        finish_reason: str | None = None
        refused = False
        try:
            for chunk in stream:
                chunk_content, chunk_finish, chunk_refused = _stream_summary_chunk(chunk)
                if chunk_content:
                    content_parts.append(chunk_content)
                if chunk_finish is not None:
                    finish_reason = chunk_finish
                refused = refused or chunk_refused
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(
                f"{self.provider.value} summary provider request failed",
                configured=self.configured,
                diagnostics=_provider_error_diagnostics(exc),
            ) from None
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        if finish_reason is None:
            raise AIProviderError(
                "summary stream ended before the provider finished",
                configured=self.configured,
            )
        if refused:
            raise AIProviderError(
                "summary generation was refused by the provider",
                configured=self.configured,
            )
        if finish_reason in _TRUNCATION_FINISH_REASONS:
            raise AIProviderError(
                "summary output reached the provider token limit",
                configured=self.configured,
            )
        if finish_reason == "content_filter":
            raise AIProviderError(
                "summary generation was refused by the provider",
                configured=self.configured,
            )
        if finish_reason != "stop":
            raise AIProviderError(
                "summary stream ended without completed content",
                configured=self.configured,
            )
        # Blank content stays a caller-level failure (`empty summary response`).
        return "".join(content_parts)


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
        correlation_id: str | None = None,
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
