# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Static safeguards for the standalone full-cloud Compose topology."""

from __future__ import annotations

import re
from pathlib import Path


COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.full-cloud.yml"


def _compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _service_block(text: str) -> str:
    match = re.search(r"(?ms)^services:\s*\n(?P<body>.*)$", text)
    assert match, "full-cloud Compose file must declare services"
    return match.group("body")


def test_full_cloud_has_only_application_services_and_no_profiles() -> None:
    text = _compose_text()
    service_block = _service_block(text)
    service_names = re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", service_block, re.MULTILINE)

    assert service_names == ["api", "worker", "beat", "web"]
    assert "profiles:" not in text
    assert "depends_on:" not in text
    assert "volumes:" not in text
    assert "build:" not in text
    assert not re.search(
        r"^  (db|postgres|redis|minio|qdrant|worker-gpu|zabt-vision-worker):\s*$",
        service_block,
        re.MULTILINE | re.IGNORECASE,
    )


def test_full_cloud_requires_external_dependencies_and_isolates_visual_credentials() -> None:
    text = _compose_text()
    required = (
        "DATABASE_URL",
        "REDIS_URL",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET_NAME",
        "S3_PUBLIC_URL",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "ACTSIS_API_KEY",
        "TRANSCRIPTION_BASE_URL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSION",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "AI_CHAT_BASE_URL",
        "VISION_ALLOWED_HOSTS",
        "VISION_OPENAI_API_KEY",
        "VISION_OPENAI_BASE_URL",
    )
    for name in required:
        assert f"{name}: ${{{name}:?" in text, f"{name} must fail closed"

    assert "STORAGE_PROVIDER: s3" in text
    assert "TRANSCRIPTION_PROVIDER: openai-file" in text
    assert "TRANSCRIPTION_RESPONSE_FORMAT: json" in text
    assert 'TRANSCRIPTION_CLOUD_DIARIZATION: "false"' in text
    assert "EMBEDDING_PROVIDER: openai" in text
    assert "VISION_BACKEND: direct" in text
    assert 'VISION_CLOUD_ALLOWED: "true"' in text
    assert "VISION_EGRESS_POLICY: allowlist" in text
    assert "VISION_OPENAI_API_KEY: ${VISION_OPENAI_API_KEY:?" in text
    assert "VISION_OPENAI_API_KEY: ${OPENAI_API_KEY" not in text
