# Cloud production Compose deployment

> **Topology note:** This guide documents the single-machine `cloud-prod` stack with local stateful
> services and model workers. For the managed-dependency topology with no local infrastructure or
> model workers, use the `full-cloud` profile and [`full-cloud-deployment.md`](./full-cloud-deployment.md).
> For the source-free, no-GPU GHCR deployment using local services and Actsis transcription, see
> [`actsis-cloud-production.md`](./actsis-cloud-production.md).

The `cloud-prod` profile in `docker-compose.yml` runs a production image stack on one machine. The
API, backend worker, web app, GPU worker, MinIO server, `mc` initializer, and vision worker use
Zabt-owned GHCR release images; PostgreSQL and Redis remain local services. Summaries and visual
analysis use first-party OpenAI cloud inference. NetBird, Caddy, or another external reverse proxy
terminates TLS and forwards HTTP to the published web and API ports.

> **Web image compatibility gate:** Do not deploy the currently published `v1.0.0` web image with
> this configuration; it predates the `/api/runtime-config` route. A future release containing that
> route in its web image must be built and published before the first cloud deployment. Confirm that
> release is available before setting `ZABT_IMAGE_TAG`; no release version is assumed here.

## Quick path

Run all commands below from the repository root, the directory containing `docker-compose.yml`.

1. Use Docker Compose v2.24.4 or newer, install the NVIDIA driver/container toolkit for local
   transcription, and confirm GHCR pull access.
2. Confirm a published release contains the `/api/runtime-config` route, then copy `.env.example` to
   the root `.env` if needed. Set `COMPOSE_PROFILES=cloud-prod`, `ZABT_IMAGE_TAG` to that compatible
   immutable release, and the deployment values listed below. Keep real credentials in `.env` or a
   secret-management layer; never commit that file.
3. Pull the pinned images and start the stack. Production services have no local `build:` fallback:

   ```sh
   docker compose pull
   docker compose up -d --no-build
   ```

4. Inspect the service state and focused logs before routing traffic through the reverse proxy:

   ```bash
   docker compose ps
   docker compose logs --tail=200 api-cloud-prod worker-cloud-prod beat-cloud-prod web-cloud-prod worker-gpu-cloud-prod zabt-vision-worker-cloud-prod
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
| `api-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-api:${ZABT_IMAGE_TAG}` | FastAPI API, local MinIO client, OpenAI summary settings | `8000` |
| `worker-cloud-prod` / `beat-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-worker:${ZABT_IMAGE_TAG}` | Celery processing and scheduled tasks | none |
| `web-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-web:${ZABT_IMAGE_TAG}` | Next.js production web app | `3000` |
| `cloud-prod-env-check` | `alpine:3.22` | Checks required variable presence using boolean flags only | none |
| `redis` | Base Compose image | Local broker | none |
| `db-cloud-prod` | `postgres:16-alpine` | Local PostgreSQL with `postgres_data` volume | `5433` |
| `minio-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-minio:${ZABT_IMAGE_TAG}` | Zabt-built MinIO object storage server | `9000`, `9001` |
| `minio-init-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-mc:${ZABT_IMAGE_TAG}` | Creates the bucket and configures its webhook event | none |
| `worker-gpu-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-gpu-worker:${ZABT_IMAGE_TAG}` | Local WhisperX/diarization transcription over `worker-gpu:8001` | **none** |
| `zabt-vision-worker-cloud-prod` | `ghcr.io/danielrondongarcia/zabt-ai-vision-worker:${ZABT_IMAGE_TAG}` | Internal visual pipeline with OpenAI inference | **none** |

The `cloud-prod` profile activates the production-suffixed services in the table. Redis remains
always-on. The production services expose internal network aliases `api`, `db`, `minio`,
`worker-gpu`, and `zabt-vision-worker`, so application URLs keep their established service names.
`worker-bot` is not activated because its separate `bot` profile is not selected. Named volumes are
shared with the local profile:

- `DATABASE_URL` points to `db:5432`.
- `REDIS_URL` is fixed to `redis://redis:6379/0`.
- Backend storage uses `STORAGE_PROVIDER=minio` and `MINIO_ENDPOINT=minio:9000`.
- Transcription uses `TRANSCRIPTION_BACKEND=gpu-local` and
  `GPU_SERVICE_URL=http://worker-gpu:8001`.
- The API calls the vision worker at `http://zabt-vision-worker:8003`.
- The vision worker uses MinIO's S3-compatible API at `http://minio:9000`; this is internal local
  networking, not a managed object-storage prerequisite.

## Required launch setting

Set `COMPOSE_PROFILES=cloud-prod` in the root `.env` for every Compose command in this guide. The
profile is loaded with the rest of the deployment configuration; it is not a secret.

## Required deployment variables

The production profile requires `ZABT_IMAGE_TAG`, `MCP_PUBLIC_URL`, `MINIO_WEBHOOK_SECRET`, the
database and authentication settings, MinIO and OpenAI credentials, and the public web URLs. Set
them in the root `.env`; Compose passes each value only to the services that need it. Use only a
published immutable release whose web image contains `/api/runtime-config`; do not use the currently
published `v1.0.0` web image. If `ZABT_IMAGE_TAG` is unset, Compose resolves a deliberately
non-release tag and the pull fails rather than falling back to a build or `latest`.
The `cloud-prod-env-check` service also blocks dependent production services when required values
are absent; it receives only presence flags and reports missing variable names, never secret values.

```dotenv
# Select only the production services from docker-compose.yml.
COMPOSE_PROFILES=cloud-prod

# One immutable release tag selects all production application images.
ZABT_IMAGE_TAG=<published-compatible-release-tag>

# Local PostgreSQL in the Compose network
POSTGRES_USER=<postgres-user>
POSTGRES_PASSWORD=<random-postgres-password>
POSTGRES_DB=<postgres-db>
DATABASE_URL=postgresql+asyncpg://<postgres-user>:<postgres-password>@db:5432/<postgres-db>

# First-party local authentication and public origins
AUTH_JWT_SECRET=<random-secret-at-least-32-bytes>
AUTH_ALLOWED_ORIGINS=https://<web-public-domain>
BACKEND_CORS_ORIGINS=https://<web-public-domain>
APP_URL=https://<web-public-domain>
MCP_PUBLIC_URL=https://<api-public-domain>/api/v1/mcp
NEXT_PUBLIC_API_URL=https://<api-public-domain>/api/v1
NEXT_PUBLIC_FRONTEND_URL=https://<web-public-domain>

# Local MinIO. MINIO_ENDPOINT is fixed to minio:9000 inside the production network.
MINIO_ACCESS_KEY=<minio-access-key>
MINIO_SECRET_KEY=<minio-secret-key>
MINIO_BUCKET_NAME=zabt-ai-bucket
MINIO_WEBHOOK_SECRET=<random-webhook-secret>
MINIO_PUBLIC_ENDPOINT=http://<browser-reachable-deployment-host>:9000

# Separate first-party OpenAI credentials for summaries and visual analysis
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=<openai-summary-key>
VISION_JUDGE_MODEL=gpt-4o-mini
VISION_OPENAI_BASE_URL=https://api.openai.com/v1
VISION_OPENAI_MODEL=gpt-4o-mini
VISION_ALLOWED_HOSTS=api.openai.com
VISION_OPENAI_API_KEY=<openai-vision-key>

# Set this only when the selected local diarization model requires Hugging Face access.
HF_TOKEN=<hugging-face-token-if-required>
```

`MINIO_PUBLIC_ENDPOINT` defaults to `http://localhost:9000`, which is suitable only when the
browser is on the deployment host. Set it to the deployment host's browser-reachable address (or a
TLS reverse-proxy URL) when users access the web app from another machine. The MinIO access key,
secret, and bucket must match `minio-cloud-prod` and `minio-init-cloud-prod`.

The production web image reads `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_FRONTEND_URL` from the root
`.env` through runtime-only web settings and a dynamic `/api/runtime-config` route. The browser loads
this public-only response before its first API request. After a compatible web image containing the
route has been deployed, these browser-safe URLs can be changed in the root `.env` without rebuilding
or republishing the web image. The currently published `v1.0.0` web image lacks the route and cannot
use this runtime configuration. If either URL is missing, the route fails rather than falling back
to a build-time localhost address. The web service receives only these public URLs and public
telemetry settings; backend, database, OpenAI, and MinIO credentials remain scoped to their
respective services.

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
| `MINIO_WEBHOOK_SECRET` | `change-me-in-production` | Local default only; cloud-prod requires an explicit secret. |
| `MINIO_SECURE` | `false` | Internal MinIO client scheme. |
| `AUTH_JWT_ISSUER` | `zabt-api` | JWT issuer identifier. |
| `AUTH_JWT_AUDIENCE` | `zabt-client` | JWT audience identifier. |
| `AUTH_COOKIE_SAMESITE` | `lax` | Browser cookie SameSite policy. |
| `VISION_LOCAL_URL` | `http://zabt-vision-worker:8003` | Internal API-to-vision-worker URL. |
| `VISION_SIGNED_URL_EXPIRATION` | `3600` | Lifetime of visual-media URLs in seconds. |

The backend rejects the shared local `change-me-in-production` placeholder for
`MINIO_WEBHOOK_SECRET` in production, including case and surrounding-whitespace variations. Set a
deployment-specific randomly generated secret; the backend will fail validation at startup if the
secret is empty or still uses the shared placeholder.

The production services set `AUTH_ENVIRONMENT=production`, `AUTH_COOKIE_SECURE=true`,
`STORAGE_PROVIDER=minio`, `MINIO_ENDPOINT=minio:9000`, `TRANSCRIPTION_BACKEND=gpu-local`,
`GPU_SERVICE_URL=http://worker-gpu:8001`, and `REDIS_URL=redis://redis:6379/0`.

## OpenAI inference configuration

Summary inference defaults to the first-party endpoint and model; both values can be set in `.env`:

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

The release workflow publishes the Zabt-owned MinIO server and client alongside the application
images. Cloud production selects every image with one immutable `ZABT_IMAGE_TAG`:

```text
ghcr.io/danielrondongarcia/zabt-ai-api:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-worker:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-web:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-gpu-worker:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-minio:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-mc:${ZABT_IMAGE_TAG}
ghcr.io/danielrondongarcia/zabt-ai-vision-worker:${ZABT_IMAGE_TAG}
```

The MinIO community container images are no longer publicly pullable. Zabt builds both images from
pinned upstream source tags without using a MinIO Docker Hub or Quay base image:

| Image | Pinned upstream source | Build toolchain |
|---|---|---|
| `zabt-ai-minio` | `minio/minio` `RELEASE.2025-10-15T17-29-55Z` (`9e49d5e7a648`) (archived server source) | Go `1.24.8`; Alpine `3.22.2` runtime |
| `zabt-ai-mc` | `minio/mc` `RELEASE.2025-08-13T08-35-41Z` (`7394ce0dd2a8`) | Go `1.23.10`; Alpine `3.21.4` runtime |

The release tag selects the Zabt image version; it does not change the upstream source pin. Zabt owns
future image security updates: maintainers must monitor upstream advisories and available fixes,
update the source pins and rebuild/publish the images. The server source repository is archived, so
security maintenance may require a Zabt-managed patch or a replacement source if upstream fixes are
unavailable. The images retain upstream licenses and use pinned Alpine runtime bases with CA
certificates.

Set `ZABT_IMAGE_TAG` in the root `.env` to a published immutable `vMAJOR.MINOR.PATCH` release whose
web image contains `/api/runtime-config`; never use `latest` or the currently published `v1.0.0` web
image for this topology. Compose does not validate the tag format, so confirm compatibility and
availability before deployment. The Docker workflow validates the tag format.

For a cloud-prod release, dispatch **Release** from `main` after the runtime-config change is merged
and set `include_optional=true`; MinIO and `mc` are in the seven-image core matrix, while the vision
image is optional in the workflow but required by this topology. Do not deploy until the release
completes and its web image includes `/api/runtime-config`. Make the GHCR packages public for
anonymous pulls, or authenticate the host to `ghcr.io` with a credential that has `read:packages`. Run
`docker compose pull` before `docker compose up -d --no-build`; every GHCR-backed production service
uses `pull_policy: always` and has no `build:` definition. Restore the previous tag in
`ZABT_IMAGE_TAG` to roll back.

## Switching from the local profile

The local and production profiles use separate service names but share named data volumes. Before
switching profiles, stop the local stack with `docker compose --profile local down`; this preserves
named volumes. Then set `COMPOSE_PROFILES=cloud-prod` in `.env` and start the production profile.
Back up important data before changing application versions or migrations.

Never use `docker compose down -v` to force a profile transition. The `-v` flag deletes named
volumes, including PostgreSQL, MinIO, Redis, media, and model-cache data.

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
docker compose logs -f api-cloud-prod worker-cloud-prod beat-cloud-prod
docker compose logs -f worker-gpu-cloud-prod zabt-vision-worker-cloud-prod web-cloud-prod
```

If a service exits, inspect its logs for missing required variables, PostgreSQL migrations, MinIO
bucket/webhook setup, NVIDIA runtime/model access, local diarization licensing, or the explicit
OpenAI vision allowlist. These checks validate container behavior only; they do not prove that
external DNS, firewall, NetBird ACL, OpenAI account, or GPU capacity is ready.

## Rollback and data safety

For an image-based rollback, set `ZABT_IMAGE_TAG` to a previous published immutable release whose
web image also contains `/api/runtime-config`. Do not roll this topology back to the currently
published `v1.0.0` web image. From the repository root, set `ZABT_IMAGE_TAG` in `.env`, then pull the
pinned images and recreate the services without building locally:

```bash
docker compose pull
docker compose up -d --no-build
```

Keep database and MinIO backups aligned with the application version before applying migrations.

`docker compose down` stops the stack and keeps named volumes. **`docker compose down -v` is
destructive: it deletes named volumes and can delete local PostgreSQL, MinIO, Redis, media, and
model-cache data. Do not use `down -v` for an ordinary restart, profile switch, or rollback.**
