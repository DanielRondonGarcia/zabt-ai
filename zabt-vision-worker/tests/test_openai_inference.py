# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from pydantic import BaseModel

from zabt_vision.inference.openai_backend import OpenAIInference, OpenAIInferenceError


class JudgeResult(BaseModel):
    is_boundary: bool
    confidence: float
    caption: str
    reasoning: str


def test_openai_inference_builds_client_with_base_url_and_key():
    with patch("zabt_vision.inference.openai_backend.openai.OpenAI") as client_factory:
        OpenAIInference(
            model="gpt-4o-mini",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )

    client_factory.assert_called_once_with(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
    )


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_openai_inference_sends_base64_images_and_configured_options():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _response("A login page")
    image = Image.new("RGB", (10, 10), color="white")

    inference = OpenAIInference(
        model="gpt-4o-mini",
        api_key="test-key",
        image_detail="high",
        max_tokens=321,
        client=fake_client,
    )

    assert inference.generate([image], "Describe this screen.") == "A login page"

    request = fake_client.chat.completions.create.call_args.kwargs
    assert request["model"] == "gpt-4o-mini"
    assert request["max_tokens"] == 321
    content = request["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe this screen."}
    image_part = content[1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["detail"] == "high"
    data_url = image_part["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1])[:2] == b"\xff\xd8"


def test_openai_inference_requests_json_mode_and_validates_schema():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _response(
        '{"is_boundary": true, "confidence": 0.9, "caption": "X", "reasoning": "r"}'
    )
    inference = OpenAIInference(model="gpt-4o-mini", api_key="test-key", client=fake_client)

    result = inference.generate([Image.new("RGB", (10, 10))], "Judge.", schema=JudgeResult)

    assert isinstance(result, JudgeResult)
    assert result.is_boundary is True
    request = fake_client.chat.completions.create.call_args.kwargs
    assert request["response_format"] == {"type": "json_object"}
    assert "is_boundary" in request["messages"][0]["content"][0]["text"]


def test_openai_inference_sanitizes_provider_errors():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError(
        "POST https://api.openai.com/v1/chat/completions?api_key=sk-secret "
        "payload=data:image/jpeg;base64,raw-image"
    )
    inference = OpenAIInference(model="gpt-4o-mini", api_key="sk-secret", client=fake_client)

    with pytest.raises(OpenAIInferenceError) as error:
        inference.generate([Image.new("RGB", (10, 10))], "Describe.")

    message = str(error.value)
    assert "sk-secret" not in message
    assert "api.openai.com" not in message
    assert "data:image" not in message


def test_openai_inference_rejects_missing_key_without_client():
    with pytest.raises(ValueError, match="API key is not configured"):
        OpenAIInference(model="gpt-4o-mini", api_key=None)
