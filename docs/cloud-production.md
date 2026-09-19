# Cloud production Compose deployment

> **Topology note:** This guide documents the single-machine `cloud-prod` stack with local stateful
> services and model workers. For the managed-dependency topology with no local infrastructure or
> model workers, use [`full-cloud-deployment.md`](./full-cloud-deployment.md) and
> `docker-compose.full-cloud.yml` instead.

`docker-compose.cloud.prod.yml` runs a production image stack on one machine. The API, backend
worker, web app, vision worker, and GPU worker come from published release images, while PostgreSQL,
MinIO, Redis, and local GPU transcription stay on the same Compose network. Summaries and visual
analysis use first-party OpenAI cloud inference. NetBird, Caddy, or another external reverse proxy
terminates TLS and forwards HTTP to the published web and API ports.

## Quick path

1. Make sure the host has Docker Compose, an NVIDIA driver/container toolkit for local transcription,
   access to the published GHCR images, and the deployment variables below.
2. Keep real values in the deployment host's `.env` or secret-management layer; never commit that
   file. Set `COMPOSE_PROFILES=cloud-prod` in the process environment so it overrides a conflicting
   local profile value from `.env`.
3. Pull the pinned images and start the stack. This release path intentionally does not use
   `--build`:

   **PowerShell**

   ```powershell
   $env:COMPOSE_PROFILES="cloud-prod"
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml pull
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml up -d
   ```

   **POSIX shell**

   ```bash
   export COMPOSE_PROFILES=cloud-prod
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.cloud.prod.yml \
     pull
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.cloud.prod.yml \
     up -d
   ```

4. Inspect the service state and focused logs before routing traffic through the reverse proxy:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml ps
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs --tail=200 api worker beat web worker-gpu zabt-vision-worker
   ```

   On PowerShell, `Invoke-WebRequest http://127.0.0.1:3000/` checks the web listener and
   `Invoke-WebRequest http://127.0.0.1:8000/` checks the API listener. The GPU and vision workers
   are internal services; check them from the Compose network when needed.

> **Data safety:** Do not use `docker compose down -v` for a restart, profile change, rollback, or
> routine maintenance. The `-v` flag deletes named volumes, including local PostgreSQL, MinIO,
> Redis, media, and model-cache data.

## Topology

| Service | Runtime source | Production role | Host port |
|---|---|---|---|
| `api` | `ZABT_API_IMAGE` | FastAPI API, local MinIO client, OpenAI summary settings | `8000` |
| `worker` / `beat` | `ZABT_WORKER_IMAGE` | Celery processing and scheduled tasks | none |
| `web` | `ZABT_WEB_IMAGE` | Next.js production web app | `3000` |
| `redis` | Base Compose image | Local broker | none |
| `db` | Base Compose `postgres:16-alpine` | Local PostgreSQL with `postgres_data` volume | `5433` |
| `minio` / `minio-init` | Base Compose images | Local object storage and bucket/webhook setup | `9000`, `9001` |
| `worker-gpu` | `ZABT_GPU_IMAGE` | Local WhisperX/diarization transcription over `worker-gpu:8001` | **none** |
| `zabt-vision-worker` | `ZABT_VISION_IMAGE` | Internal visual pipeline with OpenAI inference | **none** |

The `cloud-prod` profile activates `db`, `minio`, `minio-init`, `worker-gpu`, `web`, and
`zabt-vision-worker`. The base `redis`, `api`, `worker`, and `beat` services remain always-on.
`worker-bot` is not activated because its separate `bot` profile is not selected. Named volumes and
the base service names are inherited so backend-to-service networking remains local:

- `DATABASE_URL` points to `db:5432`.
- `REDIS_URL` is fixed to `redis://redis:6379/0`.
- Backend storage uses `STORAGE_PROVIDER=minio` and `MINIO_ENDPOINT=minio:9000`.
- Transcription uses `TRANSCRIPTION_BACKEND=gpu-local` and
  `GPU_SERVICE_URL=http://worker-gpu:8001`.
- The API calls the vision worker at `http://zabt-vision-worker:8003`.
- The vision worker uses MinIO's S3-compatible API at `http://minio:9000`; this is internal local
  networking, not a managed object-storage prerequisite.

## Required launch setting

Set `COMPOSE_PROFILES=cloud-prod` for every Compose command in this guide. The process-level value
overrides a conflicting `COMPOSE_PROFILES=local` entry in `.env`; it is not a secret and is not an
interpolated deployment variable.

## Required deployment variables

The override fails closed when the published image references, database URL, authentication values,
MinIO credentials, OpenAI credentials, or public web URLs are missing. The examples below use the
published `v0.1.0` release; use another immutable version only when that release contains the same
stack contract.

```dotenv
# Published GHCR images
ZABT_API_IMAGE=ghcr.io/<owner>/zabt-ai-api:v0.1.0
ZABT_WORKER_IMAGE=ghcr.io/<owner>/zabt-ai-worker:v0.1.0
ZABT_WEB_IMAGE=ghcr.io/<owner>/zabt-ai-web:v0.1.0
ZABT_GPU_IMAGE=ghcr.io/<owner>/zabt-ai-gpu-worker:v0.1.0
ZABT_VISION_IMAGE=ghcr.io/<owner>/zabt-ai-vision-worker:v0.1.0

# Local PostgreSQL in the Compose network
DATABASE_URL=postgresql+asyncpg://<postgres-user>:<postgres-password>@db:5432/<postgres-db>

# First-party local authentication and public origins
AUTH_JWT_SECRET=<random-secret-at-least-32-bytes>
AUTH_ALLOWED_ORIGINS=https://<web-public-domain>
BACKEND_CORS_ORIGINS=https://<web-public-domain>
APP_URL=https://<web-public-domain>
NEXT_PUBLIC_API_URL=https://<api-public-domain>/api/v1
NEXT_PUBLIC_FRONTEND_URL=https://<web-public-domain>

# Local MinIO. MINIO_ENDPOINT is fixed to minio:9000 by the override.
MINIO_ACCESS_KEY=<minio-access-key>
MINIO_SECRET_KEY=<minio-secret-key>
MINIO_BUCKET_NAME=zabt-ai-bucket
MINIO_PUBLIC_ENDPOINT=http://<browser-reachable-deployment-host>:9000

# Separate first-party OpenAI credentials for summaries and visual analysis
OPENAI_API_KEY=<openai-summary-key>
VISION_OPENAI_API_KEY=<openai-vision-key>

# Set this only when the selected local diarization model requires Hugging Face access.
HF_TOKEN=<hugging-face-token-if-required>
```

`MINIO_PUBLIC_ENDPOINT` defaults to `http://localhost:9000`, which is suitable only when the
browser is on the deployment host. Set it to the deployment host's browser-reachable address (or a
TLS reverse-proxy URL) when users access the web app from another machine. The MinIO access key,
secret, and bucket must match the local `minio` and `minio-init` services.

`HF_TOKEN` is optional for public models. The `pyannote/speaker-diarization-3.1` model commonly
requires an accepted Hugging Face license and token; provide it before starting `worker-gpu` when
the local diarization setup requires that access.

### Local service defaults

The base Compose file provides these local-service defaults. Change the PostgreSQL values together
with `DATABASE_URL` if the deployment uses different credentials.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `app` | Local PostgreSQL user. |
| `POSTGRES_PASSWORD` | `app` | Local PostgreSQL password. Replace for production. |
| `POSTGRES_DB` | `zabt` | Local PostgreSQL database. |
| `MINIO_PUBLIC_ENDPOINT` | `http://localhost:9000` | Browser-facing MinIO endpoint used in presigned URLs. |
| `MINIO_WEBHOOK_SECRET` | `change-me-in-production` | Shared MinIO webhook token; replace for production. |
| `MINIO_SECURE` | `false` | Internal MinIO client scheme. |
| `AUTH_JWT_ISSUER` | `zabt-api` | JWT issuer identifier. |
| `AUTH_JWT_AUDIENCE` | `zabt-client` | JWT audience identifier. |
| `AUTH_COOKIE_SAMESITE` | `lax` | Browser cookie SameSite policy. |
| `VISION_LOCAL_URL` | `http://zabt-vision-worker:8003` | Internal API-to-vision-worker URL. |
| `VISION_SIGNED_URL_EXPIRATION` | `3600` | Lifetime of visual-media URLs in seconds. |

The override fixes `AUTH_ENVIRONMENT=production`, `AUTH_COOKIE_SECURE=true`,
`STORAGE_PROVIDER=minio`, `MINIO_ENDPOINT=minio:9000`, `TRANSCRIPTION_BACKEND=gpu-local`,
`GPU_SERVICE_URL=http://worker-gpu:8001`, and `REDIS_URL=redis://redis:6379/0`.

## OpenAI inference configuration

Summary inference is fixed to the first-party endpoint and model:

```text
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

Visual analysis is also explicit and separate from summaries:

```text
VISION_INFERENCE_BACKEND=openai
VISION_OPENAI_BASE_URL=https://api.openai.com/v1
VISION_OPENAI_MODEL=gpt-4o-mini
VISION_EGRESS_POLICY=allowlist
VISION_ALLOWED_HOSTS=api.openai.com
```

The vision worker has no GPU reservation and no host port. Its only model egress is the HTTPS
OpenAI endpoint allowed above; its local MinIO client remains on the internal Compose network.

Review OpenAI data-processing and retention terms for the selected account, obtain any required
participant consent, and avoid putting raw media, prompts, signed URLs, or credentials in logs.
`VISION_OPENAI_IMAGE_DETAIL=low` is the economical default; `auto` or `high` can improve fidelity
at higher token cost. `VISION_OPENAI_MAX_TOKENS` bounds the response size.

## GHCR image pinning

The release workflow publishes the five images used by this topology with names matching the
variables above:

```text
ghcr.io/<owner>/zabt-ai-api:v0.1.0
ghcr.io/<owner>/zabt-ai-worker:v0.1.0
ghcr.io/<owner>/zabt-ai-web:v0.1.0
ghcr.io/<owner>/zabt-ai-gpu-worker:v0.1.0
ghcr.io/<owner>/zabt-ai-vision-worker:v0.1.0
```

Use immutable version tags rather than mutable tags. Set all five `ZABT_*_IMAGE` variables to the
same release before running `pull` and `up -d`. Do not add `--build`: this topology is intended to
run the published release artifacts.

## Switching from the local profile

Changing from `COMPOSE_PROFILES=local` to `COMPOSE_PROFILES=cloud-prod` preserves named volumes,
but Compose may recreate old local containers so their profiles, images, ports, and environment
match the production override. Expect the local `worker-gpu`, `db`, `minio`, `web`, or vision
containers to be recreated when their effective configuration changes. This does not require
deleting volumes. Back up important data before changing application versions or migrations.

Never use `docker compose down -v` to force a profile transition. Stop or recreate services through
the two-file command shown above and keep the named volumes intact.

## NetBird HTTP routing and firewall

Create two HTTP reverse-proxy routes on the NetBird peer or its adjacent proxy:

| Public route | Peer target | Purpose |
|---|---|---|
| `https://<web-public-domain>` | `http://127.0.0.1:3000` | Next.js production web server |
| `https://<api-public-domain>` | `http://127.0.0.1:8000` | FastAPI API |

The external proxy owns TLS and should forward the original `Host`, scheme, and client headers as
appropriate for its platform. These services speak HTTP, so use HTTP routing rather than a TCP
route; the proxy must preserve application-layer host and origin behavior.

The Compose bindings publish ports 3000 and 8000 on all host interfaces. The `127.0.0.1` targets in
the table apply only when the reverse proxy runs on the same host; if it runs on another NetBird
peer, target the deployment peer's reachable address instead. Do not rely on Docker publishing to
restrict access: enforce host-firewall rules and, for a remote proxy, NetBird ACLs that allow only
the intended proxy peer(s) to reach ports 3000 and 8000. Do not expose these ports directly to the
public Internet.

MinIO port 9000 must also be reachable from the browser when `MINIO_PUBLIC_ENDPOINT` points
directly to MinIO. Restrict that access to the expected application users or place MinIO behind an
appropriate authenticated HTTPS route; do not expose the MinIO console or API broadly.

Do not expose port 8001 for `worker-gpu` or port 8003 for the vision worker. The backend reaches
both by their internal Compose service names.

## Health checks and logs

The images expose these useful liveness endpoints:

- Web: `GET /` on port 3000.
- API: `GET /` on port 8000. The API also exposes
  `GET /api/v1/health/transcription`, which reports the configured health route rather than full
  PostgreSQL, MinIO, GPU, or OpenAI readiness.
- Vision worker: `GET /health` on its internal port 8003.
- GPU worker: its internal service endpoint on port 8001.

Use `docker compose ps` to check container state and follow focused logs with:

```bash
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs -f api worker beat
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs -f worker-gpu zabt-vision-worker web
```

If a service exits, inspect its logs for missing required variables, PostgreSQL migrations, MinIO
bucket/webhook setup, NVIDIA runtime/model access, local diarization licensing, or the explicit
OpenAI vision allowlist. These checks validate container behavior only; they do not prove that
external DNS, firewall, NetBird ACL, OpenAI account, or GPU capacity is ready.

## Rollback and data safety

For an image-based rollback, set all five `ZABT_*_IMAGE` variables to the previous published
immutable version, pull those images, and run `docker compose ... up -d` without `--build`. Keep
database and MinIO backups aligned with the application version before applying migrations.

`docker compose down` stops the stack and keeps named volumes. **`docker compose down -v` is
destructive: it deletes named volumes and can delete local PostgreSQL, MinIO, Redis, media, and
model-cache data. Do not use `down -v` for an ordinary restart, profile switch, or rollback.**
