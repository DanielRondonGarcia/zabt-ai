# Cloud production Compose deployment

`docker-compose.cloud.prod.yml` runs the production-shaped stack on a cloud peer: Next.js uses
`next build`/`next start`, transcription uses RunPod, summaries and visual analysis use OpenAI,
and PostgreSQL plus S3-compatible object storage are managed services. NetBird, Caddy, or another
external reverse proxy terminates TLS and forwards HTTP to the published web and API ports.

## Quick path

1. Prepare the managed services and deployment variables described below. Keep the real values in
   the deployment host's `.env` or secret-management layer; never commit that file.
2. Set `COMPOSE_PROFILES=cloud-prod` in the process environment. This process-level value must
   override a `COMPOSE_PROFILES=local` entry that may still exist in `.env`.
3. Pull and start the pinned production images. This release path intentionally does not use
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

4. Inspect the services and logs before sending traffic through the reverse proxy:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml ps
   docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs --tail=200 api worker beat web zabt-vision-worker
   ```

   On PowerShell, `Invoke-WebRequest http://127.0.0.1:3000/` checks the web listener and
   `Invoke-WebRequest http://127.0.0.1:8000/` checks the API listener. The vision worker is
   internal, so check it from the Compose network with `docker compose ... exec` if needed.

The override deliberately leaves the base `redis` service always-on. Set `REDIS_URL` to a managed
broker when desired; the local Redis container will then be started but unused unless another
service still points at it.

## Prerequisites

- **Managed PostgreSQL** reachable from the Compose host, with a connection URL compatible with
  the backend's async SQLAlchemy driver (`postgresql+asyncpg://...`). The API runs migrations at
  startup.
- **S3-compatible object storage** with a bucket, credentials, and an endpoint that the Compose
  host can reach. The public endpoint used to create presigned URLs must also be reachable by the
  browser and by the RunPod worker.
- **RunPod Serverless endpoint** configured for the transcription contract, plus its API key and
  endpoint ID.
- **Two OpenAI credentials or an intentional key-sharing policy**: `OPENAI_API_KEY` is the
  summary credential and `VISION_OPENAI_API_KEY` is the explicit visual-analysis credential. The
  latter is never inherited from an OpenRouter-compatible summary key.
- **Strong local-auth secret** shared by `api`, `worker`, and `beat`. It must be at least 32 bytes,
  have sufficient character diversity, and not be a placeholder.
- **Redis choice**: use the bundled Redis broker by leaving `REDIS_URL` unset, or provide a
  managed Redis URL (including `rediss://` when TLS is required).
- **External reverse proxy/TLS**: NetBird Reverse Proxy, Caddy, nginx, or an equivalent HTTP
  proxy must provide the public DNS names, TLS certificates, and forwarding rules. This Compose
  file does not provision certificates, Kong, or a TCP proxy.

## Required launch setting

Set `COMPOSE_PROFILES=cloud-prod` for every Compose command in this guide. This process-level
setting selects the cloud-production profiles and overrides a conflicting `COMPOSE_PROFILES=local`
value in `.env`; it is not a secret and is not an interpolated deployment variable.

## Required deployment variables

The override uses fail-closed `${VAR:?message}` checks for the variables below. The values are
placeholders only, not credentials. Each image variable must point to a published, immutable
GHCR release tag such as `v1.2.3`; the override has no mutable `:latest` fallback.

```dotenv
ZABT_API_IMAGE=ghcr.io/<owner>/zabt-ai-api:vX.Y.Z
ZABT_WORKER_IMAGE=ghcr.io/<owner>/zabt-ai-worker:vX.Y.Z
ZABT_WEB_IMAGE=ghcr.io/<owner>/zabt-ai-web:vX.Y.Z
ZABT_VISION_IMAGE=ghcr.io/<owner>/zabt-ai-vision-worker:vX.Y.Z

DATABASE_URL=postgresql+asyncpg://<db-user>:<db-password>@<managed-db-host>:5432/<db-name>
AUTH_JWT_SECRET=<random-secret-at-least-32-bytes>
AUTH_ALLOWED_ORIGINS=https://<web-public-domain>
BACKEND_CORS_ORIGINS=https://<web-public-domain>
APP_URL=https://<web-public-domain>

S3_ENDPOINT_URL=https://<s3-endpoint>
S3_ACCESS_KEY_ID=<s3-access-key>
S3_SECRET_ACCESS_KEY=<s3-secret-key>
S3_BUCKET_NAME=<s3-bucket-name>
S3_PUBLIC_URL=https://<browser-and-runpod-reachable-s3-endpoint>

RUNPOD_API_KEY=<runpod-api-key>
RUNPOD_ENDPOINT_ID=<runpod-endpoint-id>

OPENAI_API_KEY=<openai-summary-key>
VISION_OPENAI_API_KEY=<openai-vision-key>

NEXT_PUBLIC_API_URL=https://<api-public-domain>/api/v1
NEXT_PUBLIC_FRONTEND_URL=https://<web-public-domain>
```

`ZABT_VISION_IMAGE` is required because the vision worker is active in this topology. The release
workflow publishes that image only when its `include_optional` input is enabled. The two public
URL variables are required because they are compiled into the browser bundle as well as passed to
the running web container.

### Defaults and fixed production configuration

These settings are not required entries in `.env`:

- The override fixes `AUTH_ENVIRONMENT=production`, `AUTH_COOKIE_SECURE=true`,
  `STORAGE_PROVIDER=s3`, `TRANSCRIPTION_BACKEND=runpod`, `GPU_SERVICE_URL` to empty,
  and `SENTRY_ENVIRONMENT=production`.
- Summary inference is fixed to `OPENAI_BASE_URL=https://api.openai.com/v1` and
  `OPENAI_MODEL=gpt-4o-mini`.
- Vision is fixed to `VISION_ENABLED=true`, `VISION_BACKEND=local`,
  `VISION_INFERENCE_BACKEND=openai`, `VISION_CLOUD_ALLOWED=true`,
  `VISION_EGRESS_POLICY=allowlist`, `VISION_ALLOWED_HOSTS=api.openai.com`,
  `VISION_JUDGE_MODEL=gpt-4o-mini`, `VISION_REQUIRE_VISION=true`,
  `VISION_OPENAI_BASE_URL=https://api.openai.com/v1`, `VISION_OPENAI_MODEL=gpt-4o-mini`,
  `VISION_OPENAI_IMAGE_DETAIL=low`, and `VISION_OPENAI_MAX_TOKENS=1024`.

The following values have safe defaults and can be overridden when the deployment needs different
configuration:

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Use the bundled Redis or set a managed Redis URL. |
| `S3_REGION` | `auto` | Region value for the managed S3-compatible service. |
| `AUTH_JWT_ISSUER` | `zabt-api` | JWT issuer identifier. |
| `AUTH_JWT_AUDIENCE` | `zabt-client` | JWT audience identifier. |
| `AUTH_COOKIE_SAMESITE` | `lax` | Browser cookie SameSite policy. |
| `RUNPOD_POLL_INTERVAL` | `5` | RunPod polling interval in seconds. |
| `RUNPOD_TIMEOUT` | `1800` | RunPod timeout in seconds. |
| `VISION_LOCAL_URL` | `http://zabt-vision-worker:8003` | Internal API-to-vision-worker URL. |
| `VISION_SIGNED_URL_EXPIRATION` | `3600` | Presigned media URL lifetime in seconds. |

Optional observability values include `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`,
`NEXT_PUBLIC_POSTHOG_KEY`, and `NEXT_PUBLIC_POSTHOG_HOST` (which defaults to
`https://us.i.posthog.com`).

## GHCR image pinning

The release workflow publishes versioned images with the following names:

```text
ghcr.io/<owner>/zabt-ai-api:vX.Y.Z
ghcr.io/<owner>/zabt-ai-worker:vX.Y.Z
ghcr.io/<owner>/zabt-ai-web:vX.Y.Z
ghcr.io/<owner>/zabt-ai-vision-worker:vX.Y.Z
```

The vision image is published when the release workflow's `include_optional` input is enabled.
The same workflow also publishes `:latest`, but the cloud-production override intentionally
rejects missing image variables and documents only immutable `vX.Y.Z` tags. Set the four
`ZABT_*_IMAGE` variables from the required-variable section to the same release version before
starting the stack. The pull/up flow below uses the published images and does not rebuild them:

```powershell
$env:ZABT_API_IMAGE="ghcr.io/<owner>/zabt-ai-api:vX.Y.Z"
$env:ZABT_WORKER_IMAGE="ghcr.io/<owner>/zabt-ai-worker:vX.Y.Z"
$env:ZABT_WEB_IMAGE="ghcr.io/<owner>/zabt-ai-web:vX.Y.Z"
$env:ZABT_VISION_IMAGE="ghcr.io/<owner>/zabt-ai-vision-worker:vX.Y.Z"
$env:COMPOSE_PROFILES="cloud-prod"
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml up -d
```

Use the equivalent `export` assignments on POSIX systems. Do not pass `--build` when intentionally
running the already-published pinned images; otherwise Compose can rebuild from the local source
instead of running the release artifact.

## NetBird HTTP routing

Create two HTTP reverse-proxy routes on the NetBird peer or its adjacent proxy:

| Public route | Peer target | Purpose |
|---|---|---|
| `https://<web-public-domain>` | `http://127.0.0.1:3000` | Next.js production web server |
| `https://<api-public-domain>` | `http://127.0.0.1:8000` | FastAPI API |

The external proxy owns TLS and should forward the original `Host`, scheme, and client headers as
appropriate for its platform. The API remains bound to `8000:8000` and the web service to
`3000:3000` so the proxy can target them as separate HTTP services. Use HTTP routing, not TCP:
these services speak HTTP, the proxy must terminate TLS and preserve HTTP host/origin behavior,
and TCP mode would bypass the application-layer routing this topology requires.

> **Firewall and NetBird ACL warning:** These host bindings intentionally remain unchanged and
> publish on all host interfaces; this guide does not claim that a loopback-only binding has been
> verified. The `127.0.0.1` targets in the table apply only when the reverse proxy runs on the
> same host. If the proxy runs on another NetBird peer, target the deployment peer's reachable
> address instead. In both cases, do not rely on Docker publishing to restrict access: enforce
> host-firewall rules and, for a remote proxy, NetBird ACLs that allow only the intended proxy
> peer(s) to reach ports 3000 and 8000. Do not expose these ports directly to the public Internet.

Do not expose port `8003` for the vision worker. The API calls it by the internal Compose service
name `zabt-vision-worker:8003`; its OpenAI and S3 egress are not public listener routes.

## OpenAI privacy and cost

The summary path sends transcript/context text to the configured OpenAI-compatible endpoint.
Visual processing sends selected JPEG keyframes and the visual prompt, which may include a short
transcript excerpt, from the vision worker to OpenAI. Keep the two keys separate, review the data
processing and retention terms for the selected OpenAI account, obtain any required participant
consent, and avoid putting raw media, prompts, signed URLs, or credentials in logs.

`gpt-4o-mini` with `VISION_OPENAI_IMAGE_DETAIL=low` is the economical default. Low detail reduces
image-token usage and latency but can miss small text or fine UI changes; `auto` or `high` can
improve fidelity at higher token cost. Output is bounded by `VISION_OPENAI_MAX_TOKENS`. Pricing,
retention, and model capabilities change, so confirm the current OpenAI documentation before
setting a production budget.

## Health checks and logs

The images expose these useful liveness endpoints:

- Web: `GET /` on port 3000.
- API: `GET /` on port 8000. The API also exposes
  `GET /api/v1/health/transcription`; this reports the configured health route, not a full managed
  database/S3/RunPod readiness check.
- Vision worker: `GET /health` on its internal port 8003.

Use `docker compose ps` to check container state and follow focused logs with:

```bash
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs -f api worker beat
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml logs -f web zabt-vision-worker
```

If a service exits, inspect its logs for missing required variables, database migrations, S3
permissions, RunPod job failures, or the explicit OpenAI vision allowlist. These checks validate
container behavior only; this document does not claim that cloud resources or reverse-proxy routes
have been tested.

## Rollback and data safety

For an image-based rollback, set all `ZABT_*_IMAGE` variables to the previous published
`vX.Y.Z` tag, pull those images, and run `docker compose ... up -d` without `--build`. Keep the
database and bucket backups aligned with the application version before applying migrations.

`docker compose down` stops the stack and keeps named volumes. **`docker compose down -v` is
destructive: it deletes named volumes and can delete local Redis or any other Compose-managed
data. Do not use `down -v` for an ordinary restart or rollback.** Managed PostgreSQL and S3 data
are outside those local volumes, but their own deletion and retention policies still apply.
