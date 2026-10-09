# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused provider-selection and secret-boundary tests without live providers."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest
from fastapi import Response
from pydantic import ValidationError

from app.api.v1.endpoints import users
from app.models.ai_provider import AIProviderConfiguration, CustomAIProvider
from app.services import ai_provider
from app.services.ai_chat import AIChatService
from app.services.ai_provider_configuration import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    AIProviderConfigurationService,
    AIProviderSecretStorageError,
    AIProviderConfigurationValidationError,
    normalize_base_url,
    validate_runtime_provider_endpoint,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Session:
    def __init__(self, store: "_Store") -> None:
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def exec(self, statement):
        requested_user_id = statement.whereclause.right.value
        value = next(
            (
                configuration
                for configuration in self.store.configurations
                if configuration.user_id == requested_user_id
            ),
            None,
        )
        return _Result(value)

    def add(self, configuration):
        if configuration not in self.store.configurations:
            self.store.configurations.append(configuration)

    def commit(self):
        return None

    def refresh(self, configuration):
        return None

    def delete(self, configuration):
        self.store.configurations.remove(configuration)


class _Store:
    def __init__(self) -> None:
        self.configurations: list[AIProviderConfiguration] = []

    def session(self):
        return _Session(self)


class _Encryption:
    def encrypt_token(self, value: str) -> str:
        return f"encrypted:{value}"

    def decrypt_token(self, value: str) -> str:
        return value.removeprefix("encrypted:")


def _service(store: _Store | None = None) -> AIProviderConfigurationService:
    store = store or _Store()
    return AIProviderConfigurationService(
        session_factory=store.session,
        encryption_service=_Encryption(),
    )


def test_secret_is_encrypted_and_safe_read_response_has_no_key() -> None:
    store = _Store()
    service = _service(store)
    configuration = service.upsert(
        7,
        provider=CustomAIProvider.OPENAI,
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        api_key="sk-user-secret",
        enabled=True,
        use_for_summary=True,
        use_for_chat=True,
        fields_set={
            "provider",
            "model",
            "base_url",
            "api_key",
            "enabled",
            "use_for_summary",
            "use_for_chat",
        },
    )

    assert configuration.encrypted_api_key == "encrypted:sk-user-secret"
    safe = users._ai_provider_read(configuration).model_dump()
    assert safe["api_key_configured"] is True
    assert "api_key" not in safe
    assert "sk-user-secret" not in repr(safe)


def test_secret_save_fails_without_encryption_and_does_not_echo_the_key() -> None:
    class BrokenEncryption:
        def encrypt_token(self, value: str) -> str:
            raise ValueError(f"encryption failed for {value}")

    service = AIProviderConfigurationService(
        session_factory=_Store().session,
        encryption_service=BrokenEncryption(),
    )
    with pytest.raises(AIProviderSecretStorageError) as exc_info:
        service.upsert(
            7,
            provider=CustomAIProvider.OPENAI,
            model="gpt-test",
            base_url=None,
            api_key="secret-that-must-not-echo",
            enabled=True,
            use_for_summary=True,
            use_for_chat=True,
            fields_set={"provider", "api_key"},
        )
    assert "secret-that-must-not-echo" not in str(exc_info.value)


def test_configuration_reads_are_scoped_to_the_requested_owner() -> None:
    store = _Store()
    service = _service(store)
    store.configurations.append(
        AIProviderConfiguration(
            user_id=1,
            provider=CustomAIProvider.OLLAMA,
            model="llama3.2:3b",
            base_url="http://localhost:11434/v1",
        )
    )

    assert service.get_for_user(1) is not None
    assert service.get_for_user(2) is None


def test_current_user_api_passes_only_the_authenticated_owner_id(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[int] = []
    monkeypatch.setattr(
        users.ai_provider_configuration_service,
        "get_for_user",
        lambda user_id: requested.append(user_id) or None,
    )

    response = Response()
    result = users.get_ai_provider_configuration(response, SimpleNamespace(id=23))

    assert requested == [23]
    assert result.provider is None
    assert result.api_key_configured is False
    assert response.headers["cache-control"] == "no-store"


def test_provider_validation_rejects_unsafe_urls_and_masks_secret_errors() -> None:
    for value in (
        "ftp://example.com",
        "https://user:password@example.com/v1",
        "https://example.com/v1?token=secret",
    ):
        with pytest.raises(ValueError):
            normalize_base_url(value)

    secret = "secret-key-should-not-appear"
    with pytest.raises(ValidationError) as exc_info:
        users.AIProviderConfigurationPatch(api_key=secret * 200)
    assert secret not in str(exc_info.value)


def test_provider_url_policy_allows_documented_ollama_local_hosts() -> None:
    assert normalize_base_url(
        "http://localhost:11434/v1",
        provider=CustomAIProvider.OLLAMA,
    ) == "http://localhost:11434/v1"
    assert normalize_base_url(
        "http://host.docker.internal:11434/v1",
        provider=CustomAIProvider.OLLAMA,
    ) == "http://host.docker.internal:11434/v1"

    for value in (
        "http://10.0.0.5:11434/v1",
        "http://169.254.169.254:11434/v1",
        "http://127.0.0.2:11434/v1",
        "http://[::2]:11434/v1",
    ):
        with pytest.raises(AIProviderConfigurationValidationError):
            normalize_base_url(value, provider=CustomAIProvider.OLLAMA)


def test_ollama_defaults_target_the_official_cloud_endpoint() -> None:
    assert DEFAULT_MODELS[CustomAIProvider.OLLAMA] == "gemma4:31b"
    assert DEFAULT_BASE_URLS[CustomAIProvider.OLLAMA] == "https://ollama.com/v1"
    assert normalize_base_url(
        DEFAULT_BASE_URLS[CustomAIProvider.OLLAMA],
        provider=CustomAIProvider.OLLAMA,
    ) == "https://ollama.com/v1"


def test_ollama_allows_public_https_endpoints_but_requires_runtime_validation() -> None:
    assert normalize_base_url(
        "https://api.example.com/v1",
        provider=CustomAIProvider.OLLAMA,
    ) == "https://api.example.com/v1"

    for value in (
        "http://api.example.com/v1",
        "https://10.0.0.5/v1",
        "https://169.254.169.254/v1",
        "https://metadata.google.internal/v1",
    ):
        with pytest.raises(AIProviderConfigurationValidationError):
            normalize_base_url(value, provider=CustomAIProvider.OLLAMA)


def test_hosted_provider_urls_require_https_and_public_hosts() -> None:
    for provider in (CustomAIProvider.OPENAI, CustomAIProvider.ANTHROPIC):
        with pytest.raises(AIProviderConfigurationValidationError):
            normalize_base_url("http://api.example.com/v1", provider=provider)

    for value in (
        "https://10.0.0.5/v1",
        "https://169.254.169.254/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://localhost/v1",
    ):
        with pytest.raises(AIProviderConfigurationValidationError):
            normalize_base_url(value, provider=CustomAIProvider.OPENAI)


def test_provider_persistence_rejects_http_before_storing_credentials() -> None:
    store = _Store()
    with pytest.raises(AIProviderConfigurationValidationError):
        _service(store).upsert(
            7,
            provider=CustomAIProvider.OPENAI,
            model="gpt-test",
            base_url="http://api.example.com/v1",
            api_key="sk-user-secret",
            enabled=True,
            use_for_summary=True,
            use_for_chat=True,
            fields_set={"provider", "base_url", "api_key"},
        )

    assert store.configurations == []


def test_ollama_http_rejects_a_key_before_storing_credentials() -> None:
    store = _Store()
    with pytest.raises(
        AIProviderConfigurationValidationError,
        match="Ollama API keys require an HTTPS base URL",
    ):
        _service(store).upsert(
            7,
            provider=CustomAIProvider.OLLAMA,
            model="qwen-local",
            base_url="http://localhost:11434/v1",
            api_key="ollama-user-secret",
            enabled=True,
            use_for_summary=True,
            use_for_chat=True,
            fields_set={"provider", "base_url", "api_key"},
        )

    assert store.configurations == []


def test_openai_and_anthropic_require_a_secret() -> None:
    with pytest.raises(AIProviderConfigurationValidationError, match="api_key is required"):
        _service().upsert(
            1,
            provider=CustomAIProvider.OPENAI,
            model="gpt-test",
            base_url=None,
            api_key=None,
            enabled=True,
            use_for_summary=True,
            use_for_chat=True,
            fields_set={"provider"},
        )


def test_switching_provider_resets_defaults_and_clears_the_old_secret() -> None:
    store = _Store()
    service = _service(store)
    service.upsert(
        7,
        provider=CustomAIProvider.OPENAI,
        model="gpt-user",
        base_url="https://api.openai.com/v1",
        api_key="sk-user-secret",
        enabled=True,
        use_for_summary=True,
        use_for_chat=True,
        fields_set={"provider", "model", "base_url", "api_key"},
    )

    switched = service.upsert(
        7,
        provider=CustomAIProvider.OLLAMA,
        model=None,
        base_url=None,
        api_key="ollama-cloud-secret",
        enabled=None,
        use_for_summary=None,
        use_for_chat=None,
        fields_set={"provider", "api_key"},
    )

    assert switched.provider == CustomAIProvider.OLLAMA
    assert switched.model == "gemma4:31b"
    assert switched.base_url == "https://ollama.com/v1"
    assert switched.encrypted_api_key == "encrypted:ollama-cloud-secret"


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
        )


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_disabled_or_unselected_provider_uses_the_application_default() -> None:
    fallback = _FakeOpenAIClient()
    original = ai_provider.ai_provider_configuration_service.get_for_user
    try:
        ai_provider.ai_provider_configuration_service.get_for_user = lambda user_id: None
        resolved = ai_provider.get_completion_client(
            10,
            purpose="chat",
            fallback_client=fallback,
            fallback_model="application-model",
        )
        assert resolved.provider == CustomAIProvider.OPENAI
        assert resolved.complete([{"role": "user", "content": "hello"}], temperature=0.2) == "answer"
        assert fallback.chat.completions.calls[0]["model"] == "application-model"
    finally:
        ai_provider.ai_provider_configuration_service.get_for_user = original


def test_ollama_selection_uses_the_user_model_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = AIProviderConfiguration(
        user_id=10,
        provider=CustomAIProvider.OLLAMA,
        model="qwen-local",
        base_url="http://host.docker.internal:11434/v1",
        encrypted_api_key=None,
        enabled=True,
        use_for_summary=True,
        use_for_chat=True,
    )
    constructed: list[dict] = []
    selected_user_ids: list[int] = []

    class FakeFactory:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(
        ai_provider.ai_provider_configuration_service,
        "get_for_user",
        lambda user_id: selected_user_ids.append(user_id) or configuration,
    )
    monkeypatch.setattr(ai_provider, "OpenAI", FakeFactory)

    resolved = ai_provider.get_completion_client(
        10,
        purpose="summary",
        fallback_client=_FakeOpenAIClient(),
        fallback_model="application-model",
    )

    assert resolved.provider == CustomAIProvider.OLLAMA
    assert resolved.model == "qwen-local"
    assert constructed == [{
        "base_url": "http://host.docker.internal:11434/v1",
        "api_key": "",
        "timeout": ai_provider.PROVIDER_TIMEOUT_SECONDS,
        "max_retries": ai_provider.MAX_PROVIDER_RETRIES,
        "_enforce_credentials": False,
    }]
    assert selected_user_ids == [10]


def test_keyless_local_ollama_client_has_no_authorization_header() -> None:
    runtime = ai_provider._RuntimeProviderConfiguration(
        provider=CustomAIProvider.OLLAMA,
        model="qwen-local",
        base_url="http://localhost:11434/v1",
        api_key=None,
    )

    resolved = ai_provider._build_custom_client(
        runtime,
        openai_factory=ai_provider.OpenAI,
    )

    assert resolved._client.auth_headers == {}


def test_openai_compatible_completion_sends_bounded_default_output() -> None:
    fake_client = _FakeOpenAIClient()
    client = ai_provider.OpenAICompatibleCompletionClient(
        fake_client,
        model="local-model",
        provider=CustomAIProvider.OLLAMA,
        configured=True,
    )

    assert client.complete(
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
    ) == "answer"
    assert fake_client.chat.completions.calls[0]["max_tokens"] == ai_provider.MAX_OUTPUT_TOKENS


def test_custom_openai_http_url_is_rejected_before_key_transmission() -> None:
    runtime = ai_provider._RuntimeProviderConfiguration(
        provider=CustomAIProvider.OPENAI,
        model="gpt-test",
        base_url="http://api.example.com/v1",
        api_key="sk-user-secret",
    )
    constructed: list[dict] = []

    class FakeFactory:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with pytest.raises(ai_provider.AIProviderError, match="configured AI provider"):
        ai_provider._build_custom_client(runtime, openai_factory=FakeFactory)

    assert constructed == []


def test_ollama_http_key_is_rejected_before_factory_construction() -> None:
    runtime = ai_provider._RuntimeProviderConfiguration(
        provider=CustomAIProvider.OLLAMA,
        model="qwen-local",
        base_url="http://localhost:11434/v1",
        api_key="ollama-user-secret",
    )
    constructed: list[dict] = []

    class FakeFactory:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with pytest.raises(ai_provider.AIProviderError, match="configured AI provider"):
        ai_provider._build_custom_client(runtime, openai_factory=FakeFactory)

    assert constructed == []


def test_runtime_dns_resolution_rejects_a_private_dns_style_host() -> None:
    url = "https://127.0.0.1.nip.io/v1"

    assert normalize_base_url(url, provider=CustomAIProvider.OPENAI) == url

    def private_resolver(hostname: str, port: int, *, type: int):
        assert hostname == "127.0.0.1.nip.io"
        assert port == 443
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    with pytest.raises(
        AIProviderConfigurationValidationError,
        match="private or otherwise non-public",
    ):
        validate_runtime_provider_endpoint(
            url,
            provider=CustomAIProvider.OPENAI,
            resolver=private_resolver,
        )


def test_openai_custom_request_checks_runtime_endpoint_before_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeOpenAIClient()

    def reject_endpoint(*args, **kwargs):
        raise AIProviderConfigurationValidationError("endpoint blocked")

    monkeypatch.setattr(ai_provider, "validate_runtime_provider_endpoint", reject_endpoint)
    client = ai_provider.OpenAICompatibleCompletionClient(
        fake_client,
        model="gpt-test",
        provider=CustomAIProvider.OPENAI,
        configured=True,
        endpoint_url="https://127.0.0.1.nip.io/v1",
    )

    with pytest.raises(ai_provider.AIProviderError, match="configured AI provider"):
        client.complete([{"role": "user", "content": "hello"}], temperature=0.2)

    assert fake_client.chat.completions.calls == []


def test_configured_provider_failure_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = AIProviderConfiguration(
        user_id=10,
        provider=CustomAIProvider.OPENAI,
        model="gpt-user",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="ciphertext",
        enabled=True,
        use_for_summary=True,
        use_for_chat=True,
    )
    fallback = _FakeOpenAIClient()
    monkeypatch.setattr(
        ai_provider.ai_provider_configuration_service,
        "get_for_user",
        lambda user_id: configuration,
    )
    monkeypatch.setattr(
        ai_provider.integration_service,
        "decrypt_token",
        lambda value: (_ for _ in ()).throw(ValueError("decrypt failed")),
    )

    with pytest.raises(ai_provider.AIProviderError, match="configured AI provider"):
        ai_provider.get_completion_client(
            10,
            purpose="chat",
            fallback_client=fallback,
            fallback_model="application-model",
        )
    assert fallback.chat.completions.calls == []


class _FakeAnthropicResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"content": [{"type": "text", "text": "Claude answer"}]}


class _FakeHTTPClient:
    def __init__(self, requests: list[dict]) -> None:
        self.requests = requests

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, headers, json):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return _FakeAnthropicResponse()


def test_anthropic_mapping_uses_messages_headers_and_bounded_output(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []
    monkeypatch.setattr(ai_provider.httpx, "Client", lambda timeout: _FakeHTTPClient(requests))
    monkeypatch.setattr(ai_provider, "validate_runtime_provider_endpoint", lambda *args, **kwargs: None)
    client = ai_provider.AnthropicCompletionClient(
        base_url="https://api.anthropic.com",
        api_key="anthropic-secret",
        model="claude-test",
    )

    result = client.complete(
        [
            {"role": "system", "content": "System rules"},
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Follow-up"},
        ],
        temperature=0.2,
        max_tokens=99999,
    )

    request = requests[0]
    assert result == "Claude answer"
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "anthropic-secret"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert request["json"]["system"] == "System rules"
    assert [message["role"] for message in request["json"]["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert request["json"]["max_tokens"] == ai_provider.MAX_OUTPUT_TOKENS


def test_chat_resolution_receives_the_current_user_id() -> None:
    resolved_calls: list[dict] = []

    class Completion:
        def complete(self, messages, *, temperature, max_tokens=None):
            return "selected answer"

    def resolver(user_id, **kwargs):
        resolved_calls.append({"user_id": user_id, **kwargs})
        return Completion()

    service = AIChatService(
        retrieval_service=SimpleNamespace(search=lambda **kwargs: [{"meeting_id": 1}]),
        client=None,
        provider_resolver=resolver,
        group_service=SimpleNamespace(
            get_accessible=lambda group_id, user_id: SimpleNamespace(description=None)
        ),
    )

    answer = service._complete(8, 42, "Question", [{"meeting_id": 1}], [])

    assert answer == "selected answer"
    assert resolved_calls[0]["user_id"] == 42
    assert resolved_calls[0]["purpose"] == "chat"
