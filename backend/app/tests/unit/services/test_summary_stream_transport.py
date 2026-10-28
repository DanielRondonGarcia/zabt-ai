# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public `complete_summary` streaming primitive contract (T2, unit A).

Adapter-level deterministic tests for the summary-only Chat Completions
streaming transport on `OpenAICompatibleCompletionClient.complete_summary`:
the official OpenAI SDK over a local `httpx.MockTransport` with synthetic SSE
bodies. No ai_agent wiring involved, no live provider, no network beyond the
local mock transport, no database.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from app.services.ai_provider import AIProviderError, OpenAICompatibleCompletionClient

_SUMMARY_MODEL = "oc/glm-5.3-flash"
# Scalar per-operation timeout mandated by the specification: a summary-only
# one-hour allowance for streamed reads while connect keeps the bounded
# production default. A scalar timeout is never a total wall deadline.
_SUMMARY_READ_ALLOWANCE = 3600.0
_BOUNDED_CONNECT_SECONDS = 120.0
_MESSAGES = [
    {"role": "system", "content": "SYSTEM_PROMPT_SECRET"},
    {"role": "user", "content": "PROMPT_SECRET"},
]


def _chunk(delta: dict | None = None, *, finish_reason: str | None = None) -> dict:
    return {
        "id": "chatcmpl-wire",
        "object": "chat.completion.chunk",
        "model": _SUMMARY_MODEL,
        "choices": [
            {
                "index": 0,
                "delta": {} if delta is None else delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _usage_chunk() -> dict:
    """A trailing token-accounting chunk carried with no choices."""
    return {
        "id": "chatcmpl-wire",
        "object": "chat.completion.chunk",
        "model": _SUMMARY_MODEL,
        "choices": [],
        "usage": {"prompt_tokens": 5, "total_tokens": 9},
    }


def _sse(*payloads: dict) -> str:
    events = [f"data: {json.dumps(payload)}" for payload in payloads]
    events.append("data: [DONE]")
    return "\n\n".join(events) + "\n\n"


def _terminal_stop(stream_text: str) -> str:
    return _sse(
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"content": stream_text}),
        _chunk({}, finish_reason="stop"),
    )


def _completion_json(content: str) -> str:
    """A non-streaming chat.completion body used by non-summary calls."""
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
    """Official OpenAI SDK client on a local mock transport.

    Returns ``(adapter, captured_requests, queued)``; each request is served the
    next queued body (an SSE string, a completion JSON string, or a
    ``BaseException`` to raise from the transport).
    """
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


def _stream_summary(client: OpenAICompatibleCompletionClient):
    """Call the public summary primitive through pytest for the wire tests."""
    return client.complete_summary(_MESSAGES, temperature=0.3)


# ── Outbound request shape ──────────────────────────────────────────────────


def test_complete_summary_sends_one_standard_chat_completions_request():
    client, captured = _wire_client([_terminal_stop("# Meeting notes\n\n## Summary")])

    result = _stream_summary(client)

    assert "# Meeting notes" in result
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    parsed = json.loads(request.content)
    assert parsed["model"] == _SUMMARY_MODEL
    assert parsed["stream"] is True
    assert parsed["temperature"] == 0.3
    assert parsed["messages"] == _MESSAGES
    # No Zabt-imposed output cap on the summary wire request.
    assert "max_tokens" not in parsed
    assert "max_completion_tokens" not in parsed


def test_complete_summary_consumes_text_and_never_hidden_reasoning():
    body = _sse(
        _chunk(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "HIDDEN_REASONING_SECRET",
            }
        ),
        _chunk({"content": "Meeting notes"}),
        _chunk({"content": None, "reasoning_content": "MORE_HIDDEN_REASONING"}),
        _chunk({}, finish_reason="stop"),
    )
    client, captured = _wire_client([body])

    result = _stream_summary(client)

    assert result == "Meeting notes"
    assert len(captured) == 1


def test_complete_summary_ignores_trailing_usage_chunks_with_empty_choices():
    body = _sse(
        _chunk({"content": "Notes"}),
        _chunk({}, finish_reason="stop"),
        _usage_chunk(),
    )
    client, captured = _wire_client([body])

    result = _stream_summary(client)

    assert result == "Notes"
    assert len(captured) == 1


def test_complete_summary_returns_blank_text_for_a_completed_empty_stream():
    """The primitive itself returns "" on a stop terminal; the dispatch layer
    turns blank content into the safe empty-summary failure."""
    body = _sse(_chunk({"content": ""}), _chunk({}, finish_reason="stop"))
    client, captured = _wire_client([body])

    assert _stream_summary(client) == ""
    assert len(captured) == 1


# ── Rejection contract: every non-success stream is a safe failure ───────────

_REJECTION_CASES = [
    pytest.param(
        _sse(_chunk({"content": "TRUNCATED_PARTIAL_SECRET"}), _chunk({}, finish_reason="length")),
        "token limit",
        ["TRUNCATED_PARTIAL_SECRET"],
        id="partial-content-token-limit",
    ),
    pytest.param(
        _sse(_chunk({"content": None}), _chunk({}, finish_reason="max_tokens")),
        "token limit",
        [],
        id="empty-max-tokens-limit",
    ),
    pytest.param(
        _sse(_chunk({"content": None}), _chunk({}, finish_reason="length")),
        "token limit",
        [],
        id="empty-length-limit",
    ),
    pytest.param(
        _sse(_chunk({}, finish_reason="content_filter")),
        "refused",
        [],
        id="content-filter-finish",
    ),
    pytest.param(
        _sse(
            _chunk({"refusal": "REFUSAL_EXPLANATION_SECRET"}),
            _chunk({}, finish_reason="stop"),
        ),
        "refused",
        ["REFUSAL_EXPLANATION_SECRET"],
        id="refusal-delta",
    ),
    pytest.param(
        _sse(_chunk({"content": "LOOKS_COMPLETE_SECRET"})),
        "before the summary finished",
        ["LOOKS_COMPLETE_SECRET"],
        id="missing-terminal-finish",
    ),
    pytest.param(
        httpx.ReadTimeout("TIMEOUT_SECRET"),
        "timed out",
        ["TIMEOUT_SECRET"],
        id="transport-timeout",
    ),
]


@pytest.mark.parametrize(("body", "expected_message", "leaked_secrets"), _REJECTION_CASES)
def test_complete_summary_rejection_contract_has_one_request_and_no_leak(
    body, expected_message, leaked_secrets
):
    # A second queued body proves no second request is ever attempted.
    client, captured = _wire_client([body, _terminal_stop("never sent")])

    with pytest.raises(AIProviderError) as raised:
        _stream_summary(client)

    assert len(captured) == 1
    message = str(raised.value)
    assert expected_message in message
    assert "PROMPT_SECRET" not in message
    assert "SYSTEM_PROMPT_SECRET" not in message
    for secret in leaked_secrets:
        assert secret not in message


# ── Summary-only timeout allowance ──────────────────────────────────────────


def test_complete_summary_uses_a_one_hour_read_allowance_with_bounded_connect():
    client, captured = _wire_client([_terminal_stop("Notes")])

    _stream_summary(client)

    assert captured[0].extensions["timeout"] == {
        "connect": _BOUNDED_CONNECT_SECONDS,
        "read": _SUMMARY_READ_ALLOWANCE,
        "write": _SUMMARY_READ_ALLOWANCE,
        "pool": _SUMMARY_READ_ALLOWANCE,
    }


def test_non_summary_complete_keeps_the_bounded_default_no_one_hour_allowance():
    client, captured = _wire_client([_completion_json("Answer")])

    title_text = client.complete(
        [{"role": "user", "content": "TITLE_PROMPT"}],
        temperature=0.1,
        max_tokens=50,
    )

    request = captured[0]
    parsed = json.loads(request.content)
    assert title_text == "Answer"
    assert parsed["max_tokens"] == 50
    assert "stream" not in parsed
    timeout = request.extensions["timeout"]
    # No summary one-hour allowance leaks into a non-summary request.
    assert timeout["read"] != 3600.0
    assert timeout["connect"] != 3600.0


# ── Cleanup on success and failure ──────────────────────────────────────────


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
    """A stream-shaped object exposing ``__iter__``/``close`` close tracking."""

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


def _fake_stream_client(stream_factory):
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **request: stream_factory(request))
        )
    )
    return OpenAICompatibleCompletionClient(sdk, model=_SUMMARY_MODEL)


def test_complete_summary_close_is_called_on_success_and_on_error():
    good_stream = _FakeStream([_ns_chunk("A"), _ns_chunk(None, "stop")])
    good_client = _fake_stream_client(lambda request: good_stream)
    assert good_client.complete_summary(_MESSAGES, temperature=0.3) == "A"
    assert good_stream.closed

    failing_stream = _FakeStream([_ns_chunk("Part"), TimeoutError("boom")])
    failing_client = _fake_stream_client(lambda request: failing_stream)
    with pytest.raises(AIProviderError):
        failing_client.complete_summary(_MESSAGES, temperature=0.3)
    assert failing_stream.closed