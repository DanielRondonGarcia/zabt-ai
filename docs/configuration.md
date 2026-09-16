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
| `OPENAI_API_KEY` | — | **REQUIRED for a remote summary endpoint.** It is also the default shared credential for `openai-file` when `TRANSCRIPTION_API_KEY` is empty; use a credential accepted by the official OpenAI API for that path. It remains independent from visual inference and auth settings. |
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
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` / `S3_PUBLIC_URL` / `S3_REGION` | — | Used when `STORAGE_PROVIDER=s3`; explicit values also override the vision worker's bundled-MinIO fallbacks. |

## Transcription

Transcription provider selection, model, and options are independent from the summary and vision
endpoint settings. Copy `.env.example` to `.env`, choose one provider, and pass the same
`TRANSCRIPTION_*` values to the API and worker. For `openai-file`, `TRANSCRIPTION_API_KEY` is an
optional override; when it is empty, the provider uses `OPENAI_API_KEY`. The audio client always
uses the official OpenAI endpoint and never inherits `OPENAI_BASE_URL`. Provider errors are
surfaced explicitly; the registry never silently switches to another provider.

### Provider selection and file options

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSCRIPTION_PROVIDER` | `gpu-local` | `gpu-local`, `runpod`, or `openai-file`. This is the canonical selector. |
| `TRANSCRIPTION_BACKEND` | — | Compatibility alias for `gpu-local`/`runpod` only. Do not set it to a different value from `TRANSCRIPTION_PROVIDER`. |
| `TRANSCRIPTION_MODEL` | `gpt-transcribe` | OpenAI file candidates are `gpt-transcribe` and `gpt-4o-mini-transcribe`; model choice is explicit and configurable. GPU/RunPod model selection remains in their worker settings. |
| `TRANSCRIPTION_API_KEY` | — | Optional credential override for `openai-file`; when empty, `OPENAI_API_KEY` is used. The audio client still uses the official OpenAI endpoint, not `OPENAI_BASE_URL`. Never put a credential in the repository. |
| `TRANSCRIPTION_LANGUAGE` | — | Optional language sent to the selected provider. |
| `TRANSCRIPTION_ALLOWED_LANGUAGES` | — | Optional comma-separated Whisper/provider language hints. |
| `TRANSCRIPTION_TIMESTAMP_MODE` | `none` | `none`, `segment`, or `word`. `word` enables word highlighting and the optional timestamp pass. |
| `TRANSCRIPTION_TIMESTAMP_MODEL` | `whisper-1` | Model used only by the optional timestamp pass. The primary `TRANSCRIPTION_MODEL` remains the text/summary model. |
| `TRANSCRIPTION_RESPONSE_FORMAT` | `json` | Format for the primary text request. It may remain `json`; the separate timestamp request always uses `verbose_json`. |
| `TRANSCRIPTION_SPEAKER_REQUIRED` | `false` | The first-slice providers do not advertise cloud diarization, so `true` is rejected before audio submission. Missing optional speakers remain `SPEAKER_UNKNOWN`. |
| `TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES` | `20000000` | Safe threshold for retaining the existing one-request path; must remain below OpenAI's 25 MB file limit. |
| `TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES` | `15000000` | Post-FFmpeg chunk-size guard; a larger generated chunk fails with an actionable preparation error. |
| `TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS` | `600` | Target duration for deterministic mono 16 kHz MP3 chunks. |
| `TRANSCRIPTION_OPENAI_MAX_CHUNKS` | `1024` | Bounded protection against unexpectedly long media. |
| `TRANSCRIPTION_OPENAI_MAX_RETRIES` | `2` | Additional attempts for transient OpenAI connection, 408, 429, and 5xx failures; the SDK's automatic retries are disabled. |
| `TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS` / `TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS` | `1` / `8` | Bounded exponential retry delay in seconds. |
| `TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS` | `900` | Timeout for each FFmpeg/ffprobe preparation command. |

Examples:

```dotenv
# Default local GPU path; the existing RunPod wire contract remains available.
TRANSCRIPTION_PROVIDER=gpu-local
# TRANSCRIPTION_PROVIDER=runpod
# TRANSCRIPTION_API_KEY=  # optional override; empty uses OPENAI_API_KEY

# Explicit general OpenAI file transcription (not medical or realtime).
# TRANSCRIPTION_PROVIDER=openai-file
# TRANSCRIPTION_MODEL=gpt-transcribe
# TRANSCRIPTION_API_KEY=your-transcription-openai-key
# TRANSCRIPTION_RESPONSE_FORMAT=json
# TRANSCRIPTION_TIMESTAMP_MODEL=whisper-1
# TRANSCRIPTION_TIMESTAMP_MODE=word
# TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES=20000000
# TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES=15000000
# TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS=600
```

`openai-file` submits a local supported audio/media file (including WAV/MP3/MP4 and other documented
suffixes) through the official file-transcription API. Files at or below the safe single-request threshold retain the existing
path. Larger files are decoded locally with the FFmpeg/ffprobe toolchain, converted to mono 16 kHz
MP3, split into deterministic chunks, validated before upload, transcribed sequentially, and
merged with absolute offsets. Temporary chunks are deleted on success or failure; chunk names are
deterministic within one task workspace, but cross-restart resume is not persisted because this
correction does not add a database/schema migration. FFmpeg is installed in the backend API/worker
image. When `TRANSCRIPTION_TIMESTAMP_MODE` is `segment` or `word`, each file/chunk is submitted
twice: the configured primary model (normally `gpt-transcribe`) supplies canonical text and usage,
then `whisper-1` supplies playback timing with `verbose_json`. This adds one OpenAI request and
its associated latency/cost per file/chunk; a failed timestamp pass preserves primary text and
records a capability gap instead of claiming synchronized words. It is a general batch provider:
realtime is not exposed, cloud medical parity is not claimed, and `gpt-4o-transcribe-diarize` is a
future-only candidate until a separate capability contract is verified. The shared credential-gated
fixture lives under `backend/tests/fixtures/transcription/`; it never stores private audio.

Ollama remains supported for the separate visual/LLM paths where their settings say so, but it is
not registered as an audio transcription provider. Generic OpenAI-compatible `base_url` audio is
also not assumed: `OPENAI_BASE_URL` configures summarization only, and an unverified audio provider
selection is rejected rather than routed through another endpoint.

### Local, RunPod, and medical settings

These settings preserve the existing local/RunPod transport and MedASR path:

| Variable | Default | Notes |
|----------|---------|-------|
| `GPU_SERVICE_URL` | `http://worker-gpu:8001` | Local GPU worker URL. |
| `WHISPER_MODEL` | `large-v3` | `tiny`/`base`/`small`/`medium`/`large-v3`. Smaller = faster/less VRAM. |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | Gated on HF — accept terms. |
| `MEDASR_MODEL` | `google/medasr` | Optional medical ASR model. |
| `HF_TOKEN` | — | **REQUIRED for diarization.** Accept the pyannote gate first. |
| `DIARIZATION_MIN_SPEAKERS` / `DIARIZATION_MAX_SPEAKERS` | `1` / `10` | Speaker-count bounds. |
| `RUNPOD_API_KEY` / `RUNPOD_ENDPOINT_ID` | — | Used when `TRANSCRIPTION_PROVIDER=runpod` (or the compatibility alias selects it). |
| `RUNPOD_POLL_INTERVAL` / `RUNPOD_TIMEOUT` | `5` / `1800` | RunPod poll cadence / job timeout (s). |
| `GPU_LOCAL_TIMEOUT` | `7200` | Local `gpu-local` worker job timeout (s), including CPU-only transcription. Change it in `.env` and recreate the backend `worker` with `--no-build`; no image rebuild is required. |

Medical transcription stays on local/RunPod MedASR. Selecting `openai-file` for a medical request
fails explicitly; it does not claim medical parity or replace the existing path. No database or
transcript-schema migration is needed for provider selection, and rollback is configuration-only:
set `TRANSCRIPTION_PROVIDER=gpu-local` or `TRANSCRIPTION_PROVIDER=runpod` with the corresponding
existing settings.

## Visual breakdown (optional; profile `vision`)

Visual processing is disabled by default. Starting the `vision` Compose profile does not enable
the stage by itself: set `VISION_ENABLED=true` only after confirming the selected provider, model,
and egress policy. A failed or unavailable visual stage falls back to a transcript-only summary;
it never switches providers automatically.

| Variable | Default | Notes |
|----------|---------|-------|
| `VISION_ENABLED` | `false` | Explicitly enables the optional visual stage. Keep disabled for transcript-only deployments. |
| `VISION_BACKEND` | `local` | `local` or `runpod`. |
| `VISION_INFERENCE_BACKEND` | `ollama` | Worker inference backend: `ollama` (local) or `openai` (cloud). Provider selection is explicit. |
| `VISION_LOCAL_URL` | `http://zabt-vision-worker:8003` | |
| `VISION_JUDGE_MODEL` | `qwen3-vl:8b-thinking` | Local Ollama model. OpenAI uses `VISION_OPENAI_MODEL` instead. |
| `VISION_OPENAI_API_KEY` | — | Explicit vision-worker key. Set an OpenAI key when `VISION_OPENAI_BASE_URL` is OpenAI; it is intentionally not inherited from the summary key. |
| `VISION_OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible vision endpoint. Cloud use is disabled until the explicit HTTPS allowlist settings are provided. |
| `VISION_OPENAI_MODEL` | `gpt-4o-mini` | Economical default for visual analysis. `gpt-4.1-mini` is an alternative with higher text-token rates. |
| `VISION_OPENAI_IMAGE_DETAIL` | `low` | `low`, `auto`, or `high`. Lower detail reduces image-token usage but can miss small screen text. |
| `VISION_OPENAI_MAX_TOKENS` | `1024` | Bounded output-token budget for each vision request (`1`–`4096`). |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint. |
| `OLLAMA_NO_CLOUD` | `1` | Prevent Ollama from routing inference to a cloud provider. |
| `VISION_REQUIRE_VISION` | `true` | Reject providers that do not advertise vision capability. |
| `VISION_MAX_RETRIES` / `VISION_RETRY_BACKOFF_SECONDS` | `2` / `5` | Bounded visual-worker retries and exponential backoff. |
| `VISION_CLOUD_ALLOWED` | `false` | Cloud calls require an explicit `true` value together with `VISION_INFERENCE_BACKEND=openai`. |
| `VISION_EGRESS_POLICY` | `deny` | OpenAI mode requires the explicit `allowlist` value; local/private Ollama endpoints remain allowed under the private policy. |
| `VISION_ALLOWED_HOSTS` | — | OpenAI mode requires the explicit `api.openai.com` allowlist entry. Use HTTPS endpoints only. |
| `VISION_SIGNED_URL_EXPIRATION` | `3600` | Lifetime in seconds for fresh media URLs sent to the worker. |
| `VISION_RUNPOD_API_KEY` / `VISION_RUNPOD_ENDPOINT_ID` / `VISION_POLL_INTERVAL` / `VISION_TIMEOUT` | — | RunPod vision path. |
| `SUMMARY_CHUNK_SECONDS` / `SUMMARY_MAX_INPUT_TOKENS` | `120` / `6000` | Bounded temporal chunks and request budget for summary context. |

### Use OpenAI for visual analysis

The summary service and visual worker use separate credentials. `OPENAI_API_KEY` may remain an
OpenRouter or other-compatible summary key; set `VISION_OPENAI_API_KEY` to an OpenAI key when
`VISION_OPENAI_BASE_URL` is `https://api.openai.com/v1`. To use cloud visual inference, set:

```dotenv
VISION_ENABLED=true
VISION_INFERENCE_BACKEND=openai
VISION_OPENAI_API_KEY=your-openai-vision-key
VISION_CLOUD_ALLOWED=true
VISION_EGRESS_POLICY=allowlist
VISION_ALLOWED_HOSTS=api.openai.com
VISION_OPENAI_BASE_URL=https://api.openai.com/v1
VISION_OPENAI_MODEL=gpt-4o-mini
VISION_OPENAI_IMAGE_DETAIL=low
VISION_OPENAI_MAX_TOKENS=1024
```

By default, the Compose vision worker stores keyframes and raw pipeline output in the bundled local
MinIO using `http://minio:9000`, region `us-east-1`, the local `minioadmin` credentials, and the
`zabt-ai-bucket` bucket. Explicit `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`,
`S3_SECRET_ACCESS_KEY`, or `S3_BUCKET_NAME` values override those worker fallbacks for external
S3-compatible storage.

The worker converts each selected keyframe to a JPEG base64 data URL and sends it as an
`image_url` content part alongside the existing prompt. Structured judge calls request JSON mode
and validate the returned JSON with the existing Pydantic schema. Keyframes and the related visual
prompt (which can include a short transcript excerpt) leave the worker for OpenAI. Keep API keys,
signed URLs, and image payloads out of logs.

OpenAI's [image guide](https://developers.openai.com/api/docs/guides/images) and
[Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)
document base64 image inputs and the `low`/`auto`/`high` detail controls. `low` is the economical
default, but small screen text can be missed; use `auto` or `high` when fidelity matters. Image
inputs are billed as tokens, so there is no fixed per-image price: the amount varies with image
resolution, model, and detail level. As a current Standard-pricing reference, `gpt-4o-mini` lists
`$0.15 / 1M` input and `$0.60 / 1M` output text tokens, while `gpt-4.1-mini` lists `$0.40 / 1M`
input and `$1.60 / 1M` output text tokens. Check the
[current pricing page](https://developers.openai.com/api/docs/pricing) and image cost calculator
before setting a production budget.

### Keep visual analysis local with Ollama

Set `VISION_INFERENCE_BACKEND=ollama`, keep `OLLAMA_NO_CLOUD=1`, and provide an Ollama host in
`OLLAMA_HOST`. With the default private/in-network endpoint, `VISION_EGRESS_POLICY=deny` remains
valid. This path is independent of the OpenAI vision settings and remains an explicit supported
alternative.

For a private allowlist, list only the internal vision endpoint and inference host in
`VISION_ALLOWED_HOSTS`. Do not put API keys or signed URLs in logs. On provider failure, inspect
the bounded `visual_breakdown_error`, leave `VISION_ENABLED` false while recovering the endpoint,
and retry the meeting after the worker is healthy.

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
