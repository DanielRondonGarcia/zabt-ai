# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Static safeguards for the standalone full-cloud Compose topology."""

from __future__ import annotations

import re
from pathlib import Path


COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.full-cloud.yml"
BASE_COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"
LOCAL_ACTSIS_COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.local-actsis.yml"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "docs" / "full-cloud.env.example"


def _compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _env_example_text() -> str:
    return ENV_EXAMPLE_PATH.read_text(encoding="utf-8")


def _base_compose_text() -> str:
    return BASE_COMPOSE_PATH.read_text(encoding="utf-8")


def _local_actsis_compose_text() -> str:
    return LOCAL_ACTSIS_COMPOSE_PATH.read_text(encoding="utf-8")


def _service_block(text: str) -> str:
    match = re.search(r"(?ms)^services:\s*\n(?P<body>.*)$", text)
    assert match, "full-cloud Compose file must declare services"
    return match.group("body")


def _named_service_block(text: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\s*\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\s*$|\Z)",
        text,
    )
    assert match, f"Compose file must declare service {service!r}"
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
    for forbidden in ("GPU_SERVICE_URL", "HF_TOKEN", "pyannote", "worker-gpu", "zabt-vision-worker"):
        assert forbidden.casefold() not in text.casefold()


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
    assert "TRANSCRIPTION_MODEL: whisper-diarize" in text
    assert "TRANSCRIPTION_RESPONSE_FORMAT: diarized_json" in text
    assert 'TRANSCRIPTION_CLOUD_DIARIZATION: "true"' in text
    assert 'TRANSCRIPTION_SPEAKER_REQUIRED: "true"' in text
    assert "TRANSCRIPTION_CHUNKING_STRATEGY: auto" in text
    assert "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS: ${TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS:-7200}" in text
    assert "TRANSCRIPTION_OPENAI_MAX_RETRIES: ${TRANSCRIPTION_OPENAI_MAX_RETRIES:-0}" in text
    assert "MEETING_RECOVERY_GRACE_SECONDS: ${MEETING_RECOVERY_GRACE_SECONDS:-9000}" in text
    assert "ACTSIS_API_KEY: ${ACTSIS_API_KEY:?" in text
    assert "ACTSIS_API_KEY: ${OPENAI_API_KEY" not in text
    assert "TRANSCRIPTION_API_KEY: ${OPENAI_API_KEY" not in text
    assert "EMBEDDING_PROVIDER: openai" in text
    assert "VISION_BACKEND: direct" in text
    assert 'VISION_CLOUD_ALLOWED: "true"' in text
    assert "VISION_EGRESS_POLICY: allowlist" in text
    assert "VISION_OPENAI_API_KEY: ${VISION_OPENAI_API_KEY:?" in text
    assert "VISION_OPENAI_API_KEY: ${OPENAI_API_KEY" not in text


def test_full_cloud_env_example_selects_https_actsis_diarization_without_gpu_values() -> None:
    text = _env_example_text()

    assert "ACTSIS_API_KEY=<actsis-key>" in text
    assert "TRANSCRIPTION_BASE_URL=https://ai.actsis.internal/v1" in text
    assert "TRANSCRIPTION_MODEL=whisper-diarize" in text
    assert "TRANSCRIPTION_RESPONSE_FORMAT=diarized_json" in text
    assert "TRANSCRIPTION_CLOUD_DIARIZATION=true" in text
    assert "TRANSCRIPTION_SPEAKER_REQUIRED=true" in text
    assert "TRANSCRIPTION_CHUNKING_STRATEGY=auto" in text
    assert "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS=7200" in text
    assert "TRANSCRIPTION_OPENAI_MAX_RETRIES=0" in text
    assert "MEETING_RECOVERY_GRACE_SECONDS=9000" in text
    assert "TRANSCRIPTION_API_KEY=${OPENAI_API_KEY}" not in text
    for forbidden in ("GPU_SERVICE_URL", "HF_TOKEN", "pyannote"):
        assert forbidden.casefold() not in text.casefold()


def test_base_compose_keeps_generic_timeout_retry_and_recovery_fallbacks() -> None:
    text = _base_compose_text()

    expected_fallbacks = {
        "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS": "600",
        "TRANSCRIPTION_OPENAI_MAX_RETRIES": "2",
        "MEETING_RECOVERY_GRACE_SECONDS": "900",
    }
    for name, value in expected_fallbacks.items():
        setting = f"{name}: ${{{name}:-{value}}}"
        assert text.count(setting) == 3, f"{name} must stay generic for api, worker, and beat"
        assert f"{name}: ${{{name}:-9000}}" not in text


def test_local_actsis_overlay_sets_long_policy_for_api_worker_and_beat_without_secrets() -> None:
    text = _local_actsis_compose_text()

    assert "ACTSIS_API_KEY: ${ACTSIS_API_KEY:?ACTSIS_API_KEY must be set in .env}" in text
    assert "ACTSIS_API_KEY: <" not in text
    assert "HF_TOKEN" not in text
    assert "profiles: !override [gpu]" in text
    assert "- \"8000:8000\"" in _named_service_block(text, "api")
    assert "- \"3001:3000\"" in _named_service_block(text, "web")

    expected_values = {
        "TRANSCRIPTION_MODEL": "whisper-diarize",
        "TRANSCRIPTION_BASE_URL": "https://ai.actsis.internal/v1",
        "TRANSCRIPTION_RESPONSE_FORMAT": "diarized_json",
        "TRANSCRIPTION_CLOUD_DIARIZATION": '"true"',
        "TRANSCRIPTION_SPEAKER_REQUIRED": '"true"',
        "TRANSCRIPTION_CHUNKING_STRATEGY": "auto",
        "TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES": '"500000000"',
        "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS": '"7200"',
        "TRANSCRIPTION_OPENAI_MAX_RETRIES": '"0"',
        "MEETING_RECOVERY_GRACE_SECONDS": '"9000"',
    }
    for service in ("api", "worker", "beat"):
        block = _named_service_block(text, service)
        assert "ACTSIS_API_KEY: ${ACTSIS_API_KEY:?ACTSIS_API_KEY must be set in .env}" in block
        for name, value in expected_values.items():
            assert f"{name}: {value}" in block, f"{service} must set {name} explicitly"
