# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from dotenv import load_dotenv
from pydantic import PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings

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

    # Canonical provider selection. TRANSCRIPTION_BACKEND remains a local/RunPod
    # compatibility alias and is never used to select an alternate provider.
    TRANSCRIPTION_PROVIDER: Optional[str] = None
    TRANSCRIPTION_BACKEND: Optional[str] = None
    TRANSCRIPTION_MODEL: str = "gpt-transcribe"
    TRANSCRIPTION_BASE_URL: str = "https://api.openai.com/v1"
    TRANSCRIPTION_API_KEY: str = ""
    TRANSCRIPTION_CLOUD_DIARIZATION: bool = False
    TRANSCRIPTION_LANGUAGE: Optional[str] = None
    TRANSCRIPTION_ALLOWED_LANGUAGES: str = ""
    TRANSCRIPTION_TIMESTAMP_MODE: str = "none"
    TRANSCRIPTION_TIMESTAMP_MODEL: str = "whisper-1"
    TRANSCRIPTION_RESPONSE_FORMAT: str = "json"
    TRANSCRIPTION_SPEAKER_REQUIRED: bool = False
    # OpenAI file uploads stay on the existing single-request path below this
    # safe threshold. Larger media is converted to deterministic local MP3
    # chunks before it reaches the provider's 25 MB limit.
    TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES: int = 20_000_000
    # Custom OpenAI-compatible gateways such as Actsis can opt in to one full
    # media upload below this threshold. The default 0 keeps official OpenAI
    # and custom gateways on the existing safe chunking behavior.
    TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES: int = 0
    TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES: int = 15_000_000
    TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS: float = 600.0
    TRANSCRIPTION_OPENAI_MAX_CHUNKS: int = 1024
    TRANSCRIPTION_OPENAI_MAX_RETRIES: int = 2
    TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS: float = 1.0
    TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS: float = 8.0
    TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS: float = 900.0

    # GPU Service (used when TRANSCRIPTION_BACKEND=gpu-local)
    GPU_SERVICE_URL: str = "http://gpu-worker:8001"
    GPU_LOCAL_TIMEOUT: int = 7200

    # Active long-running jobs refresh this timestamp every 30 seconds.
    MEETING_RECOVERY_GRACE_SECONDS: int = 900

    # RunPod Serverless (used when TRANSCRIPTION_BACKEND=runpod)
    RUNPOD_API_KEY: str = ""
    RUNPOD_ENDPOINT_ID: str = ""
    RUNPOD_POLL_INTERVAL: int = 5
    RUNPOD_TIMEOUT: int = 300

    # Visual breakdown runs in the backend/Celery worker. The legacy transport
    # settings remain accepted for older deployments but are no longer needed.
    VISION_BACKEND: str = "direct"
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
    VISION_OPENAI_API_KEY: str = ""
    VISION_OPENAI_BASE_URL: str = ""
    VISION_OPENAI_MODEL: str = "gpt-4o-mini"
    VISION_OPENAI_IMAGE_DETAIL: Literal["low", "auto", "high"] = "low"
    VISION_OPENAI_MAX_TOKENS: int = 1024
    VISION_FPS: int = 2
    VISION_MAX_FRAMES: int = 120
    VISION_MAX_CANDIDATE_FRAMES: int = 12
    VISION_MAX_SEGMENTS: int = 20
    VISION_MAX_FRAME_BYTES: int = 2_000_000
    VISION_MAX_MEDIA_BYTES: int = 500_000_000
    VISION_CHANGE_THRESHOLD: float = 0.18
    VISION_CONFIDENCE_THRESHOLD: float = 0.7
    VISION_MAX_TRANSCRIPT_CHARS: int = 6000
    VISION_FFMPEG_TIMEOUT_SECONDS: float = 900.0

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

    @model_validator(mode="after")
    def _validate_embedding_settings(self) -> "Settings":
        """Validate embedding provider configuration."""
        provider = (self.EMBEDDING_PROVIDER or "").strip().lower()
        valid_providers = {"ollama", "openai"}

        if provider not in valid_providers:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {sorted(valid_providers)}, "
                f"got '{self.EMBEDDING_PROVIDER}'"
            )

        if provider == "openai":
            # Only check the explicitly set attributes, not env vars
            api_key = (self.EMBEDDING_API_KEY or "").strip()
            if not api_key:
                # Check if OPENAI_API_KEY was explicitly set on this instance
                openai_key = (self.OPENAI_API_KEY or "").strip()
                if not openai_key:
                    raise ValueError(
                        "EMBEDDING_API_KEY (or OPENAI_API_KEY) must be set for "
                        "EMBEDDING_PROVIDER=openai"
                    )

        if self.EMBEDDING_DIMENSION <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be a positive integer")
        if self.EMBEDDING_MAX_BATCH <= 0:
            raise ValueError("EMBEDDING_MAX_BATCH must be a positive integer")

        base_url = (self.EMBEDDING_BASE_URL or "").strip()
        if not base_url:
            raise ValueError("EMBEDDING_BASE_URL must not be empty")

        return self

    @model_validator(mode="after")
    def _validate_transcription_settings(self) -> "Settings":
        provider = (self.TRANSCRIPTION_PROVIDER or "").strip()
        backend = (self.TRANSCRIPTION_BACKEND or "").strip()
        valid_providers = {"gpu-local", "runpod", "openai-file"}
        valid_backends = {"gpu-local", "runpod"}
        if provider and provider not in valid_providers:
            raise ValueError(f"TRANSCRIPTION_PROVIDER must be one of {sorted(valid_providers)}")
        if backend and backend not in valid_backends:
            raise ValueError(f"TRANSCRIPTION_BACKEND must be one of {sorted(valid_backends)}")
        if provider and backend and provider != backend:
            raise ValueError(
                "TRANSCRIPTION_PROVIDER and compatibility TRANSCRIPTION_BACKEND disagree; "
                "configure one provider explicitly"
            )
        selected = provider or backend or "gpu-local"
        self.TRANSCRIPTION_PROVIDER = selected
        self.TRANSCRIPTION_BACKEND = selected if selected in valid_backends else None

        if not self.TRANSCRIPTION_MODEL.strip():
            raise ValueError("TRANSCRIPTION_MODEL must not be empty")
        if not self.TRANSCRIPTION_TIMESTAMP_MODEL.strip():
            raise ValueError("TRANSCRIPTION_TIMESTAMP_MODEL must not be empty")
        if self.TRANSCRIPTION_TIMESTAMP_MODE not in {"none", "segment", "word"}:
            raise ValueError("TRANSCRIPTION_TIMESTAMP_MODE must be none, segment, or word")
        if self.TRANSCRIPTION_RESPONSE_FORMAT not in {"json", "verbose_json", "diarized_json"}:
            raise ValueError(
                "TRANSCRIPTION_RESPONSE_FORMAT must be json, verbose_json, or diarized_json"
            )
        transcription_base_url = self.TRANSCRIPTION_BASE_URL.rstrip("/")
        if not transcription_base_url:
            raise ValueError("TRANSCRIPTION_BASE_URL must not be empty")
        if selected == "openai-file":
            has_provider_key = bool(self.TRANSCRIPTION_API_KEY.strip() or self.ACTSIS_API_KEY.strip())
            has_shared_key = bool(self.OPENAI_API_KEY.strip())
            if transcription_base_url == "https://api.openai.com/v1":
                if not (has_provider_key or has_shared_key):
                    raise ValueError(
                        "TRANSCRIPTION_API_KEY, ACTSIS_API_KEY, or OPENAI_API_KEY is required "
                        "for TRANSCRIPTION_PROVIDER=openai-file"
                    )
            elif not has_provider_key:
                raise ValueError(
                    "TRANSCRIPTION_API_KEY or ACTSIS_API_KEY is required for a custom "
                    "TRANSCRIPTION_BASE_URL"
                )
        if self.TRANSCRIPTION_SPEAKER_REQUIRED and not self.TRANSCRIPTION_CLOUD_DIARIZATION:
            raise ValueError(
                "TRANSCRIPTION_SPEAKER_REQUIRED requires TRANSCRIPTION_CLOUD_DIARIZATION=true"
            )
        if selected == "runpod" and (not self.RUNPOD_API_KEY.strip() or not self.RUNPOD_ENDPOINT_ID.strip()):
            raise ValueError(
                "RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID are required for "
                "TRANSCRIPTION_PROVIDER=runpod"
            )
        if selected != "openai-file":
            return self
        if not 0 < self.TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES < 25_000_000:
            raise ValueError(
                "TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES must be between 1 and 24999999"
            )
        if self.TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES < 0:
            raise ValueError("TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES must not be negative")
        if not 0 < self.TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES <= self.TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES:
            raise ValueError(
                "TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES must be positive and no larger "
                "than TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES"
            )
        if self.TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS <= 0:
            raise ValueError("TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS must be greater than zero")
        if not 0 <= self.TRANSCRIPTION_OPENAI_MAX_RETRIES <= 5:
            raise ValueError("TRANSCRIPTION_OPENAI_MAX_RETRIES must be between 0 and 5")
        if self.TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS < 0:
            raise ValueError("TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS must not be negative")
        if self.TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS < self.TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS:
            raise ValueError(
                "TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS must be at least "
                "TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS"
            )
        if self.TRANSCRIPTION_OPENAI_MAX_CHUNKS <= 0:
            raise ValueError("TRANSCRIPTION_OPENAI_MAX_CHUNKS must be greater than zero")
        if self.TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS <= 0:
            raise ValueError("TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS must be greater than zero")
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

    # Embedding Provider Settings (Ollama/OpenAI-compatible /v1/embeddings)
    EMBEDDING_PROVIDER: str = "ollama"  # "ollama" | "openai"
    EMBEDDING_BASE_URL: str = "http://host.docker.internal:11434/v1"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    EMBEDDING_DIMENSION: int = 768
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MAX_BATCH: int = 32
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION_PREFIX: str = "meeting_embeddings"
    INDEXING_ENABLED: bool = True  # Feature toggle / rollback switch

    # AI Settings (OpenAI-compatible — works with OpenRouter, Together, etc.)
    OPENAI_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""
    # Credentials and optional CA bundle for the internal Actsis OpenAI-compatible gateway.
    # Keep both values in .env; Compose passes them only to backend services.
    ACTSIS_API_KEY: str = ""
    ACTSIS_CA_BUNDLE: str = ""

    # Retrieval-backed chat uses an OpenAI-compatible endpoint. Chat-specific
    # values can override the shared summary settings when a separate gateway
    # is desired; an empty chat key falls back to OPENAI_API_KEY.
    AI_CHAT_BASE_URL: str = "https://api.openai.com/v1"
    AI_CHAT_MODEL: str = "gpt-4o-mini"
    AI_CHAT_API_KEY: str = ""

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
