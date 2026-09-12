# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from unittest.mock import patch

import pytest

from zabt_vision.inference.factory import make_inference
from zabt_vision.settings import Settings


def test_factory_selects_openai_with_configured_settings():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key="test-key",
        vision_openai_base_url="https://api.openai.com/v1",
        vision_cloud_allowed=True,
        vision_egress_policy="allowlist",
        vision_allowed_hosts="api.openai.com",
    )

    with patch("zabt_vision.inference.factory.OpenAIInference") as backend:
        inference = make_inference(settings)

    assert inference is backend.return_value
    backend.assert_called_once_with(
        model="gpt-4o-mini",
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        image_detail="low",
        max_tokens=1024,
    )


def test_factory_preserves_local_ollama_backend():
    settings = Settings(vision_inference_backend="ollama", vision_egress_policy="deny")

    with patch("zabt_vision.inference.factory.OllamaInference") as backend:
        inference = make_inference(settings)

    assert inference is backend.return_value
    backend.assert_called_once_with(model=settings.vision_judge_model, host=settings.ollama_host)


def test_factory_rejects_cloud_backend_when_cloud_is_disabled():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key="test-key",
        vision_cloud_allowed=False,
        vision_egress_policy="allowlist",
        vision_allowed_hosts="api.openai.com",
    )

    with pytest.raises(PermissionError, match="cloud vision egress is disabled"):
        make_inference(settings)


def test_factory_rejects_openai_backend_without_a_key():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key=None,
        vision_cloud_allowed=True,
        vision_egress_policy="allowlist",
        vision_allowed_hosts="api.openai.com",
    )

    with pytest.raises(ValueError, match="API key is not configured"):
        make_inference(settings)


def test_factory_rejects_openai_endpoint_outside_allowlist():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key="test-key",
        vision_openai_base_url="https://unexpected.example/v1",
        vision_cloud_allowed=True,
        vision_egress_policy="allowlist",
        vision_allowed_hosts="api.openai.com",
    )

    with pytest.raises(PermissionError, match="not in the vision allowlist"):
        make_inference(settings)


def test_factory_rejects_http_openai_endpoint_even_if_allowlisted():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key="test-key",
        vision_openai_base_url="http://api.openai.com/v1",
        vision_cloud_allowed=True,
        vision_egress_policy="allowlist",
        vision_allowed_hosts="api.openai.com",
    )

    with pytest.raises(PermissionError, match="must use https"):
        make_inference(settings)


def test_factory_requires_allowlist_for_cloud_openai():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_api_key="test-key",
        vision_cloud_allowed=True,
        vision_egress_policy="allow",
        vision_allowed_hosts="api.openai.com",
    )

    with pytest.raises(PermissionError, match="requires the allowlist policy"):
        make_inference(settings)


def test_factory_accepts_private_ollama_endpoint_under_deny_policy():
    settings = Settings(
        vision_inference_backend="ollama",
        ollama_host="http://host.docker.internal:11434",
        vision_cloud_allowed=False,
        vision_egress_policy="deny",
    )

    with patch("zabt_vision.inference.factory.OllamaInference"):
        make_inference(settings)
