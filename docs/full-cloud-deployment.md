# Full-cloud deployment

`docker-compose.full-cloud.yml` runs only Zabt's deployable application services. PostgreSQL,
Redis, S3-compatible object storage, Qdrant, Actsis, and OpenAI are external dependencies; no
local infrastructure or model worker is required by this topology.

## Quick path

1. Publish immutable images for `api`, `worker`, and `web`. The backend image must include the
   Actsis CA certificate at `/usr/local/share/ca-certificates/actsis-root-ca.crt`, as the supplied
   backend Dockerfile does.
2. Copy `docs/full-cloud.env.example` to an untracked deployment env file, replace every
   placeholder, and load the values through your deployment secret manager.
3. Validate the rendered topology without starting services:

   ```bash
   docker compose --env-file .env.full-cloud -f docker-compose.full-cloud.yml config --quiet
   ```

4. Pull and start only after the external endpoints, credentials, DNS, TLS, and bucket policy are
   ready:

   ```bash
   docker compose --env-file .env.full-cloud -f docker-compose.full-cloud.yml pull
   docker compose --env-file .env.full-cloud -f docker-compose.full-cloud.yml up -d
   ```

The Compose file is standalone. Do not add another Compose file or a local profile to this
command; doing so can reintroduce services that this deployment intentionally does not use.

## Application topology

| Service | Role | Required external access |
|---|---|---|
| `api` | FastAPI API and database migrations | Managed Postgres, Redis, S3, Qdrant, Actsis, OpenAI |
| `worker` | Celery transcription, summaries, intelligence, embeddings, retrieval chat, and visual processing | Same backend dependencies |
| `beat` | Celery scheduled tasks | Managed Postgres and Redis; provider settings are shared for task imports |
| `web` | Next.js application | Public API and frontend URLs at image-build/runtime configuration |

The backend worker processes visual media in-process with `DirectVisionService`; it sends bounded
frame data to the explicit OpenAI visual endpoint. There is no separate visual inference service,
local model pull, GPU reservation, or model-cache volume in this topology.

## Required external configuration

The complete placeholder list is in [`full-cloud.env.example`](./full-cloud.env.example). The
Compose file fails closed when these values are absent:

| Area | Variables | Notes |
|---|---|---|
| Images and public app | `ZABT_API_IMAGE`, `ZABT_WORKER_IMAGE`, `ZABT_WEB_IMAGE`, `APP_URL`, `AUTH_ALLOWED_ORIGINS`, `BACKEND_CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_FRONTEND_URL` | Use the same immutable release for all three images. |
| Database and broker | `DATABASE_URL`, `REDIS_URL` | Use managed endpoints; TLS URLs such as `rediss://` are supported by the Redis client. |
| Object storage | `STORAGE_PROVIDER=s3`, `S3_ENDPOINT_URL`, `S3_PUBLIC_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME` | The public URL must be reachable by browsers for presigned upload/download operations. |
| Actsis transcription | `ACTSIS_API_KEY`, `TRANSCRIPTION_BASE_URL` | `ACTSIS_CA_BUNDLE` is optional; Compose supplies the backend image's default CA path and accepts an override. `TRANSCRIPTION_PROVIDER` is fixed to `openai-file`; a dedicated `TRANSCRIPTION_API_KEY` may override the Actsis key. |
| Actsis embeddings | `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION` | `EMBEDDING_PROVIDER` is fixed to `openai`; a dedicated `EMBEDDING_API_KEY` may override the Actsis key. |
| Qdrant | `QDRANT_URL`, `QDRANT_API_KEY` | The backend passes the API key to the Qdrant client; keep it in secret storage. |
| OpenAI summary/chat | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `AI_CHAT_BASE_URL` | Summary and structured intelligence use `OPENAI_*`; retrieval chat can use an optional dedicated `AI_CHAT_API_KEY`. |
| OpenAI visual inference | `VISION_OPENAI_API_KEY`, `VISION_OPENAI_BASE_URL`, `VISION_ALLOWED_HOSTS` | The visual key is required separately and is never taken from `OPENAI_API_KEY`. |

Set the three OpenAI base URLs explicitly; the official HTTPS endpoint is
`https://api.openai.com/v1`. If a compatible managed endpoint is selected instead, update its
corresponding allowlist value; visual inference still requires HTTPS,
`VISION_CLOUD_ALLOWED=true`, `VISION_EGRESS_POLICY=allowlist`, and a matching
`VISION_ALLOWED_HOSTS` entry.

## Upload confirmation

External S3-compatible storage has no local object-storage webhook wired into this Compose file;
there is no MinIO webhook in the full-cloud topology.
After the browser finishes the presigned upload, the client must call
`POST /api/v1/meetings/{meeting_id}/confirm-upload`. The current web app already does this when
the API reports `storage_provider: "s3"`; custom clients must preserve the same confirmation step.
Without confirmation, the meeting remains `pending_upload` and the Celery transcription chain is
not started.

## Actsis transcription boundary

Actsis transcription in this topology is intentionally non-diarized. The Compose configuration
sets `TRANSCRIPTION_RESPONSE_FORMAT=json`, `TRANSCRIPTION_CLOUD_DIARIZATION=false`, and
`TRANSCRIPTION_SPEAKER_REQUIRED=false`. Do not configure `diarized_json` or claim speaker
diarization from the Actsis path; use a separate explicitly authorized provider topology if that
capability becomes available.

The same Actsis key isolation applies to embeddings: custom Actsis endpoints use `ACTSIS_API_KEY`
unless a dedicated embedding or transcription key is supplied. `ACTSIS_CA_BUNDLE` is optional;
when it is unset, Compose supplies `/usr/local/share/ca-certificates/actsis-root-ca.crt`, the
certificate installed by the backend image. Set `ACTSIS_CA_BUNDLE` explicitly to override that
default when a different trusted CA bundle is required for the deployment.

## Operational checklist

- [ ] Managed Postgres and Redis are reachable from the backend containers.
- [ ] S3 CORS, bucket permissions, and `S3_PUBLIC_URL` allow the browser upload/download flow.
- [ ] The Qdrant collection endpoint is HTTPS and its API key is stored outside the repository.
- [ ] Actsis DNS and its CA bundle are available in both `api` and `worker` images.
- [ ] The OpenAI summary key and the dedicated visual key are both configured.
- [ ] `VISION_ALLOWED_HOSTS` matches the host in `VISION_OPENAI_BASE_URL`.
- [ ] The first upload was confirmed and the resulting meeting entered the processing queue.
- [ ] No local model download or provider pull is part of startup; only the published application
  images are pulled.
