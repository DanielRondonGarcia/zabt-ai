# Configuration reference

All configuration is via environment variables in `.env` (copied from
[`.env.example`](../.env.example), the authoritative source). This page groups the variables
and notes which are required. Values marked **REQUIRED** must be set for a working deployment.

## Compose profiles

| Variable | Default | Notes |
|----------|---------|-------|
| `COMPOSE_PROFILES` | `local` | `local` = bundled db+minio+gpu+web. Add `bot`/`vision` for add-ons. Empty for the cloud split. |

## Database

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://app:app@db:5432/zabt` | **REQUIRED.** Local default targets the bundled `db`. Use your managed Postgres URL otherwise. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `app` / `app` / `zabt` | Credentials for the bundled Postgres (profile `local`). |

## Local authentication

Zabt uses local email/password authentication backed by the same PostgreSQL database as meetings.
Supabase, Keycloak, Authentik, OAuth SaaS, and an email provider are not required.

| Variable | Default | Notes |
|----------|---------|-------|
| `AUTH_ENVIRONMENT` | `development` | Set to `production` for an explicit production deployment; production requires secure cookies. |
| `AUTH_JWT_SECRET` | **no default** | **REQUIRED in every environment.** Use at least 32 random bytes; never commit it. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE` | `zabt-api` / `zabt-client` | JWT validation boundaries. |
| `AUTH_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Short-lived access credential lifetime. |
| `AUTH_REFRESH_SESSION_EXPIRE_DAYS` | `30` | Database-backed refresh-session lifetime. |
| `AUTH_ACCESS_COOKIE_NAME` / `AUTH_REFRESH_COOKIE_NAME` | `zabt_access_token` / `zabt_refresh_token` | Web HttpOnly cookie names. |
| `AUTH_COOKIE_SECURE` | `false` | Keep `false` for plain localhost; set `true` behind HTTPS. Production rejects `false`. |
| `AUTH_COOKIE_SAMESITE` | `lax` | Cookie policy for the web client. `none` is rejected unless `AUTH_COOKIE_SECURE=true`. |
| `AUTH_COOKIE_DOMAIN` | — | Optional production cookie domain. |
| `AUTH_ALLOWED_ORIGINS` | `BACKEND_CORS_ORIGINS` | Exact origins allowed for cookie-authenticated state changes. |

Web clients use API-owned HttpOnly cookies and never store auth secrets in browser storage. Mobile
clients receive JSON access/refresh tokens and store them through Expo SecureStore (with the
existing Expo Go fallback). Access tokens are accepted as Bearer credentials by protected routes.
The first slice intentionally has no email verification or password reset because it has no email
delivery dependency; the UI reports this limitation honestly.

The API refuses to start when `AUTH_JWT_SECRET` is missing, a known placeholder, shorter than 32
bytes, or too low in character diversity. The verifier pins HS256 and requires `exp`, `iat`, `iss`,
`aud`, `sub`, and the local access-token `type`; issuer and audience are checked against the
configured values. Keep the same secret, issuer, and audience across API, worker, and beat.

WebSocket clients should use the access cookie or an `Authorization: Bearer ...` header. The
legacy `?token=` query parameter remains compatible only when the request has an exact allowed
`Origin`; query strings can still be captured by browser history, reverse-proxy/access logs, or
other request metadata, so do not use it when a header or cookie is available. WebSocket auth also
loads the local user, rejects inactive accounts, and enforces meeting ownership.

### Remaining local-auth limitations

This bounded hardening correction does not add refresh-request single-flight coordination or login
rate limiting/lockout and timing equalization. Existing rows created only through the former
Supabase integration still need an explicit migration/conversion path before local login can use
them. Multipart upload URL/completion ownership remains a pre-existing gap outside this correction.
Refresh cookies still use the existing API-wide path; review that scope separately before exposing
the API through a shared parent domain. Email verification and password reset also remain
intentionally unavailable without a configured delivery provider.

## URLs

| Variable | Default | Notes |
|----------|---------|-------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/v1` | Browser → API base URL. |
| `NEXT_PUBLIC_FRONTEND_URL` | `http://localhost:3000` | |
| `APP_URL` | `http://localhost:3000` | Used in email deep-links. |
| `BACKEND_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins. |

## LLM (summarization)

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint, including Ollama Cloud. |
| `OPENAI_API_KEY` | — | **REQUIRED for a remote endpoint.** Keep this separate from auth settings. |
| `OPENAI_MODEL` | `google/gemini-3.1-flash-lite-preview` | Model id understood by your endpoint. |

## Object storage

| Variable | Default | Notes |
|----------|---------|-------|
| `STORAGE_PROVIDER` | `minio` | `minio` (bundled) or `s3`. |
| `MINIO_ENDPOINT` | `minio:9000` | In-cluster endpoint. |
| `MINIO_PUBLIC_ENDPOINT` | `http://localhost:9000` | Browser-reachable endpoint for presigned URLs. |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `minioadmin` | Change for anything internet-facing. |
| `MINIO_BUCKET_NAME` | `zabt-ai-bucket` | |
| `MINIO_SECURE` | `false` | `true` if MinIO is served over HTTPS. |
| `MINIO_WEBHOOK_SECRET` | `change-me-in-production` | Shared secret for the MinIO→API upload webhook. |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` / `S3_PUBLIC_URL` / `S3_REGION` | — | Used when `STORAGE_PROVIDER=s3`. |

## Transcription

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSCRIPTION_BACKEND` | `gpu-local` | `gpu-local` (bundled worker) or `runpod`. |
| `GPU_SERVICE_URL` | `http://worker-gpu:8001` | Local GPU worker URL. |
| `WHISPER_MODEL` | `large-v3` | `tiny`/`base`/`small`/`medium`/`large-v3`. Smaller = faster/less VRAM. |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | Gated on HF — accept terms. |
| `MEDASR_MODEL` | `google/medasr` | Optional medical ASR model. |
| `HF_TOKEN` | — | **REQUIRED for diarization.** Accept the pyannote gate first. |
| `DIARIZATION_MIN_SPEAKERS` / `DIARIZATION_MAX_SPEAKERS` | `1` / `10` | Speaker-count bounds. |
| `RUNPOD_API_KEY` / `RUNPOD_ENDPOINT_ID` | — | Used when `TRANSCRIPTION_BACKEND=runpod`. |
| `RUNPOD_POLL_INTERVAL` / `RUNPOD_TIMEOUT` | `5` / `1800` | Poll cadence / job timeout (s). |

## Visual breakdown (optional; profile `vision`)

Visual processing is disabled by default. Starting the `vision` Compose profile does not enable
the stage by itself: set `VISION_ENABLED=true` only after confirming that the endpoint, model,
and egress policy are appropriate for the deployment. A failed or unavailable visual stage falls
back to a transcript-only summary; it never switches providers automatically.

| Variable | Default | Notes |
|----------|---------|-------|
| `VISION_ENABLED` | `false` | Explicitly enables the optional visual stage. Keep disabled for transcript-only deployments. |
| `VISION_BACKEND` | `local` | `local` or `runpod`. |
| `VISION_INFERENCE_BACKEND` | `ollama` | Worker inference backend; keep local/private unless cloud use is explicitly approved. |
| `VISION_LOCAL_URL` | `http://zabt-vision-worker:8003` | |
| `VISION_JUDGE_MODEL` | `qwen3-vl:8b-thinking` | Served via Ollama. |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint. |
| `OLLAMA_NO_CLOUD` | `1` | Prevent Ollama from routing inference to a cloud provider. |
| `VISION_REQUIRE_VISION` | `true` | Reject providers that do not advertise vision capability. |
| `VISION_MAX_RETRIES` / `VISION_RETRY_BACKOFF_SECONDS` | `2` / `5` | Bounded visual-worker retries and exponential backoff. |
| `VISION_CLOUD_ALLOWED` | `false` | Must be explicitly enabled before selecting a cloud vision backend. |
| `VISION_EGRESS_POLICY` | `deny` | `deny`, `allowlist`, or explicit `allow`; no implicit provider switch. |
| `VISION_ALLOWED_HOSTS` | — | Comma-separated hosts permitted when using `allowlist`. |
| `VISION_SIGNED_URL_EXPIRATION` | `3600` | Lifetime in seconds for fresh media URLs sent to the worker. |
| `VISION_RUNPOD_API_KEY` / `VISION_RUNPOD_ENDPOINT_ID` / `VISION_POLL_INTERVAL` / `VISION_TIMEOUT` | — | RunPod vision path. |
| `SUMMARY_CHUNK_SECONDS` / `SUMMARY_MAX_INPUT_TOKENS` | `120` / `6000` | Bounded temporal chunks and request budget for summary context. |

For a private allowlist, set `VISION_EGRESS_POLICY=allowlist` and list only the internal vision
endpoint and inference host in `VISION_ALLOWED_HOSTS`. Do not put API keys or signed URLs in
logs. On provider failure, inspect the bounded `visual_breakdown_error`, leave `VISION_ENABLED`
false while recovering the endpoint, and retry the meeting after the worker is healthy.

## Integrations & notifications (optional)

| Variable | Notes |
|----------|-------|
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_TENANT_ID` / `MICROSOFT_REDIRECT_URI` | Microsoft/Teams OAuth. |
| `TOKEN_ENCRYPTION_KEY` | Fernet key encrypting stored OAuth tokens. **Required if you enable integrations.** Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `BOT_DISPLAY_NAME` / `BOT_WORKER_URL` | Teams meeting bot (profile `bot`). |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | Transactional email (Resend). |
| `NOTIFICATION_PROVIDER` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Notifications. |

## Observability (all optional)

| Variable | Notes |
|----------|-------|
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE` | Backend error tracking. |
| `NEXT_PUBLIC_SENTRY_DSN` / `NEXT_PUBLIC_SENTRY_ENVIRONMENT` | Frontend error tracking. |
| `LOGFIRE_TOKEN` | Logfire tracing. |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | LLM observability. |
| `POSTHOG_API_KEY` / `POSTHOG_HOST` / `NEXT_PUBLIC_POSTHOG_KEY` / `NEXT_PUBLIC_POSTHOG_HOST` | Product analytics. |

## Mobile app (optional)

`EXPO_ACCESS_TOKEN`, `EXPO_PUBLIC_API_URL` — only needed if you build the Expo mobile app.
Mobile local authentication uses the API's `client=mobile` contract and SecureStore; no Supabase
variables are needed.

> This table is kept in sync with `.env.example`. If you add a variable to the code, add it to
> both.
