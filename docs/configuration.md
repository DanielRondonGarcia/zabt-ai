# Configuration reference

All configuration is via environment variables in `.env` (copied from
[`.env.example`](../.env.example), the authoritative source). This page groups the variables
and notes which are required. Values marked **REQUIRED** must be set for a working deployment.

## Compose profiles

| Variable | Default | Notes |
|----------|---------|-------|
| `COMPOSE_PROFILES` | `local` | `local` = bundled db+minio+gpu+web. Add `bot`/`vision` for add-ons. Empty for the cloud split. |

## Container memory limits

The base Compose file applies hard per-service memory caps. Override only the services that need
more room in `.env`; values use Docker memory syntax such as `m` or `g`. These are protective caps.
The default `large-v3` and pyannote workload may need a smaller model or a higher explicit override
if it is OOM-killed.

| Variable | Default | Service |
|----------|---------|---------|
| `ZABT_MEMORY_LIMIT_REDIS` / `ZABT_MEMORY_LIMIT_API` / `ZABT_MEMORY_LIMIT_WORKER` / `ZABT_MEMORY_LIMIT_BEAT` | `256m` / `768m` / `1g` / `256m` | Always-on broker and backend processes. |
| `ZABT_MEMORY_LIMIT_DB` / `ZABT_MEMORY_LIMIT_MINIO` / `ZABT_MEMORY_LIMIT_MINIO_INIT` | `768m` / `768m` / `128m` | Local PostgreSQL and object storage services. |
| `ZABT_MEMORY_LIMIT_WORKER_GPU` | `4g` | Local GPU/CPU transcription worker protective cap; use a smaller model or a higher explicit override if it is OOM-killed. |
| `ZABT_MEMORY_LIMIT_WEB` / `ZABT_MEMORY_LIMIT_QDRANT` | `512m` / `512m` | Local web and vector-store services. |
| `ZABT_MEMORY_LIMIT_WORKER_BOT` | `3g` | Optional browser bot; includes headroom for its `2gb` shared-memory mount. |
| `ZABT_MEMORY_LIMIT_VISION_WORKER` | `1g` | Optional visual breakdown worker. |

### Local Actsis without a GPU

The base Compose file keeps generic transcription and recovery fallbacks for the local GPU,
OpenAI-compatible, and RunPod paths: `600` seconds per request, `2` provider retries, and a
`900`-second stale-recovery grace. Use the dedicated overlay when the local run should use the
HTTPS Actsis diarization path without starting the GPU worker:

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.local-actsis.yml \
  --profile local up -d
```

The overlay sources `ACTSIS_API_KEY` from the untracked `.env` and sets
`whisper-diarize`/`diarized_json`, required cloud speakers, `chunking_strategy=auto`, a
500000000-byte direct-upload threshold, a `7200`-second request timeout, zero provider retries,
and a `9000`-second recovery grace for API, worker, and beat. The grace is intentionally greater
than the blocking request timeout, so Beat does not claim or dispatch a live request at the timeout
boundary. It preserves the local API `http://localhost:8000` and web `http://localhost:3001` ports;
the base GPU worker is moved to an opt-in `gpu` profile and is not started by this command.

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
| `NEXT_PUBLIC_FRONTEND_URL` | `http://localhost:3001` | |
| `APP_URL` | `http://localhost:3001` | Used in email deep-links. |
| `BACKEND_CORS_ORIGINS` | `http://localhost:3001` | Comma-separated allowed origins. |

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

Transcription provider selection, model, endpoint, and options are independent from the summary and
vision settings. Copy `.env.example` to `.env`, choose one provider, and pass the same
`TRANSCRIPTION_*` values to the API and worker. Ensure Beat receives the same
`MEETING_RECOVERY_GRACE_SECONDS` value. For `openai-file`, credential fallback is endpoint-specific:
the official OpenAI endpoint uses `TRANSCRIPTION_API_KEY` then `OPENAI_API_KEY`;
a custom endpoint uses `TRANSCRIPTION_API_KEY` then `ACTSIS_API_KEY`. `OPENAI_BASE_URL` never
controls audio transcription. Provider errors are surfaced explicitly; the registry never silently
switches to another provider.

### Provider selection and file options

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSCRIPTION_PROVIDER` | `gpu-local` | `gpu-local`, `runpod`, or `openai-file`. This is the canonical selector. |
| `TRANSCRIPTION_BACKEND` | — | Compatibility alias for `gpu-local`/`runpod` only. Do not set it to a different value from `TRANSCRIPTION_PROVIDER`. |
| `TRANSCRIPTION_BASE_URL` | `https://api.openai.com/v1` | Audio endpoint for `openai-file`. Custom endpoints are explicit; enabled cloud diarization requires a custom `https://` endpoint. |
| `TRANSCRIPTION_MODEL` | `gpt-transcribe` | OpenAI file candidates are `gpt-transcribe` and `gpt-4o-mini-transcribe`. Custom Actsis diarization uses `whisper-diarize`; GPU/RunPod model selection remains in their worker settings. |
| `TRANSCRIPTION_API_KEY` | — | Optional credential override for `openai-file`. Empty falls back to `OPENAI_API_KEY` at the official endpoint or `ACTSIS_API_KEY` at a custom endpoint. Never put a credential in the repository. |
| `ACTSIS_API_KEY` | — | Fallback credential only for a custom `TRANSCRIPTION_BASE_URL`; it is never used as the official OpenAI fallback. |
| `TRANSCRIPTION_CLOUD_DIARIZATION` | `false` | Must be `true` for speaker-required cloud transcription. `whisper-diarize` also requires a custom HTTPS endpoint. |
| `TRANSCRIPTION_LANGUAGE` | — | Optional language sent to the selected provider. |
| `TRANSCRIPTION_ALLOWED_LANGUAGES` | — | Optional comma-separated Whisper/provider language hints. |
| `TRANSCRIPTION_TIMESTAMP_MODE` | `none` | `none`, `segment`, or `word`. `word` enables word highlighting and the optional timestamp pass. |
| `TRANSCRIPTION_TIMESTAMP_MODEL` | `whisper-1` | Model used only by the optional timestamp pass. The primary `TRANSCRIPTION_MODEL` remains the text/summary model. |
| `TRANSCRIPTION_RESPONSE_FORMAT` | `json` | Primary format: `json`, `verbose_json`, or `diarized_json`. `diarized_json` is required for speaker-required or `whisper-diarize` requests; timestamp passes use `verbose_json`. |
| `TRANSCRIPTION_SPEAKER_REQUIRED` | `false` | Requires `TRANSCRIPTION_CLOUD_DIARIZATION=true` for `openai-file`; enabled cloud diarization rejects unlabeled segments instead of inventing speakers. |
| `TRANSCRIPTION_CHUNKING_STRATEGY` | — | Optional provider chunking hint. Actsis diarization uses `auto`. |
| `TRANSCRIPTION_OPENAI_SINGLE_REQUEST_MAX_BYTES` | `20000000` | Safe threshold for retaining the existing one-request path; must remain below OpenAI's 25 MB file limit. |
| `TRANSCRIPTION_OPENAI_CHUNK_MAX_BYTES` | `15000000` | Post-FFmpeg chunk-size guard; a larger generated chunk fails with an actionable preparation error. |
| `TRANSCRIPTION_OPENAI_CHUNK_DURATION_SECONDS` | `600` | Target duration for deterministic mono 16 kHz MP3 chunks. |
| `TRANSCRIPTION_OPENAI_MAX_CHUNKS` | `1024` | Bounded protection against unexpectedly long media. |
| `TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS` | `600` | Generic blocking request timeout, passed to both the OpenAI SDK and its httpx transport. Full-cloud Actsis defaults to `7200`. |
| `TRANSCRIPTION_OPENAI_MAX_RETRIES` | `2` | Additional attempts for transient connection, 408, 429, and 5xx failures; full-cloud Actsis defaults to `0` to avoid repeating a long request. |
| `TRANSCRIPTION_OPENAI_RETRY_BACKOFF_SECONDS` / `TRANSCRIPTION_OPENAI_RETRY_MAX_BACKOFF_SECONDS` | `1` / `8` | Bounded exponential retry delay in seconds. |
| `MEETING_RECOVERY_GRACE_SECONDS` | `900` | Stale recovery grace after the last heartbeat. Keep it above the maximum blocking provider request plus an operational margin; full-cloud and local Actsis runtime overrides use `9000` when the request timeout is `7200`. |
| `TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS` | `900` | Timeout for each FFmpeg/ffprobe preparation command. |

Examples:

```dotenv
# Default local GPU path; the existing RunPod wire contract remains available.
TRANSCRIPTION_PROVIDER=gpu-local
# TRANSCRIPTION_PROVIDER=runpod
# TRANSCRIPTION_API_KEY=  # optional; fallback depends on TRANSCRIPTION_BASE_URL

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

# Custom HTTPS Actsis diarization. TRANSCRIPTION_API_KEY may override ACTSIS_API_KEY.
# TRANSCRIPTION_PROVIDER=openai-file
# TRANSCRIPTION_BASE_URL=https://ai.actsis.internal/v1
# ACTSIS_API_KEY=<actsis-key>
# TRANSCRIPTION_API_KEY=
# TRANSCRIPTION_MODEL=whisper-diarize
# TRANSCRIPTION_CLOUD_DIARIZATION=true
# TRANSCRIPTION_RESPONSE_FORMAT=diarized_json
# TRANSCRIPTION_SPEAKER_REQUIRED=true
# TRANSCRIPTION_CHUNKING_STRATEGY=auto
# TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS=7200
# TRANSCRIPTION_OPENAI_MAX_RETRIES=0
# MEETING_RECOVERY_GRACE_SECONDS=9000
```

`openai-file` submits a local supported audio/media file (including WAV/MP3/MP4 and other documented
suffixes) to `TRANSCRIPTION_BASE_URL`. The official OpenAI endpoint is the default; custom endpoints
such as Actsis are explicit. Files at or below the safe single-request threshold retain the existing
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
realtime is not exposed and cloud medical parity is not claimed. Actsis `whisper-diarize` is the
explicit custom-HTTPS cloud-diarization path and requires `diarized_json` plus speaker labels. The
shared credential-gated fixture lives under `backend/tests/fixtures/transcription/`; it never stores
private audio.

Ollama remains supported for the separate visual/LLM paths where their settings say so, but it is
not registered as an audio transcription provider. Audio endpoints must be selected explicitly with
`TRANSCRIPTION_BASE_URL`; an unverified provider selection is rejected rather than silently routed
through `OPENAI_BASE_URL`.

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

### Microsoft Entra public SPA OIDC

Microsoft Entra OIDC is configured globally from **Integrations** by an
administrator. The browser uses a public SPA client with PKCE, so no OIDC
client secret belongs in `.env`. The redirect URI must be on the same origin as
the frontend SPA. Local development defaults to
`http://localhost:3001/login`; the frontend derives the current browser origin
at runtime. In production, register the actual frontend HTTPS origin followed
by `/login`, for example `https://app.example.com/login`.

The delegated Graph callback remains a separate backend URL:
`https://api.example.com/api/v1/integrations/microsoft/callback`. Do not use
that backend callback as the public SPA OIDC redirect.

| Variable | Notes |
|----------|-------|
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_TENANT_ID` / `MICROSOFT_REDIRECT_URI` | Server-side delegated Microsoft Graph/Teams OAuth only. These values are separate from OIDC. |
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
