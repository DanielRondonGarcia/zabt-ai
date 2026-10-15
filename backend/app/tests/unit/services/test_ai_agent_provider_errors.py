# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Safe diagnostics for summary-provider failures."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai_agent import summarize_transcript
from app.services.ai_provider import AIProviderError, OpenAICompatibleCompletionClient


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


def test_network_failure_logs_safe_summary_diagnostics(caplog):
    request = httpx.Request(
        "POST", "https://provider.invalid/chat?api_key=URL_SECRET"
    )
    network_error = httpx.ConnectError(
        "prompt=PROMPT_SECRET api_key=KEY_SECRET body=BODY_SECRET",
        request=request,
    )
    network_error.__cause__ = TimeoutError("TRANSCRIPT_SECRET")
    client = _completion_client(network_error)

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        with pytest.raises(AIProviderError, match="openai provider request failed"):
            summarize_transcript(
                "PROMPT_SECRET",
                style_examples=["STYLE_SECRET"],
                template_body="TEMPLATE_SECRET",
                completion_client=client,
            )

    assert "operation=summary" in caplog.text
    assert "stage=stage_summarize" in caplog.text
    assert "phase=transcript_summary" in caplog.text
    assert "provider=openai" in caplog.text
    assert "model=oc/glm-5.3-flash" in caplog.text
    assert "exception_class=ConnectError" in caplog.text
    assert "cause_class=TimeoutError" in caplog.text
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
        with pytest.raises(AIProviderError, match="openai provider request failed"):
            summarize_transcript("PROMPT_SECRET", completion_client=client)

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


def test_successful_summary_returns_content_without_failure_log(caplog):
    client = _completion_client("Safe summary")

    with caplog.at_level(logging.ERROR, logger="app.services.ai_agent"):
        result = summarize_transcript("PROMPT_SECRET", completion_client=client)

    assert result == "Safe summary"
    assert "summary provider request failed" not in caplog.text
    assert "PROMPT_SECRET" not in caplog.text
