# Advanced: cloud / RunPod split deployment

The single-machine `cloud-prod` profile is the primary production path. This **advanced split
topology** suits teams that want elastic GPU capacity without keeping a GPU server running 24/7:

> **ACT-TLC-12:** This deployment has no local GPU, so use the `cloud` profile (plus `kong` when
> using the bundled TLS gateway), not `cloud-prod`, which starts a local GPU worker.

- A small, cheap **API VPS** runs the `api-cloud`, `worker-cloud`, and `beat-cloud` services (no GPU).
- **Transcription** runs on **RunPod Serverless** GPUs, invoked per job.
- **Postgres** and **object storage** are managed services (e.g. Supabase Postgres + S3/R2).
- **Kong** fronts the API for TLS + rate limiting (optional if you terminate TLS elsewhere).

```mermaid
flowchart LR
    subgraph VPS [Small API VPS]
      K[Kong :80/:443] --> API[FastAPI]
      API --> WK[Celery worker]
      RD[(Redis)]
    end
    WK -->|presigned audio URL| RP[RunPod Serverless GPU]
    RP -->|transcript| WK
    API --> PG[(Managed Postgres)]
    API --> S3[(S3 / R2)]
    WEB[Web UI on Vercel] --> K
```

## 1. Provision external services

- **Postgres:** a managed Postgres (Supabase, Neon, RDS…). Get its asyncpg URL.
- **Object storage:** an S3-compatible bucket (AWS S3, Cloudflare R2, MinIO elsewhere).
- **RunPod:** deploy the GPU worker as a Serverless endpoint (see `zabt-gpu-worker/` and its
  handler). Note the endpoint ID and an API key.
- **Supabase:** auth project (as in the single-machine guide).

## 2. Configure `.env`

```dotenv
COMPOSE_PROFILES=cloud,kong               # add `kong` when using the bundled TLS gateway
API_HOST_BIND=127.0.0.1                   # keep the API host port local to the gateway
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=zabt-media
S3_PUBLIC_URL=https://media.your-domain
S3_REGION=auto
TRANSCRIPTION_BACKEND=runpod
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...
APP_URL=https://app.your-domain
NEXT_PUBLIC_API_URL=https://api.your-domain/api/v1
MINIO_PUBLIC_ENDPOINT=https://api.your-domain
```

For the `cloud` profile, `TRANSCRIPTION_BACKEND` is the root `.env` selector: Compose maps that same
value to both `TRANSCRIPTION_PROVIDER` and the legacy `TRANSCRIPTION_BACKEND` setting, defaulting to
`runpod` when the selector is unset. Local profiles continue to use `TRANSCRIPTION_PROVIDER` and
default to `gpu-local`.

With `STORAGE_PROVIDER=s3` there is no MinIO webhook; the frontend calls
`POST /api/v1/meetings/{id}/confirm-upload` after uploading to trigger the pipeline.

## 3. Kong (TLS gateway)

The `kong` profile in `docker-compose.yml` enables a Kong service that reads `kong/kong.yml` and TLS
certificates from `kong/ssl/`:

```bash
mkdir -p kong/ssl
# place origin.pem and origin.key (e.g. a Cloudflare Origin Certificate) in kong/ssl/
```

If TLS is terminated elsewhere (Cloudflare Tunnel, an external load balancer, or another reverse
proxy), set `COMPOSE_PROFILES=cloud` to omit Kong. Keep `API_HOST_BIND=127.0.0.1` when the proxy is
on the same host; if it runs on another host, bind the API port only to an address reachable by that
proxy and restrict it with the host firewall.

## 4. Launch

```bash
docker compose up -d
```

This runs `redis`, `api-cloud`, `worker-cloud`, `beat-cloud`, and `kong` — no local GPU, DB, or
storage. The API service retains the internal network alias `api` for Kong routing.

## 5. Frontend

In this topology the Next.js UI is usually hosted separately (e.g. Vercel) pointing
`NEXT_PUBLIC_API_URL` at your API domain. The `local` profile also starts bundled local dependencies;
use the local-stack instructions if you need the containerized web UI.

## Notes

- Keep the API VPS firewalled to 80/443 only; Postgres/Redis should not be publicly reachable.
- RunPod cold starts add latency to the first job after idle; tune endpoint min-workers to
  trade cost for latency.
- Rotate `RUNPOD_API_KEY`, storage keys, and `AUTH_JWT_SECRET` on any suspected exposure.
