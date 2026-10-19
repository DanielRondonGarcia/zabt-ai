# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Safe diagnostics for summary-provider failures."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai_agent import summarize_transcript
from app.services.multimodal_context import ContextBuildResult, ContextChunk, ContextItem
from app.services.ai_provider import (
    AIProviderError,
    OpenAICompatibleCompletionClient,
    ProviderErrorDiagnostics,
    SUMMARY_OUTPUT_TOKENS,
)


class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome

    def create(self, **request):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.outcome))]
        )


def _completion_client(outcome):
    sdk_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(outcome))
    )
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


def _sequenced_client(outcomes):
    completions = SequencedCompletions(outcomes)
    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return (
        OpenAICompatibleCompletionClient(sdk_client, model="oc/glm-5.3-flash"),
        completions,
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


def test_summary_requests_the_dedicated_output_budget():
    client, completions = _sequenced_client([("Safe summary", "stop")])

    summarize_transcript("PROMPT_SECRET", completion_client=client)

    assert completions.requests[0]["max_tokens"] == SUMMARY_OUTPUT_TOKENS


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
