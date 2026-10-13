# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Static safeguards for the root Compose deployment profiles."""

from __future__ import annotations

import re
from pathlib import Path


COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"
BASE_COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "docs" / "full-cloud.env.example"
MINIO_DOCKERFILE_PATH = Path(__file__).resolve().parents[3] / "docker" / "minio" / "Dockerfile"
MINIO_MC_DOCKERFILE_PATH = Path(__file__).resolve().parents[3] / "docker" / "minio" / "Dockerfile.mc"


def _compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _env_example_text() -> str:
    return ENV_EXAMPLE_PATH.read_text(encoding="utf-8")


def _base_compose_text() -> str:
    return BASE_COMPOSE_PATH.read_text(encoding="utf-8")


def _local_actsis_compose_text() -> str:
    return BASE_COMPOSE_PATH.read_text(encoding="utf-8")


def _service_block(text: str) -> str:
    match = re.search(r"(?ms)^services:\s*\n(?P<body>.*)$", text)
    assert match, "full-cloud Compose file must declare services"
    return match.group("body")


def _named_service_block(text: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:(?:[ \t]+&[A-Za-z0-9_-]+)?\s*\n"
        rf"(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:(?:[ \t]+&[A-Za-z0-9_-]+)?\s*$|\Z)",
        text,
    )
    assert match, f"Compose file must declare service {service!r}"
    return match.group("body")


def test_full_cloud_profile_has_published_application_services_without_builds() -> None:
    text = _compose_text()
    names = (
        "full-cloud-env-check",
        "api-full-cloud",
        "worker-full-cloud",
        "beat-full-cloud",
        "web-full-cloud",
    )
    blocks = {name: _named_service_block(text, name) for name in names}
    for name, block in blocks.items():
        assert "profiles: [full-cloud]" in block, f"{name} must be scoped to full-cloud"
        assert "build:" not in block, f"{name} must use a published image"

    app_blocks = "\n".join(blocks[name] for name in names[1:])
    assert "volumes:" not in app_blocks
    for forbidden in ("GPU_SERVICE_URL", "HF_TOKEN", "pyannote", "worker-gpu", "zabt-vision-worker"):
        assert forbidden.casefold() not in app_blocks.casefold()


def test_local_minio_profiles_build_pinned_sources_for_target_architecture() -> None:
    text = _compose_text()
    expected_builds = {
        "minio": ("zabt-ai-minio:local", "docker/minio/Dockerfile"),
        "minio-init": ("zabt-ai-mc:local", "docker/minio/Dockerfile.mc"),
    }

    for service, (image, dockerfile) in expected_builds.items():
        block = _named_service_block(text, service)
        assert "profiles: [local, cpu, local-actsis]" in block
        assert f"image: {image}" in block
        assert "ghcr.io/" not in block
        assert "context: ." in block
        assert f"dockerfile: {dockerfile}" in block

    for service, image in (
        ("minio-cloud-prod", "zabt-ai-minio"),
        ("minio-init-cloud-prod", "zabt-ai-mc"),
    ):
        block = _named_service_block(text, service)
        assert "profiles: [cloud-prod]" in block
        expected_image = (
            f"image: ghcr.io/danielrondongarcia/{image}:"
            "${ZABT_IMAGE_TAG:-missing-zabt-image-tag}"
        )
        assert expected_image in block
        assert "pull_policy: always" in block
        assert "build:" not in block

    for path in (MINIO_DOCKERFILE_PATH, MINIO_MC_DOCKERFILE_PATH):
        dockerfile = path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^ARG TARGETARCH$", dockerfile), (
            f"{path.name} must declare BuildKit TARGETARCH"
        )
        assert "amd64" not in dockerfile, (
            f"{path.name} must not hardcode the build architecture"
        )

    assert "GOARCH=${TARGETARCH}" in MINIO_DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "GOARCH=${TARGETARCH}" in MINIO_MC_DOCKERFILE_PATH.read_text(encoding="utf-8")


def test_full_cloud_requires_external_dependencies_and_isolates_visual_credentials() -> None:
    text = _compose_text()
    required = (
        "ZABT_API_IMAGE",
        "ZABT_WORKER_IMAGE",
        "ZABT_WEB_IMAGE",
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
    checker = _named_service_block(text, "full-cloud-env-check")
    for name in required:
        assert f"HAS_{name}: ${{{name}:+1}}" in text, f"{name} must have a presence flag"
        assert f"{name} must be set in root .env" in checker, f"{name} must fail closed before app startup"

    assert "STORAGE_PROVIDER: s3" in text
    assert "TRANSCRIPTION_PROVIDER: openai-file" in text
    assert "TRANSCRIPTION_BACKEND: openai-file" in text
    assert "TRANSCRIPTION_MODEL: whisper-diarize" in text
    assert "TRANSCRIPTION_RESPONSE_FORMAT: diarized_json" in text
    assert 'TRANSCRIPTION_CLOUD_DIARIZATION: "true"' in text
    assert 'TRANSCRIPTION_SPEAKER_REQUIRED: "true"' in text
    assert "TRANSCRIPTION_CHUNKING_STRATEGY: auto" in text
    assert "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS: ${TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS:-7200}" in text
    assert "TRANSCRIPTION_OPENAI_MAX_RETRIES: ${TRANSCRIPTION_OPENAI_MAX_RETRIES:-0}" in text
    assert "MEETING_RECOVERY_GRACE_SECONDS: ${MEETING_RECOVERY_GRACE_SECONDS:-9000}" in text
    assert "ACTSIS_API_KEY: ${ACTSIS_API_KEY:-}" in text
    assert "ACTSIS_API_KEY: ${OPENAI_API_KEY" not in text
    assert "TRANSCRIPTION_API_KEY: ${OPENAI_API_KEY" not in text
    assert "EMBEDDING_PROVIDER: openai" in text
    assert "VISION_BACKEND: direct" in text
    assert 'VISION_CLOUD_ALLOWED: "true"' in text
    assert "VISION_EGRESS_POLICY: allowlist" in text
    assert "VISION_OPENAI_API_KEY: ${VISION_OPENAI_API_KEY:-}" in text
    assert "VISION_OPENAI_API_KEY: ${OPENAI_API_KEY" not in text


def test_cloud_prod_keeps_both_transcription_selectors_on_gpu_local() -> None:
    text = _compose_text()
    cloud_prod_transcription = re.search(
        r"(?ms)^x-cloud-prod-transcription-environment:.*?(?=^x-|^services:)",
        text,
    )
    assert cloud_prod_transcription
    assert "TRANSCRIPTION_PROVIDER: gpu-local" in cloud_prod_transcription.group()
    assert "TRANSCRIPTION_BACKEND: gpu-local" in cloud_prod_transcription.group()


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
    for service in ("api", "worker", "beat"):
        block = _named_service_block(text, service)
        for name, value in expected_fallbacks.items():
            setting = f"{name}: ${{{name}:-{value}}}"
            assert setting in block, f"{service} must keep the generic {name} fallback"
            assert f"{name}: ${{{name}:-9000}}" not in block


def test_local_actsis_profile_uses_root_env_and_does_not_start_gpu_worker() -> None:
    text = _local_actsis_compose_text()

    expected_settings = (
        "ACTSIS_API_KEY: ${ACTSIS_API_KEY:-}",
        "TRANSCRIPTION_PROVIDER: ${TRANSCRIPTION_PROVIDER:-${TRANSCRIPTION_BACKEND:-gpu-local}}",
        "TRANSCRIPTION_BACKEND: ${TRANSCRIPTION_PROVIDER:-${TRANSCRIPTION_BACKEND:-gpu-local}}",
        "TRANSCRIPTION_MODEL: ${TRANSCRIPTION_MODEL:-whisper-1}",
        "TRANSCRIPTION_BASE_URL: ${TRANSCRIPTION_BASE_URL:-https://ai.actsis.internal/v1}",
        "TRANSCRIPTION_CLOUD_DIARIZATION: ${TRANSCRIPTION_CLOUD_DIARIZATION:-false}",
        "TRANSCRIPTION_RESPONSE_FORMAT: ${TRANSCRIPTION_RESPONSE_FORMAT:-json}",
        "TRANSCRIPTION_SPEAKER_REQUIRED: ${TRANSCRIPTION_SPEAKER_REQUIRED:-false}",
        "TRANSCRIPTION_CHUNKING_STRATEGY: ${TRANSCRIPTION_CHUNKING_STRATEGY:-auto}",
        "TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES: ${TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES:-0}",
        "TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS: ${TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS:-600}",
        "TRANSCRIPTION_OPENAI_MAX_RETRIES: ${TRANSCRIPTION_OPENAI_MAX_RETRIES:-2}",
        "MEETING_RECOVERY_GRACE_SECONDS: ${MEETING_RECOVERY_GRACE_SECONDS:-900}",
    )
    for service in ("api", "worker", "beat"):
        block = _named_service_block(text, service)
        assert "profiles: [local, cpu, local-actsis]" in block
        for setting in expected_settings:
            assert setting in block, f"{service} must source {setting.split(':', 1)[0]} from root .env"
        assert "HF_TOKEN" not in block

    assert "profiles: [local, gpu]" in _named_service_block(text, "worker-gpu")
    assert "profiles: [local, cpu, local-actsis]" in _named_service_block(text, "web")


def test_runpod_profile_sets_matching_provider_selectors_from_legacy_root_setting() -> None:
    text = _compose_text()
    provider = "${TRANSCRIPTION_BACKEND:-runpod}"

    for service in ("api-cloud", "worker-cloud", "beat-cloud"):
        block = _named_service_block(text, service)
        assert "profiles: [cloud]" in block
        assert f"TRANSCRIPTION_PROVIDER: {provider}" in block
        assert f"TRANSCRIPTION_BACKEND: {provider}" in block
