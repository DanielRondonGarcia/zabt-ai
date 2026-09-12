# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Inference backend
    vision_inference_backend: Literal[
        "ollama", "openai", "lmstudio", "llamacpp", "transformers"
    ] = "ollama"
    vision_judge_model: str = "qwen3-vl:8b-thinking"

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_no_cloud: bool = True

    # OpenAI-compatible vision provider. This key is intentionally independent
    # from OPENAI_API_KEY, which may target a different summary provider.
    vision_openai_api_key: str | None = Field(
        default=None,
        validation_alias="VISION_OPENAI_API_KEY",
    )
    vision_openai_base_url: str = "https://api.openai.com/v1"
    vision_openai_model: str = "gpt-4o-mini"
    vision_openai_image_detail: Literal["low", "auto", "high"] = "low"
    vision_openai_max_tokens: int = Field(default=1024, ge=1, le=4096)

    # Provider capability and egress policy
    vision_enabled: bool = False
    vision_require_vision: bool = True
    vision_cloud_allowed: bool = False
    vision_egress_policy: Literal["deny", "allowlist", "allow"] = "deny"
    vision_allowed_hosts: str = ""

    # LM Studio (OpenAI-compatible)
    lmstudio_base_url: str = "http://localhost:1234/v1"

    # S3
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "zabt"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    # Pipeline tunables
    fps: int = 2
    phash_threshold: int = 8
    ocr_diff_threshold: float = 0.3
    scenedetect_threshold: float = 27.0
    ensemble_min_signals: int = 2
    confidence_threshold: float = 0.7
    chunk_seconds: int = 120
    refinement_window_seconds: float = 2.0
    refinement_fps: int = 4

    # OCR
    ocr_use_gpu: bool = True  # set False on CPU-only dev/CI hosts

    # Output
    work_dir: str = "/tmp/zabt-vision-worker"

    @property
    def effective_vision_model(self) -> str:
        """Return the model used by the selected inference backend."""
        if self.vision_inference_backend == "openai":
            return self.vision_openai_model
        return self.vision_judge_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance — env vars and .env are parsed once per process."""
    return Settings()
