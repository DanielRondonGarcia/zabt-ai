# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Safe diagnostics for summary-provider failures."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai_agent import infer_title, summarize_transcript
from app.services.multimodal_context import ContextBuildResult, ContextChunk, ContextItem
from app.services.ai_provider import (
    AIProviderError,
    OpenAICompatibleCompletionClient,
    ProviderErrorDiagnostics,
    SUMMARY_OUTPUT_TOKENS,
)



def _completion_client(outcome):
    """A summary-path client: exceptions raise from create, strings stream."""
    if isinstance(outcome, str):
        # Strings stream one content delta followed by a stop terminal.
        outcome = (outcome, "stop")

    class _Completions:
        def create(self, **request):
            if isinstance(outcome, BaseException):
                raise outcome
            content, finish_reason = outcome
            chunks = [_chunk_ns(content)] if content else []
            return StreamingSequencedStream(chunks + [_chunk_ns(None, finish_reason)])

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    return OpenAICompatibleCompletionClient(sdk_client, model="oc/glm-5.3-flash")


class SequencedCompletions:
    """Return one scripted (content, finish_reason) pair per provider call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(dict(request))
        content, finish_reason = self.outcomes.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )


def _chunk_ns(content, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class StreamingSequencedStream:
    """One synthetic stream per summary call; close is observable for cleanup."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._chunks:
            raise StopIteration
        event = self._chunks.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self):
        self.closed = True


class StreamingSequencedCompletions:
    """Return one scripted (content, finish_reason) stream per provider call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.streams: list[StreamingSequencedStream] = []
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(dict(request))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        content, finish_reason = outcome
        chunks = [_chunk_ns(content)] if content else []
        stream = StreamingSequencedStream(chunks + [_chunk_ns(None, finish_reason)])
        self.streams.append(stream)
        return stream


def _sequenced_client(outcomes):
    """Sequenced non-streaming outcomes for the generic `complete` adapter."""
    completions = SequencedCompletions(outcomes)
    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return (
        OpenAICompatibleCompletionClient(sdk_client, model="oc/glm-5.3-flash"),
        completions,
    )


def _streaming_sequenced_client(outcomes):
    """Sequenced summary streams for the standard OpenAI summary transport."""
    completions = StreamingSequencedCompletions(outcomes)
    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return (
        OpenAICompatibleCompletionClient(sdk_client, model="oc/glm-5.3-flash"),
        completions,
    )


def _context(*, chunk_count: int = 2) -> ContextBuildResult:
    chunks = tuple(
        ContextChunk(
            index=index,
            start=float(index * 60),
            end=float((index + 1) * 60),
            items=(
                ContextItem(
                    source="spoken",
                    source_id=index + 1,
                    start=float(index * 60),
                    end=float((index + 1) * 60),
                    content="The team agreed to review the proposal.",
                    relevance="high",
                    provenance="observed",
                    evidence_ref=f"spoken:{index + 1}",
                ),
            ),
            estimated_input_tokens=10,
            complete=True,
        )
        for index in range(chunk_count)
    )
    return ContextBuildResult(
        chunks=chunks,
        source_item_count=chunk_count,
        assigned_item_count=chunk_count,
        unassigned_items=(),
        completeness="complete",
        warning_codes=(),
    )


def test_truncated_empty_completion_retries_with_a_larger_output_budget():
    client, completions = _sequenced_client(
        [("", "length"), ("Recovered summary", "stop")]
    )

    result = client.complete(
        [{"role": "user", "content": "PROMPT_SECRET"}], temperature=0.2
    )

    assert result == "Recovered summary"
    assert len(completions.requests) == 2
    assert completions.requests[1]["max_tokens"] > completions.requests[0]["max_tokens"]


def test_persistent_truncation_raises_actionable_safe_error():
    client, completions = _sequenced_client([("", "length"), ("   ", "length")])

    with pytest.raises(AIProviderError) as raised:
        client.complete(
            [{"role": "user", "content": "PROMPT_SECRET"}], temperature=0.2
        )

    message = str(raised.value)
    assert "output tokens" in message
    assert "PROMPT_SECRET" not in message
    assert len(completions.requests) == 2


def test_truncated_non_empty_completion_is_returned_without_retry():
    client, completions = _sequenced_client([("Cut off text", "length")])

    result = client.complete(
        [{"role": "user", "content": "PROMPT_SECRET"}], temperature=0.2
    )

    assert result == "Cut off text"
    assert len(completions.requests) == 1


def test_summary_truncation_retry_reaches_the_verified_ceiling():
    client, completions = _sequenced_client(
        [("", "length"), ("Recovered summary", "stop")]
    )

    result = client.complete(
        [{"role": "user", "content": "PROMPT_SECRET"}],
        temperature=0.2,
        max_tokens=SUMMARY_OUTPUT_TOKENS,
    )

    assert result == "Recovered summary"
    assert completions.requests[0]["max_tokens"] == SUMMARY_OUTPUT_TOKENS
    assert completions.requests[1]["max_tokens"] > SUMMARY_OUTPUT_TOKENS


# Summary runs on the standard streaming transport: one HTTP request per call
# and a stream that must end with a terminal finish_reason. A window truncated
# by the provider's own output limit fails the window after that one request;
# there is no empty-truncation retry for the summary transport.
def test_one_truncated_window_keeps_the_summary_and_discloses_the_gap():
    client, completions = _streaming_sequenced_client(
        [
            ("", "length"),  # window 1 fails after its single streaming request
            ("Window two notes", "stop"),
            ("Final notes", "stop"),
        ]
    )

    result = summarize_transcript(
        "PROMPT_SECRET", context=_context(chunk_count=2), completion_client=client
    )

    assert result == "Final notes"
    assert len(completions.requests) == 3
    synthesis_prompt = completions.requests[-1]["messages"][1]["content"]
    assert "MISSING TIME WINDOWS" in synthesis_prompt
    assert "window(s) 1" in synthesis_prompt
    assert "PARTIAL WINDOW 2" in synthesis_prompt
    assert "PARTIAL WINDOW 1" not in synthesis_prompt


def test_every_truncated_window_fails_instead_of_returning_an_empty_summary():
    client, completions = _streaming_sequenced_client(
        [("", "length"), ("", "length")]
    )

    with pytest.raises(AIProviderError):
        summarize_transcript(
            "PROMPT_SECRET", context=_context(chunk_count=2), completion_client=client
        )

    # No 16k-to-32k truncation retry: one streaming request per window.
    assert len(completions.requests) == 2
    assert all(request["stream"] is True for request in completions.requests)
    assert all("max_tokens" not in request for request in completions.requests)


def test_summary_stream_requests_no_zabt_token_cap_with_one_request_per_call():
    client, completions = _streaming_sequenced_client([("Safe summary", "stop")])

    summarize_transcript("PROMPT_SECRET", completion_client=client)

    request = completions.requests[0]
    assert request["stream"] is True
    assert "max_tokens" not in request
    assert len(completions.requests) == 1


def test_completion_client_sends_litellm_trace_id_only_when_requested():
    client, completions = _sequenced_client(
        [("Correlated summary", "stop"), ("Uncorrelated result", "stop")]
    )
    trace_id = "a" * 32

    client.complete(
        [{"role": "user", "content": "PROMPT_SECRET"}],
        temperature=0.2,
        correlation_id=trace_id,
    )
    client.complete(
        [{"role": "user", "content": "SIBLING_PROMPT_SECRET"}],
        temperature=0.2,
    )

    assert completions.requests[0]["extra_headers"] == {
        "x-litellm-trace-id": trace_id,
    }
    assert "extra_headers" not in completions.requests[1]


def test_summary_progress_correlation_ids_match_each_provider_call():
    client, completions = _streaming_sequenced_client(
        [("Window one", "stop"), ("Window two", "stop"), ("Final notes", "stop")]
    )
    progress = []

    summarize_transcript(
        "PROMPT_SECRET",
        context=_context(chunk_count=2),
        completion_client=client,
        progress_callback=lambda **event: progress.append(event),
    )

    starts = [event for event in progress if event["outcome"] == "started"]
    terminals = [event for event in progress if event["outcome"] != "started"]
    trace_ids = [event["correlation_id"] for event in starts]
    assert len(trace_ids) == 3
    assert len(set(trace_ids)) == 3
    assert all(len(value) == 32 and all(char in "0123456789abcdef" for char in value) for value in trace_ids)
    for start, terminal, request, trace_id in zip(starts, terminals, completions.requests, trace_ids, strict=True):
        assert terminal["correlation_id"] == trace_id
        assert request["extra_headers"]["x-litellm-trace-id"] == trace_id
    assert all("PROMPT_SECRET" not in repr(event) for event in progress)


def test_sibling_infer_title_keeps_the_bounded_non_streaming_request():
    """The same adapter used by the title stage keeps its 50-token request."""
    client, completions = _sequenced_client([("Solid Meeting Focus", None)])

    title = infer_title("PROMPT_SECRET_LONG_SUMMARY", completion_client=client)

    assert title == "Solid Meeting Focus"
    request = completions.requests[0]
    assert request["max_tokens"] == 50
    assert "stream" not in request
    assert len(completions.requests) == 1


def test_network_failure_logs_safe_summary_diagnostics(caplog):
    request = httpx.Request(
        "POST", "https://provider.invalid/chat?api_key=URL_SECRET"
    )

    class APIConnectionError(Exception):
        pass

    network_error = APIConnectionError(
        "prompt=PROMPT_SECRET api_key=KEY_SECRET body=BODY_SECRET",
    )
    network_error.__cause__ = httpx.ConnectError("TRANSCRIPT_SECRET", request=request)
    client = _completion_client(network_error)

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        with pytest.raises(AIProviderError) as raised:
            summarize_transcript(
                "PROMPT_SECRET",
                style_examples=["STYLE_SECRET"],
                template_body="TEMPLATE_SECRET",
                completion_client=client,
            )

    public_message = str(raised.value)
    assert "could not be reached" in public_message
    assert "No HTTP response was received" in public_message
    assert "quota status cannot be determined" in public_message
    assert "Check connectivity and the provider endpoint before retrying" in public_message
    assert "operation=summary" in caplog.text
    assert "stage=stage_summarize" in caplog.text
    assert "phase=transcript_summary" in caplog.text
    assert "provider=openai" in caplog.text
    assert "model=oc/glm-5.3-flash" in caplog.text
    assert "exception_class=APIConnectionError" in caplog.text
    assert "cause_class=ConnectError" in caplog.text
    assert "http_status=None" in caplog.text
    assert "elapsed_ms=" in caplog.text
    for secret in (
        "PROMPT_SECRET",
        "STYLE_SECRET",
        "TEMPLATE_SECRET",
        "KEY_SECRET",
        "BODY_SECRET",
        "TRANSCRIPT_SECRET",
        "URL_SECRET",
        "provider.invalid",
    ):
        assert secret not in caplog.text
        assert secret not in public_message


def test_provider_http_error_logs_status_code_and_safe_error_metadata(caplog):
    request = httpx.Request(
        "POST", "https://provider.invalid/chat?api_key=URL_SECRET"
    )
    response = httpx.Response(
        429,
        headers={"x-request-id": "req_safe_12345"},
        content=b"BODY_SECRET",
        request=request,
    )
    provider_error = httpx.HTTPStatusError(
        "api_key=KEY_SECRET body=BODY_SECRET prompt=PROMPT_SECRET",
        request=request,
        response=response,
    )
    provider_error.type = "rate_limit_error"
    provider_error.code = "context_length_exceeded"
    client = _completion_client(provider_error)

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        with pytest.raises(AIProviderError) as raised:
            summarize_transcript("PROMPT_SECRET", completion_client=client)

    public_message = str(raised.value)
    assert "rate-limiting requests" in public_message
    assert "rate limit" in public_message
    assert "quota" not in public_message
    assert "billing" not in public_message
    assert "exception_class=HTTPStatusError" in caplog.text
    assert "http_status=429" in caplog.text
    assert "provider_error_type=rate_limit_error" in caplog.text
    assert "provider_error_code=context_length_exceeded" in caplog.text
    assert "request_id=req_safe_12345" in caplog.text
    for secret in (
        "PROMPT_SECRET",
        "KEY_SECRET",
        "BODY_SECRET",
        "URL_SECRET",
        "provider.invalid",
    ):
        assert secret not in caplog.text
        assert secret not in public_message
    assert "context_length_exceeded" not in public_message
    assert "rate_limit_error" not in public_message


def test_explicit_quota_code_on_http_429_has_actionable_safe_message():
    request = httpx.Request("POST", "https://provider.invalid/chat")
    response = httpx.Response(429, content=b"BILLING_BODY_SECRET", request=request)
    provider_error = httpx.HTTPStatusError(
        "billing details BILLING_BODY_SECRET",
        request=request,
        response=response,
    )
    provider_error.code = "insufficient_quota"
    client = _completion_client(provider_error)

    with pytest.raises(AIProviderError) as raised:
        summarize_transcript("PROMPT_SECRET", completion_client=client)

    public_message = str(raised.value)
    assert "quota or billing limits" in public_message
    assert "Check quota and billing before retrying" in public_message
    for secret in (
        "BILLING_BODY_SECRET",
        "insufficient_quota",
        "PROMPT_SECRET",
        "provider.invalid",
    ):
        assert secret not in public_message


@pytest.mark.parametrize(
    ("diagnostics", "expected"),
    [
        (ProviderErrorDiagnostics("APIStatusError", http_status=401), "authentication failed"),
        (ProviderErrorDiagnostics("APIStatusError", http_status=403), "denied access"),
        (ProviderErrorDiagnostics("APITimeoutError"), "request timed out"),
        (ProviderErrorDiagnostics("APIStatusError", http_status=503), "server error (HTTP 503)"),
        (ProviderErrorDiagnostics("APIStatusError", http_status=400), "rejected the request with HTTP 400"),
        (ProviderErrorDiagnostics("UnexpectedProviderError"), "could not be classified"),
    ],
)
def test_other_provider_failures_have_safe_actionable_messages(diagnostics, expected):
    error = AIProviderError("UNTRUSTED_PROVIDER_TEXT", diagnostics=diagnostics)

    public_message = str(error)
    assert expected in public_message
    assert "UNTRUSTED_PROVIDER_TEXT" not in public_message


def test_missing_diagnostics_uses_safe_generic_fallback():
    error = AIProviderError("URL_SECRET BODY_SECRET API_KEY_SECRET")

    public_message = str(error)
    assert public_message == (
        "The AI provider request failed. Check provider configuration and logs "
        "before retrying."
    )
    assert "URL_SECRET" not in public_message
    assert "BODY_SECRET" not in public_message
    assert "API_KEY_SECRET" not in public_message


@pytest.mark.parametrize(
    ("internal_message", "expected"),
    [
        (
            "configured AI provider is unavailable",
            "The configured AI provider is unavailable.",
        ),
        (
            "AI provider settings are unavailable",
            "AI provider settings are unavailable.",
        ),
        (
            "application default AI provider is unavailable",
            "The application default AI provider is unavailable.",
        ),
    ],
)
def test_allowlisted_internal_configuration_messages_remain_useful(
    internal_message: str,
    expected: str,
) -> None:
    public_message = str(AIProviderError(internal_message))

    assert expected in public_message


@pytest.mark.parametrize("context_mode", ["transcript", "single_chunk"])
def test_empty_summary_response_does_not_become_a_successful_summary(caplog, context_mode):
    client = _completion_client("   ")
    context = None
    if context_mode == "single_chunk":
        item = ContextItem(
            source="spoken", source_id=1, start=0, end=1,
            content="Meeting notes", relevance="high", provenance="observed",
            evidence_ref="spoken:1",
        )
        context = ContextBuildResult(
            chunks=(ContextChunk(index=0, start=0, end=1, items=(item,),
                                 estimated_input_tokens=5, complete=True),),
            source_item_count=1, assigned_item_count=1, unassigned_items=(),
            completeness="complete", warning_codes=(),
        )

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        with pytest.raises(AIProviderError) as raised:
            summarize_transcript("PROMPT_SECRET", context=context, completion_client=client)

    assert "PROMPT_SECRET" not in str(raised.value)
    assert "PROMPT_SECRET" not in caplog.text
    assert "empty" in caplog.text


def test_successful_summary_returns_content_without_failure_log(caplog):
    client = _completion_client("Safe summary")

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        result = summarize_transcript("PROMPT_SECRET", completion_client=client)

    assert result == "Safe summary"
    assert "summary provider request failed" not in caplog.text
    assert "PROMPT_SECRET" not in caplog.text
