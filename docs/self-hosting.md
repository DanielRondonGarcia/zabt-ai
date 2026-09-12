# Self-hosting zabt.ai (single machine)

This is the default and simplest deployment: everything runs on one machine via Docker
Compose — API, workers, CPU/GPU transcription, Postgres, Redis, MinIO object storage, and the
web UI. Authentication is first-party and stored in local PostgreSQL. The only optional remote
dependencies in this guide are the OpenAI-compatible summary endpoint and, when explicitly
enabled, the OpenAI visual-analysis endpoint. Local Ollama remains supported for visual analysis.

## 1. Prerequisites

- **Docker** and **Docker Compose v2** (`docker compose version`).
- For GPU transcription: an **NVIDIA GPU** + the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
  No GPU is fine — see [CPU-only](#cpu-only).
- ~10-15 GB free disk for model weights and media.
- An **OpenAI-compatible LLM** key (OpenRouter, OpenAI, or a local Ollama/vLLM/LM Studio) for
  summaries. OpenAI visual analysis uses a separate `VISION_OPENAI_API_KEY` when enabled.
- A **Hugging Face** token with the pyannote gate accepted (see below).

Optional visual processing is disabled by default. It is not required for transcript-only
summaries and should be enabled only after the local/private egress controls are configured.

## 2. Configure

```bash
git clone https://github.com/afeef/zabt-ai.git
cd zabt-ai
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Where to get it |
|----------|-----------------|
| `AUTH_JWT_SECRET` | Required in every environment. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the result into `.env` |
| `AUTH_ENVIRONMENT` | Leave `development` for localhost; set `production` for an explicit production deployment |
| `AUTH_ALLOWED_ORIGINS` | Exact web origin(s), normally `http://localhost:3000` locally |
| `OPENAI_API_KEY` | Your LLM provider (e.g. https://openrouter.ai/keys) |
| `HF_TOKEN` | https://huggingface.co/settings/tokens (accept pyannote gate first) |

Leave `COMPOSE_PROFILES=local`, `VISION_ENABLED=false`, and the bundled `DATABASE_URL` / MinIO
defaults as-is for the private transcript-only deployment.

The local development defaults allow plain HTTP on localhost with `AUTH_COOKIE_SECURE=false` and
`AUTH_COOKIE_SAMESITE=lax`. For an internet-facing deployment, set
`AUTH_ENVIRONMENT=production`, put the web and API behind HTTPS, set `AUTH_COOKIE_SECURE=true`,
use a deployment secret generated with the command above, and set `AUTH_ALLOWED_ORIGINS` and
`BACKEND_CORS_ORIGINS` to exact HTTPS origins. Startup rejects a missing/placeholder/weak secret,
production insecure cookies, and `AUTH_COOKIE_SAMESITE=none` without secure cookies. Do not put
access or refresh tokens in browser localStorage.

The web login/register flow sends `client=web` and receives HttpOnly cookies. The mobile flow sends
`client=mobile`, stores the JSON access/refresh pair through SecureStore, and refreshes rotated
sessions after a 401. Refresh sessions are revocable in PostgreSQL. Password reset and email
verification are intentionally not available in this first slice because no email provider is
configured.

For live transcription, prefer the WebSocket access cookie or an `Authorization: Bearer ...`
header. The legacy query-string bearer form (`?token=`) is accepted only with an exact allowed
`Origin`, but URLs can still leak through browser history and proxy/access logs; treat it as a
compatibility path, not the preferred credential transport. The WebSocket also rejects inactive
users and preserves meeting ownership checks.

### pyannote Hugging Face gate

Diarization models are gated and **not** bundled with this repo. Accept the terms on:
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0

Then create a token and set `HF_TOKEN`.

## 3. Start

```bash
docker compose up -d
docker compose logs -f api        # watch startup / migrations
```

- Web UI → http://localhost:3000
- API → http://localhost:8000/docs
- MinIO console → http://localhost:9001 (`minioadmin` / `minioadmin`)

First transcription downloads Whisper + pyannote weights into the `worker_model_cache`
volume (several GB, one-time).

## CPU-only

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
```

Set a smaller model for usable speed, e.g. `WHISPER_MODEL=base` in `.env`. CPU transcription
runs `int8` compute automatically and is roughly 1-5× real-time. The local backend timeout is
`GPU_LOCAL_TIMEOUT=7200` seconds by default, independent of `RUNPOD_TIMEOUT`. If a recording needs
more time, edit `GPU_LOCAL_TIMEOUT` in `.env` and recreate only the backend worker without rebuilding
the image:

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --no-build --force-recreate worker
```

## Optional add-ons

Enable by editing `COMPOSE_PROFILES` in `.env` (comma-separated):

- `COMPOSE_PROFILES=local,bot` — Microsoft Teams meeting bot (headless browser).
- `COMPOSE_PROFILES=local,vision` — visual breakdown worker. It still requires
  `VISION_ENABLED=true` and an explicit inference backend selection.

### Optional OpenAI visual processing

1. Set `VISION_OPENAI_API_KEY` to an OpenAI key. Keep `OPENAI_API_KEY` unchanged for summaries;
   it may target OpenRouter or another compatible provider and is not passed to the vision worker.
2. Set `VISION_ENABLED=true`, keep `VISION_BACKEND=local`, and select OpenAI explicitly:

   ```dotenv
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

3. Start or recreate only the visual worker:

   ```bash
   docker compose --profile vision up -d --build --force-recreate zabt-vision-worker
   ```

4. Confirm the worker health endpoint before processing a video:

   ```bash
   curl http://localhost:8003/health
   ```

OpenAI receives selected keyframes as base64 image inputs for visual inference. The vision worker
does not use the local GPU for that inference; `OCR_USE_GPU=false` remains set for its local OCR
signal. The separate `worker-gpu` transcription service is unchanged. Keyframes and raw pipeline
output are written to local MinIO by default (`S3_ENDPOINT_URL=http://minio:9000`, region
`us-east-1`, `minioadmin` credentials, and bucket `zabt-ai-bucket`). Set the explicit `S3_*`
variables in `.env` to override those vision-worker fallbacks for external S3-compatible storage.
`low` image detail reduces image-token usage but can miss small screen text; use `auto` or `high`
when needed. Image cost varies by resolution, model, and detail; there is no fixed per-image price.

### Optional local visual processing with Ollama

1. Set `VISION_ENABLED=true`, keep `VISION_BACKEND=local`, and set
   `VISION_INFERENCE_BACKEND=ollama`.
2. Set `OLLAMA_HOST` to the local Ollama endpoint and keep `OLLAMA_NO_CLOUD=1`.
3. Set `VISION_EGRESS_POLICY=deny` for an in-network worker, or use `allowlist` with explicit
   `VISION_ALLOWED_HOSTS` entries for the inference host.
4. Start the add-on with `docker compose --profile vision up -d --build`.

Visual failures are non-fatal: the meeting remains usable and the summary falls back to
transcript-only evidence. The pipeline keeps retries bounded and never switches to RunPod or
another provider automatically. If the endpoint is unavailable, set `VISION_ENABLED=false`,
restart the worker and API stack, then retry the meeting after `/health` is healthy. Inspect the
meeting's bounded visual status/error fields rather than searching logs for media, transcripts,
prompts, or signed URLs.

## Operations

```bash
docker compose ps                     # status
docker compose logs -f worker         # transcription/summary pipeline logs
docker compose down                   # stop (keeps volumes/data)
docker compose down -v                # stop and DELETE all data (Postgres, MinIO, models)
docker compose pull && docker compose up -d --build   # update after git pull
```

### Backups
- **Database:** `docker compose exec db pg_dump -U app -d zabt > backup.sql`
- **Object storage:** back up the `minio_data` volume (or your S3 bucket).

## Putting it on the internet

The default binds services to localhost. For remote access, front the `web` (3000) and `api`
(8000) with a TLS-terminating reverse proxy (Caddy, nginx, Traefik) or a tunnel (Cloudflare
Tunnel). Update `APP_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_FRONTEND_URL`,
`BACKEND_CORS_ORIGINS`, and `MINIO_PUBLIC_ENDPOINT` to your public URLs. For a managed-services
/ serverless-GPU topology, see [advanced-runpod-split.md](advanced-runpod-split.md).

## Troubleshooting

- **`could not select device driver "nvidia"`** → NVIDIA Container Toolkit not installed, or
  no GPU. Use the CPU compose file.
- **Diarization fails / 401 from Hugging Face** → `HF_TOKEN` missing or pyannote gate not
  accepted.
- **Uploads don't trigger transcription** → check the `minio-init` container configured the
  bucket webhook, and that `MINIO_WEBHOOK_SECRET` matches between MinIO and the API.
- **Auth errors** → confirm the API and web use the same `AUTH_JWT_SECRET`, the database migration
  reached the `m1n2o3p4q5` local-auth revision, and `AUTH_ALLOWED_ORIGINS` exactly matches the
  browser origin. Existing Supabase-only users are not silently migrated; create a local account.
- **Visual processing is skipped** → confirm both `COMPOSE_PROFILES` contains `vision` and
  `VISION_ENABLED=true`; audio-only and YouTube inputs intentionally remain transcript-only.
- **Visual worker cannot reach OpenAI** → confirm `VISION_OPENAI_API_KEY` is an OpenAI key, the
  worker uses `VISION_INFERENCE_BACKEND=openai`, `VISION_CLOUD_ALLOWED=true`,
  `VISION_EGRESS_POLICY=allowlist`, `VISION_ALLOWED_HOSTS=api.openai.com`, and an HTTPS endpoint.
  Do not broaden the allowlist or reuse an OpenRouter summary key as a first troubleshooting step.
- **Visual worker cannot reach Ollama** → verify `OLLAMA_HOST`, `OLLAMA_NO_CLOUD`, and the
  `VISION_EGRESS_POLICY`/`VISION_ALLOWED_HOSTS` combination. No provider is selected
  automatically.
- **Video analysis fails repeatedly** → leave visual processing disabled while recovering the
  endpoint, then retry after the worker health check succeeds. Existing transcript summaries do
  not need to be regenerated.

## Known limitations not covered by this correction

- Refresh retries in concurrent clients still need single-flight coordination around token rotation.
- Login/register still need rate limiting, lockout policy, and timing equalization for unknown users.
- Existing Supabase-only accounts retain their legacy identifiers but have no automatic local
  password migration.
- Multipart upload URL/completion ownership remains a pre-existing issue and was not changed here.
- Refresh cookies retain their existing API-wide path and should receive a separate scope review.
- Email verification and password reset require a separate delivery-provider feature.
