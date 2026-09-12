# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from dotenv import load_dotenv
from pydantic import PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.models import TranscriptionBackend

# Single .env at the repo root (one file per project, not per service).
# This file lives at <repo>/backend/app/core/config.py — three parents up = repo root.
# In Docker, /app/app/core/config.py resolves to /app, where there is no .env;
# Docker provides config via the `environment:` block instead, which is fine.
_REPO_ROOT_ENV = Path(__file__).resolve().parent.parent.parent.parent / ".env"

AUTH_JWT_SECRET_MIN_LENGTH = 32
_KNOWN_INSECURE_AUTH_JWT_SECRETS = frozenset(
    {
        "local-development-only-change-me",
        "replace-me-with-a-strong-random-secret",
        "change-me",
        "change-me-in-production",
        "your-jwt-secret",
        "your-secret",
        "your-secret-here",
        "secret",
        "password",
        "test",
        "ci-only-local-auth-secret",
    }
)


def validate_auth_jwt_secret(value: str) -> str:
    """Validate the local JWT signing secret without exposing its value."""

    if not isinstance(value, str):
        raise ValueError("AUTH_JWT_SECRET must be a string")

    secret = value.strip()
    if not secret or secret != value:
        raise ValueError("AUTH_JWT_SECRET must be explicitly configured without surrounding whitespace")
    if secret.casefold() in _KNOWN_INSECURE_AUTH_JWT_SECRETS:
        raise ValueError("AUTH_JWT_SECRET is a known placeholder and must be replaced")
    if len(secret.encode("utf-8")) < AUTH_JWT_SECRET_MIN_LENGTH:
        raise ValueError(
            f"AUTH_JWT_SECRET must contain at least {AUTH_JWT_SECRET_MIN_LENGTH} bytes"
        )
    if len(set(secret)) < 12:
        raise ValueError("AUTH_JWT_SECRET does not contain enough character diversity")
    return secret

# Some legacy modules (worker.py, api/upload.py, services/styles.py) read
# directly from os.environ instead of going through this Settings class.
# Eagerly load the root .env into os.environ so those reads succeed when
# `uv run` is invoked from `backend/` for local dev / tests.
if _REPO_ROOT_ENV.exists():
    load_dotenv(_REPO_ROOT_ENV, override=False)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Zabt"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"

    POSTGRES_USER: str = "app"
    POSTGRES_PASSWORD: str = "app"
    POSTGRES_DB: str = "zabt"
    DATABASE_URL: Optional[str] = None
    REDIS_URL: str = "redis://redis:6379/0"

    # MinIO Settings
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_PUBLIC_ENDPOINT: str = ""  # Browser-accessible endpoint for presigned URLs (defaults to MINIO_ENDPOINT)
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "zabt-media"
    MINIO_SECURE: bool = False
    MINIO_WEBHOOK_SECRET: str = "change-me-in-production"

    # Storage Provider Toggle ("minio" or "s3")
    STORAGE_PROVIDER: str = "minio"

    # S3-compatible Cloud Storage (used when STORAGE_PROVIDER=s3)
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET_NAME: str = "zabt-ai-bucket"
    S3_PUBLIC_URL: str = ""  # Public URL for browser presigned URLs (defaults to S3_ENDPOINT_URL)
    S3_REGION: str = "auto"

    # Transcription Backend Toggle
    TRANSCRIPTION_BACKEND: TranscriptionBackend = TranscriptionBackend.RUNPOD

    # GPU Service (used when TRANSCRIPTION_BACKEND=gpu-local)
    GPU_SERVICE_URL: str = "http://gpu-worker:8001"

    # RunPod Serverless (used when TRANSCRIPTION_BACKEND=runpod)
    RUNPOD_API_KEY: str = ""
    RUNPOD_ENDPOINT_ID: str = ""
    RUNPOD_POLL_INTERVAL: int = 5
    RUNPOD_TIMEOUT: int = 300

    # Visual breakdown worker (zabt-vision-worker — see Plan 1/2 specs)
    VISION_BACKEND: str = "local"  # "local" | "runpod"
    VISION_LOCAL_URL: str = "http://zabt-vision-worker:8003"
    VISION_RUNPOD_ENDPOINT_ID: Optional[str] = None
    VISION_RUNPOD_API_KEY: Optional[str] = None
    VISION_JUDGE_MODEL: str = "qwen3-vl:8b-thinking"
    VISION_POLL_INTERVAL: float = 5.0  # seconds between RunPod status polls
    VISION_TIMEOUT: int = 1800  # 30 minutes (per spec)
    VISION_ENABLED: bool = False
    VISION_REQUIRE_VISION: bool = True
    VISION_MAX_RETRIES: int = 2
    VISION_RETRY_BACKOFF_SECONDS: float = 5.0
    VISION_CLOUD_ALLOWED: bool = False
    VISION_EGRESS_POLICY: Literal["deny", "allowlist", "allow"] = "deny"
    VISION_ALLOWED_HOSTS: str = ""
    VISION_SIGNED_URL_EXPIRATION: int = 3600

    # Summary context budgets
    SUMMARY_CHUNK_SECONDS: int = 120
    SUMMARY_MAX_INPUT_TOKENS: int = 6000

    # First-party local authentication. There is deliberately no default: every
    # process that imports the backend must receive the same deployment secret.
    AUTH_ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    AUTH_JWT_SECRET: str
    AUTH_JWT_ALGORITHM: Literal["HS256"] = "HS256"
    AUTH_JWT_ISSUER: str = "zabt-api"
    AUTH_JWT_AUDIENCE: str = "zabt-client"
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    AUTH_REFRESH_SESSION_EXPIRE_DAYS: int = 30
    AUTH_ACCESS_COOKIE_NAME: str = "zabt_access_token"
    AUTH_REFRESH_COOKIE_NAME: str = "zabt_refresh_token"
    AUTH_COOKIE_SECURE: bool = False
    AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    AUTH_COOKIE_DOMAIN: Optional[str] = None
    AUTH_ALLOWED_ORIGINS: str = ""

    @field_validator("AUTH_JWT_SECRET")
    @classmethod
    def _validate_auth_jwt_secret(cls, value: str) -> str:
        return validate_auth_jwt_secret(value)

    @model_validator(mode="after")
    def _validate_auth_cookie_settings(self) -> "Settings":
        if self.AUTH_COOKIE_SAMESITE == "none" and not self.AUTH_COOKIE_SECURE:
            raise ValueError("AUTH_COOKIE_SECURE must be true when AUTH_COOKIE_SAMESITE=none")
        if self.AUTH_ENVIRONMENT == "production" and not self.AUTH_COOKIE_SECURE:
            raise ValueError("AUTH_COOKIE_SECURE must be true when AUTH_ENVIRONMENT=production")
        return self

    # Diarization Settings (passed to GPU service via TranscriptionConfig)
    DIARIZATION_MIN_SPEAKERS: int = 1
    DIARIZATION_MAX_SPEAKERS: int = 10

    # Email (Resend)
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "no-reply@zabt.ai"
    APP_URL: str = "https://app.zabt.ai"

    # Sentry APM
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0

    # Logfire Structured Tracing
    LOGFIRE_TOKEN: str = ""

    # Langfuse LLM Observability
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # PostHog Analytics
    POSTHOG_API_KEY: str = ""
    POSTHOG_HOST: str = "https://us.i.posthog.com"

    # AI Settings (OpenAI-compatible — works with OpenRouter, Together, etc.)
    OPENAI_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""

    # Notifications
    NOTIFICATION_PROVIDER: str = ""  # "telegram" or "" (disabled)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    EXPO_ACCESS_TOKEN: str | None = None

    # Microsoft OAuth (Graph API integration — separate from Supabase login)
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"  # "common" for multi-tenant
    MICROSOFT_REDIRECT_URI: str = ""  # e.g. https://api.zabt.ai/api/v1/integrations/microsoft/callback

    # Token encryption key (Fernet — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    TOKEN_ENCRYPTION_KEY: str = ""

    # Bot Worker
    BOT_WORKER_URL: str = "http://worker-bot:8002"

    # Comma-separated list of allowed CORS origins
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"

    def assemble_db_connection(cls, v: Optional[str], values: Dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return str(PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=values.get("POSTGRES_USER"),
            password=values.get("POSTGRES_PASSWORD"),
            host="db",
            path=f"/{values.get('POSTGRES_DB') or ''}",
        ))

    class Config:
        case_sensitive = True
        env_file = str(_REPO_ROOT_ENV)
        extra = "ignore"

settings = Settings()
