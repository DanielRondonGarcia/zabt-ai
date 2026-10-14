# Cloud Actsis production deployment

The `cloud-actsis` profile in the root `docker-compose.yml` runs Zabt application images from GHCR
and local PostgreSQL, Redis, MinIO, and Qdrant. Transcription uses the Actsis OpenAI-compatible
endpoint with cloud diarization. No source checkout, local GPU, RunPod, Kong, vision worker, or bot
worker is required on the deployment host.

## Release compatibility gate

The web image must contain the `/api/runtime-config` route so public API and frontend URLs can be
provided at container runtime. The currently published `v1.0.0` web image predates that route and is
not compatible. Use a later, published immutable `vMAJOR.MINOR.PATCH` release only after confirming
that its web image contains `/api/runtime-config` and its API, worker, web, MinIO, and `mc` images
are all available under the same tag. Do not use `latest`.

## Host files and prerequisites

The deployment directory needs only:

- `docker-compose.yml` from the repository root.
- A root `.env` containing the deployment values below.

No application source, Dockerfiles, local build context, `env_file`, or bind-mounted init script is
used by this profile. Install Docker Engine with Compose v2.24.4 or newer, ensure the host can pull
the GHCR packages (public access or credentials with `read:packages`), and configure the host's
reverse proxy and firewall before inviting users.

## Root `.env`

Set the Compose profile and a compatible immutable release tag:

```dotenv
COMPOSE_PROFILES=cloud-actsis
ZABT_IMAGE_TAG=<later-compatible-immutable-release>
```

Set local PostgreSQL credentials and the URL used by the API and workers:

```dotenv
POSTGRES_USER=<postgres-user>
POSTGRES_PASSWORD=<random-postgres-password>
POSTGRES_DB=<database-name>
DATABASE_URL=postgresql+asyncpg://<postgres-user>:<postgres-password>@db:5432/<database-name>
```

Configure authentication and public application origins:

```dotenv
AUTH_JWT_SECRET=<random-secret-at-least-32-bytes>
AUTH_ALLOWED_ORIGINS=https://<web-public-host>
BACKEND_CORS_ORIGINS=https://<web-public-host>
APP_URL=https://<web-public-host>
MCP_PUBLIC_URL=https://<api-public-host>/api/v1/mcp
NEXT_PUBLIC_API_URL=https://<api-public-host>/api/v1
NEXT_PUBLIC_FRONTEND_URL=https://<web-public-host>
```

Set MinIO credentials, a deployment-specific webhook secret, and the browser-facing HTTPS URL:

```dotenv
MINIO_ACCESS_KEY=<minio-access-key>
MINIO_SECRET_KEY=<random-minio-secret>
MINIO_BUCKET_NAME=zabt-ai-bucket
MINIO_WEBHOOK_SECRET=<random-webhook-secret>
MINIO_PUBLIC_ENDPOINT=https://<browser-reachable-minio-host>
```

`MINIO_ENDPOINT` is fixed to `minio:9000` for API-to-MinIO traffic inside the Compose network.
`MINIO_PUBLIC_ENDPOINT` is used for presigned URLs returned to browsers; route its HTTPS hostname
through a TLS-terminating proxy to the MinIO API host port. A URL that merely resolves on the
deployment host is not sufficient: test it from a client browser network.

Configure the Actsis key in this same root `.env`:

```dotenv
ACTSIS_API_KEY=<actsis-api-key>
TRANSCRIPTION_BASE_URL=https://ai.actsis.internal/v1
```

`TRANSCRIPTION_BASE_URL` defaults to `https://ai.actsis.internal/v1` when omitted. The profile pins
both `TRANSCRIPTION_PROVIDER` and `TRANSCRIPTION_BACKEND` to `openai-file`, selects
`whisper-diarize`, enables cloud diarization, requests `diarized_json`, and requires speakers. The
Actsis key is passed only to API, worker, and beat containers. `OPENAI_API_KEY` and `AI_CHAT_API_KEY`
remain separate optional credentials for summary and chat providers; neither is sent to the web
container. Embedding settings default to the Actsis base URL and `nomic-embed`; set
`EMBEDDING_API_KEY` separately only if the deployment's embedding configuration needs it (otherwise
the Actsis key is the fallback).

The one-shot Compose preflight receives only boolean presence flags. It reports missing variable
names, never their values. It requires `MINIO_PUBLIC_ENDPOINT` to be present; the API startup guard
also rejects a value that does not start with `https://` without printing the URL. Browser
reachability itself must be verified through the deployment's proxy/network path.

Do not attach the complete `.env` to any service with `env_file`. The web container receives only
`ZABT_PUBLIC_API_URL`, `ZABT_PUBLIC_FRONTEND_URL`, and public `NEXT_PUBLIC_*` telemetry settings.

## NetBird reverse proxy setup for ACT-TWC-11

Configure NetBird Reverse Proxy routes for the app hostname `zabt.proxy.actsis.com` to the ACT-TWC-11
host:

| HTTP path | Target on ACT-TWC-11 |
|---|---|
| `/api/` | API host port `8000` |
| `/.well-known/` | API host port `8000` (MCP OAuth discovery) |
| `/` | Frontend host port `3000` |

Enable **Preserve Full Path** on all three routes. NetBird strips path prefixes by default; preserving
the full path is required for the API and OAuth discovery routes to reach their expected endpoints.
These are setup instructions only; they do not mean the proxy is configured.

For browser presigned uploads, configure a separate HTTPS MinIO hostname and proxy it to ACT-TWC-11
host port `9000`. Set `MINIO_PUBLIC_ENDPOINT` to that HTTPS URL. Do not put MinIO behind a path prefix
on the app hostname: S3 request signatures are sensitive to the request host and path.

## Host ports

The default host ports are API `8000`, web `3000`, PostgreSQL `5433`, MinIO API `9000`, MinIO console
`9001`, and Qdrant `6333`. Override them in root `.env` with `API_PORT`, `WEB_PORT`,
`POSTGRES_HOST_PORT`, `MINIO_HOST_PORT`, `MINIO_CONSOLE_HOST_PORT`, and `QDRANT_HOST_PORT`. The
PostgreSQL, Qdrant, and MinIO console bindings default to loopback; API, web, and MinIO API are
published on all interfaces by default. Restrict these with host firewall rules and configure
`API_HOST_BIND`, `WEB_HOST_BIND`, `MINIO_HOST_BIND`, `POSTGRES_HOST_BIND`,
`MINIO_CONSOLE_HOST_BIND`, or `QDRANT_HOST_BIND` where needed. Redis is internal-only.

## Pull and start

Run these commands in the deployment directory containing the root Compose file and `.env`:

```sh
docker compose --profile cloud-actsis pull
docker compose --profile cloud-actsis up -d --no-build
docker compose --profile cloud-actsis ps
```

For logs, use the actual profile service names:

```sh
docker compose --profile cloud-actsis logs --tail=200 api-cloud-actsis worker-cloud-actsis beat-cloud-actsis web-cloud-actsis
```

Do not use `docker compose down -v` for restart, rollback, or maintenance; that removes persistent
database, Redis, MinIO, Qdrant, and media volumes.
