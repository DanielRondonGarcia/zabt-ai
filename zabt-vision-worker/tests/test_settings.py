# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import pytest

from zabt_vision.settings import Settings


def test_settings_loads_defaults(monkeypatch):
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    monkeypatch.delenv("VISION_INFERENCE_BACKEND", raising=False)
    monkeypatch.delenv("VISION_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("VISION_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("VISION_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("VISION_OPENAI_IMAGE_DETAIL", raising=False)
    monkeypatch.delenv("VISION_OPENAI_MAX_TOKENS", raising=False)
    monkeypatch.delenv("VISION_CLOUD_ALLOWED", raising=False)
    monkeypatch.delenv("VISION_EGRESS_POLICY", raising=False)
    monkeypatch.delenv("VISION_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("OLLAMA_NO_CLOUD", raising=False)
    s = Settings()
    assert s.vision_enabled is False
    assert s.vision_inference_backend == "ollama"
    assert s.vision_judge_model == "qwen3-vl:8b-thinking"
    assert s.vision_cloud_allowed is False
    assert s.vision_egress_policy == "deny"
    assert s.vision_allowed_hosts == ""
    assert s.ollama_no_cloud is True
    assert s.vision_openai_api_key is None
    assert s.vision_openai_base_url == "https://api.openai.com/v1"
    assert s.vision_openai_model == "gpt-4o-mini"
    assert s.vision_openai_image_detail == "low"
    assert s.vision_openai_max_tokens == 1024
    assert s.effective_vision_model == "qwen3-vl:8b-thinking"
    assert s.fps == 2
    assert s.phash_threshold == 8
    assert s.confidence_threshold == 0.7
    assert s.ensemble_min_signals == 2


def test_settings_overrides_from_env(monkeypatch):
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_INFERENCE_BACKEND", "transformers")
    monkeypatch.setenv("VISION_JUDGE_MODEL", "qwen3-vl-32b-thinking")
    monkeypatch.setenv("VISION_EGRESS_POLICY", "allowlist")
    monkeypatch.setenv("VISION_ALLOWED_HOSTS", "vision.internal,localhost")
    monkeypatch.setenv("VISION_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("VISION_OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("VISION_OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("VISION_OPENAI_IMAGE_DETAIL", "high")
    monkeypatch.setenv("VISION_OPENAI_MAX_TOKENS", "2048")
    s = Settings()
    assert s.vision_enabled is True
    assert s.vision_inference_backend == "transformers"
    assert s.vision_judge_model == "qwen3-vl-32b-thinking"
    assert s.vision_egress_policy == "allowlist"
    assert s.vision_allowed_hosts == "vision.internal,localhost"
    assert s.vision_openai_api_key == "test-key"
    assert s.vision_openai_base_url == "https://api.openai.com/v1"
    assert s.vision_openai_model == "gpt-4.1-mini"
    assert s.vision_openai_image_detail == "high"
    assert s.vision_openai_max_tokens == 2048


def test_settings_reports_selected_openai_model():
    settings = Settings(
        vision_inference_backend="openai",
        vision_openai_model="gpt-4.1-mini",
    )

    assert settings.effective_vision_model == "gpt-4.1-mini"


def test_settings_requires_independent_vision_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "summary-key")
    monkeypatch.setenv("VISION_OPENAI_API_KEY", "vision-key")

    settings = Settings()

    assert settings.vision_openai_api_key == "vision-key"


def test_settings_does_not_reuse_summary_key_for_vision(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "summary-key")
    monkeypatch.delenv("VISION_OPENAI_API_KEY", raising=False)

    settings = Settings()

    assert settings.vision_openai_api_key is None


def test_settings_rejects_unbounded_openai_token_budget():
    with pytest.raises(ValueError):
        Settings(vision_openai_max_tokens=4097)
