# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Summary flows through the public ai_agent service and provider routing.

Integration-level tests: the real OpenAI SDK over a local httpx.MockTransport
only where a full wire request is needed (sibling timeout scoping); otherwise
capturing fake SDK clients and fake CompletionProviders. Public interfaces
only: `summarize_transcript`, `infer_title`, `get_completion_client`. The
summary streaming primitive itself is tested in test_summary_stream_transport.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from app.models.ai_provider import CustomAIProvider
from app.services import ai_agent, ai_provider
from app.services.ai_agent import summarize_transcript
from app.services.ai_provider import AIProviderError, OpenAICompatibleCompletionClient

_SUMMARY_MODEL = "oc/glm-5.3-flash"


def _summarize(user_prompt: str, client) -> str:
    """Run the public transcript-only summary path (temperature 0.3)."""
    return summarize_transcript(user_prompt, completion_client=client)


def _completion_json(content: str) -> str:
    return json.dumps(
        {
            "id": "chatcmpl-wire",
            "object": "chat.completion",
            "model": _SUMMARY_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }
    )


def _wire_client(bodies: list):
    """Official OpenAI SDK client on a local mock transport."""
    captured: list[httpx.Request] = []
    queue = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = queue.pop(0)
        if isinstance(body, BaseException):
            raise body
        request_body = json.loads(request.content)
        if request_body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body.encode(),
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body.encode(),
        )

    sdk = OpenAI(
        base_url="https://ai.example.test/v1",
        api_key="sk-summary-wire-test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAICompatibleCompletionClient(sdk, model=_SUMMARY_MODEL), captured


def _ns_chunk(content: str | None, finish_reason: str | None = None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class _FakeStream:
    def __init__(self, events: list):
        self._events = list(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._events:
            raise StopIteration
        event = self._events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self):
        self.closed = True


class _FakeStreamingCompletions:
    """Captures summary create kwargs and answers title calls without a stream."""

    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **request):
        self.calls.append(dict(request))
        if request.get("stream"):
            return _FakeStream([_ns_chunk("Streamed notes"), _ns_chunk(None, "stop")])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Focused Title"),
                    finish_reason="stop",
                )
            ]
        )


def _fake_stream_client(stream_factory):
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **request: stream_factory(request))
        )
    )
    return OpenAICompatibleCompletionClient(sdk, model=_SUMMARY_MODEL)


# ── Dispatch: the default ai_agent summary path uses the new transport ───────


def test_default_summary_path_uses_the_streaming_transport(monkeypatch):
    completions = _FakeStreamingCompletions()
    monkeypatch.setattr(
        ai_agent,
        "_client",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = summarize_transcript("TRANSCRIPT_SECRET", completion_client=None)

    assert result == "Streamed notes"
    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["stream"] is True
    assert "max_tokens" not in request


def test_blank_stream_fails_closed_at_the_service_level(caplog):
    """Blank streamed content becomes the safe empty-summary failure, which the
    primitive itself cannot enforce (it returns the accumulated text)."""
    blank_stream = _FakeStream([_ns_chunk(""), _ns_chunk(None, "stop")])
    completions: list[dict] = []

    def create_stream(request):
        completions.append(dict(request))
        return blank_stream

    client = _fake_stream_client(create_stream)

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        with pytest.raises(AIProviderError):
            _summarize("PROMPT_SECRET", client)

    assert len(completions) == 1
    assert "returned empty content" in caplog.text
    assert "PROMPT_SECRET" not in caplog.text


# ── Routing: configured-user OpenAI summary vs sibling title stage ───────────


def test_configured_user_openai_summary_streams_and_title_stays_bounded(monkeypatch):
    configuration = SimpleNamespace(
        provider=CustomAIProvider.OPENAI,
        model="gpt-user",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="ciphertext",
        enabled=True,
        use_for_summary=True,
        use_for_chat=True,
    )
    monkeypatch.setattr(
        ai_provider.ai_provider_configuration_service,
        "get_for_user",
        lambda user_id: configuration,
    )
    monkeypatch.setattr(
        ai_provider.integration_service,
        "decrypt_token",
        lambda value: "sk-user-secret",
    )
    validation_calls: list[tuple] = []

    def record_validation(url, *, provider=None, resolver=None):
        validation_calls.append((url, provider))

    monkeypatch.setattr(ai_provider, "validate_runtime_provider_endpoint", record_validation)

    factories: list[dict] = []
    instances: list[RecordingOpenAI] = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            factories.append(kwargs)
            self.completions = _FakeStreamingCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            instances.append(self)

    monkeypatch.setattr(ai_provider, "OpenAI", RecordingOpenAI)

    resolved = ai_provider.get_completion_client(42, purpose="summary")

    assert isinstance(resolved, OpenAICompatibleCompletionClient)

    # The summary capability streams with no token cap and one-hour reads.
    summary_text = resolved.complete_summary(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "PROMPT_SECRET"}],
        temperature=0.3,
    )

    # The sibling title stage on the SAME resolved client keeps the bounded
    # non-streaming 50-token request, never the summary transport.
    title = ai_agent.infer_title("SUMMARY_TEXT_SECRET", completion_client=resolved)

    assert title == "Focused Title"

    # Endpoint and SSRF guard ran before every request on this route.
    expected_validation = ("https://api.openai.com/v1", CustomAIProvider.OPENAI)
    assert validation_calls == [expected_validation, expected_validation]
    assert factories[0]["base_url"] == "https://api.openai.com/v1"
    assert factories[0]["api_key"] == "sk-user-secret"
    assert factories[0]["max_retries"] == ai_provider.MAX_PROVIDER_RETRIES

    summary_request = instances[0].completions.calls[0]
    assert summary_text == "Streamed notes"
    assert summary_request["stream"] is True
    assert "max_tokens" not in summary_request
    title_request = instances[0].completions.calls[-1]
    assert title_request["max_tokens"] == 50
    assert "stream" not in title_request
    assert title_request["temperature"] == 0.1


def test_summary_reads_allowance_does_not_leak_into_sibling_title_requests():
    """A real SDK wire request proves the one-hour read allowance stays
    summary-only: the sibling title request keeps the bounded SDK default."""
    client, captured = _wire_client([_completion_json("Focused title")])

    title = ai_agent.infer_title("SUMMARY_SOURCE_SECRET", completion_client=client)

    assert title == "Focused title"
    request = captured[0]
    parsed = json.loads(request.content)
    assert parsed["max_tokens"] == 50
    assert "stream" not in parsed
    timeout = request.extensions["timeout"]
    assert timeout["read"] != 3600.0
    assert timeout["connect"] != 3600.0


# ── Non-streaming CompletionClient compatibility ────────────────────────────


def test_completion_clients_without_summary_streaming_keep_the_shared_contract():
    calls: list[dict] = []

    class FakeAlternateProvider:
        provider = "custom"
        model = "custom-model"

        def complete(
            self,
            messages,
            *,
            temperature,
            max_tokens=None,
            correlation_id=None,
        ) -> str:
            calls.append(
                {
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "correlation_id": correlation_id,
                }
            )
            return "Alt provider summary"

    alternate = FakeAlternateProvider()

    result = summarize_transcript("PROMPT_SECRET", completion_client=alternate)

    assert result == "Alt provider summary"
    assert len(calls) == 1
    # Non-streaming CompletionClient callers keep the previous summary budget.
    assert calls[0]["max_tokens"] == ai_provider.SUMMARY_OUTPUT_TOKENS
