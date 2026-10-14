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


def test_cloud_actsis_profile_uses_only_immutable_ghcr_app_images() -> None:
    text = _compose_text()
    services = (
        "cloud-actsis-env-check",
        "redis-cloud-actsis",
        "db-cloud-actsis",
        "minio-cloud-actsis",
        "minio-init-cloud-actsis",
        "qdrant-cloud-actsis",
        "api-cloud-actsis",
        "worker-cloud-actsis",
        "beat-cloud-actsis",
        "web-cloud-actsis",
    )
    blocks = {name: _named_service_block(text, name) for name in services}
    for name, block in blocks.items():
        assert "profiles: [cloud-actsis]" in block, f"{name} must be scoped to cloud-actsis"
        assert "build:" not in block, f"{name} must not build from a source checkout"
        assert "env_file:" not in block, f"{name} must not receive the full root .env"

    expected_images = {
        "api-cloud-actsis": "zabt-ai-api",
        "worker-cloud-actsis": "zabt-ai-worker",
        "beat-cloud-actsis": "zabt-ai-worker",
        "web-cloud-actsis": "zabt-ai-web",
        "minio-cloud-actsis": "zabt-ai-minio",
        "minio-init-cloud-actsis": "zabt-ai-mc",
    }
    for service, image in expected_images.items():
        block = blocks[service]
        expected = (
            f"image: ghcr.io/danielrondongarcia/{image}:"
            "${ZABT_IMAGE_TAG:-missing-zabt-image-tag}"
        )
        assert expected in block, f"{service} must use the single release tag"
        assert "pull_policy: always" in block

    profile_services = "\n".join(blocks.values())
    for forbidden in ("worker-gpu", "runpod", "kong", "vision", "zabt-vision-worker"):
        assert forbidden.casefold() not in profile_services.casefold()


def test_cloud_actsis_aligns_transcription_storage_and_credential_scope() -> None:
    text = _compose_text()
    transcription = re.search(
        r"(?ms)^x-cloud-actsis-backend-environment:.*?(?=^x-|^services:)",
        text,
    )
    storage = re.search(
        r"(?ms)^x-cloud-actsis-storage-environment:.*?(?=^x-|^services:)",
        text,
    )
    assert transcription and storage

    expected_transcription = (
        "TRANSCRIPTION_PROVIDER: openai-file",
        "TRANSCRIPTION_MODEL: whisper-diarize",
        "TRANSCRIPTION_BASE_URL: ${TRANSCRIPTION_BASE_URL:-https://ai.actsis.internal/v1}",
        'TRANSCRIPTION_CLOUD_DIARIZATION: "true"',
        "TRANSCRIPTION_RESPONSE_FORMAT: diarized_json",
        'TRANSCRIPTION_SPEAKER_REQUIRED: "true"',
        "ACTSIS_API_KEY: ${ACTSIS_API_KEY:-}",
    )
    for setting in expected_transcription:
        assert setting in transcription.group()
    assert "TRANSCRIPTION_BACKEND:" not in transcription.group()

    assert "STORAGE_PROVIDER: minio" in storage.group()
    assert "MINIO_ENDPOINT: minio:9000" in storage.group()
    assert "MINIO_WEBHOOK_SECRET: ${MINIO_WEBHOOK_SECRET:-}" in storage.group()
    api = _named_service_block(text, "api-cloud-actsis")
    worker = _named_service_block(text, "worker-cloud-actsis")
    beat = _named_service_block(text, "beat-cloud-actsis")
    web = _named_service_block(text, "web-cloud-actsis")
    assert "MINIO_PUBLIC_ENDPOINT: ${MINIO_PUBLIC_ENDPOINT:-}" in api
    assert "case \"$$MINIO_PUBLIC_ENDPOINT\"" in api
    assert "MINIO_PUBLIC_ENDPOINT:" not in worker
    assert "MINIO_PUBLIC_ENDPOINT:" not in beat

    for service, block in (("api", api), ("worker", worker), ("beat", beat)):
        assert "*cloud-actsis-backend-environment" in block, (
            f"{service} must receive the Actsis transcription settings and key"
        )
        assert "*cloud-actsis-storage-environment" in block, (
            f"{service} must receive the shared MinIO settings"
        )
    assert "ACTSIS_API_KEY:" not in web
    assert "MINIO_WEBHOOK_SECRET" not in web

    allowed_web_environment = {
        "NODE_ENV",
        "ZABT_PUBLIC_API_URL",
        "ZABT_PUBLIC_FRONTEND_URL",
        "NEXT_PUBLIC_POSTHOG_KEY",
        "NEXT_PUBLIC_POSTHOG_HOST",
        "NEXT_PUBLIC_SENTRY_DSN",
        "NEXT_PUBLIC_SENTRY_ENVIRONMENT",
    }
    web_environment = re.search(r"(?ms)^    environment:\s*\n(?P<body>.*?)(?=^    [a-z])", web)
    assert web_environment
    environment_names = set(
        re.findall(r"(?m)^      ([A-Z][A-Z0-9_]+):", web_environment.group("body"))
    )
    assert environment_names == allowed_web_environment
    for forbidden in (
        "ACTSIS_API_KEY",
        "DATABASE_URL",
        "AUTH_JWT_SECRET",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ):
        assert forbidden not in web_environment.group("body")


def test_cloud_actsis_preflight_requires_presence_flags_without_values() -> None:
    text = _compose_text()
    checker = _named_service_block(text, "cloud-actsis-env-check")
    required = (
        "ZABT_IMAGE_TAG",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "AUTH_JWT_SECRET",
        "AUTH_ALLOWED_ORIGINS",
        "BACKEND_CORS_ORIGINS",
        "APP_URL",
        "MCP_PUBLIC_URL",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET_NAME",
        "MINIO_WEBHOOK_SECRET",
        "MINIO_PUBLIC_ENDPOINT",
        "ACTSIS_API_KEY",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_FRONTEND_URL",
    )
    for name in required:
        assert f"HAS_{name}: ${{{name}:+1}}" in text
        assert f'test "$$HAS_{name}" = 1' in checker
        assert f"{name} must be set" in checker

    environment = re.search(r"(?ms)^    environment:\s*\n(?P<body>.*?)(?=^    [a-z])", checker)
    assert environment
    assert all(
        name.startswith("HAS_")
        for name in re.findall(r"(?m)^      ([A-Z][A-Z0-9_]+):", environment.group("body"))
    )
    assert "MINIO_PUBLIC_ENDPOINT must be set to a browser-reachable HTTPS URL" in checker
    assert "$$MINIO_PUBLIC_ENDPOINT" not in checker
    assert '"${MINIO_PUBLIC_ENDPOINT' not in checker


def test_cloud_actsis_publishes_configurable_host_ports_and_local_services() -> None:
    text = _compose_text()
    expected_ports = {
        "api-cloud-actsis": "${API_HOST_BIND:-0.0.0.0}:${API_PORT:-8000}:8000",
        "web-cloud-actsis": "${WEB_HOST_BIND:-0.0.0.0}:${WEB_PORT:-3000}:3000",
        "db-cloud-actsis": "${POSTGRES_HOST_BIND:-127.0.0.1}:${POSTGRES_HOST_PORT:-5433}:5432",
        "minio-cloud-actsis": "${MINIO_HOST_BIND:-0.0.0.0}:${MINIO_HOST_PORT:-9000}:9000",
        "qdrant-cloud-actsis": "${QDRANT_HOST_BIND:-127.0.0.1}:${QDRANT_HOST_PORT:-6333}:6333",
    }
    for service, mapping in expected_ports.items():
        assert mapping in _named_service_block(text, service)

    assert "image: redis:7-alpine" in _named_service_block(text, "redis-cloud-actsis")
    assert "image: postgres:16-alpine" in _named_service_block(text, "db-cloud-actsis")
    assert (
        "image: qdrant/qdrant@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10"
        in _named_service_block(text, "qdrant-cloud-actsis")
    )
    assert "./docker/postgres/init.sql" not in _named_service_block(text, "db-cloud-actsis")


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
