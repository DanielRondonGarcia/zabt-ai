# Full-cloud deployment

The `full-cloud` profile in the root `docker-compose.yml` runs only Zabt's deployable application
services. PostgreSQL, Redis, S3-compatible object storage, Qdrant, Actsis, and OpenAI are external
dependencies; no local infrastructure or model worker is required by this topology.

## Quick path

1. Publish immutable images for `api`, `worker`, and `web`. The web image must include the
   `/api/runtime-config` route; the currently published `v1.0.0` web image predates that route and
   is incompatible. The backend image must include the Actsis CA certificate at
   `/usr/local/share/ca-certificates/actsis-root-ca.crt`.
2. Copy the root `.env.example` to `.env` if needed. Set `COMPOSE_PROFILES=full-cloud` in that root
   `.env`, then use `docs/full-cloud.env.example` as a checklist for required external values.
   Compose reads only the root `.env`; a deployment secret manager may provision that file.
3. Validate the rendered topology without starting services:

   ```bash
    docker compose --profile full-cloud config --quiet
   ```

4. Pull and start only after the external endpoints, credentials, DNS, TLS, and bucket policy are
   ready:

    ```bash
    docker compose pull
    docker compose up -d
    ```

The `full-cloud` profile selects only the external-dependency application services. Do not combine it
with `local`, `cpu`, `local-actsis`, `cloud`, or `cloud-prod` profiles.

## Application topology

| Service | Role | Required external access |
|---|---|---|
| `full-cloud-env-check` | One-shot required-setting presence check; receives boolean flags only | None |
| `api-full-cloud` | FastAPI API and database migrations | Managed Postgres, Redis, S3, Qdrant, Actsis, OpenAI |
| `worker-full-cloud` | Celery transcription, summaries, intelligence, embeddings, retrieval chat, and visual processing | Same backend dependencies |
| `beat-full-cloud` | Celery scheduled tasks | Managed Postgres and Redis; provider settings are shared for task imports |
| `web-full-cloud` | Next.js application | Public API and frontend URLs at runtime |

The backend worker processes visual media in-process with `DirectVisionService`; it sends bounded
frame data to the explicit OpenAI visual endpoint. There is no separate visual inference service,
local model pull, GPU reservation, or model-cache volume in this topology.

## Required external configuration

The complete placeholder list is in [`full-cloud.env.example`](./full-cloud.env.example); copy the
required values into the root `.env`. Before starting app services, the `full-cloud-env-check`
service verifies that required values are present and reports missing variable names without
printing their values:

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

## Transcript-only intelligence

Highlight and structured-output extraction preserve short transcripts as one model request.
Longer transcripts are split into sequential, nonoverlapping windows of at most 4096 UTF-8
bytes each. Every transcript character remains in one window, including timestamps and
long words; window results are combined in order before the stage saves its output. When
persisted transcript segments are supplied to `extract_highlights`, the service renders each
segment as a compact `[start-end] [Speaker: ]text` line with real seconds from the meeting
start, ordered by start, and packed into the same
4096-byte windows. An oversized segment's text is fragmented losslessly with its original
start, end, and speaker repeated on every fragment; nothing is interpolated, truncated, or
synthesized, and only explicitly empty segment records are skipped. Segments with missing,
non-finite, or end-before-start times fail the stage before any provider request. A null or
schema-invalid response on the timed path still fails even for a single window, so a null
provider timestamp is never coerced to zero, and rejecting a timed response saves no
highlights or completion markers. Meetings without persisted segments, or with an empty
segment list, keep the transcript-only behavior described below. If
any window fails, the stage fails without saving a partial extraction. A previously
saved summary remains available. The completion client's per-request timeout stays
120 seconds; bounding requests does not guarantee that an unavailable or overloaded
provider will respond. The generic structured response accepts the observed
`resumen_ejecutivo` key from a compatible gateway as `summary` and persists the
English field name. For any transcript, a response with neither key, another
schema mismatch or no parsed result fails structured extraction rather than saving
partial or minimal output; highlights keep their separate short-input behavior.
If the shared `OPENAI_MODEL` does not
respect the typed JSON schema, set `STRUCTURED_OUTPUT_MODEL` in the deployment
`.env` to a schema-compatible model on the same `OPENAI_BASE_URL`; the API and
worker inherit it through Compose, while highlights, summary and title retain
`OPENAI_MODEL`. The Actsis gateway's `oc/glm-5.3-flash` returned incompatible
keys across multiple production attempts; `gpt-5.6-sol` passed six synthetic
strict-schema probes (two generic windows, four other meeting types), not a real
meeting replay or guarantee for all transcript content. Verify provider capability
and cost before enabling it. Leaving the override empty preserves prior model
selection and failure semantics. Reindexing vectors does not retry
a failed highlights stage.

## Summary evidence windows

Long meeting evidence is summarized in bounded windows. Each per-window partial
call uses a concise evidence-extraction prompt: it keeps the shared evidence
rules, source references, spoken-versus-visual labels, uncertainty, the upload
date, and the requested output language, but it never carries the final report
system prompt, selected style examples, or the custom output template. Those
are applied once, on the final synthesis call (or on the single-window and
transcript-only summary calls). The provider still controls its own context and
output ceilings, and concise bullets are requested by the prompt, not
guaranteed.

## Summary streaming transport

The summary service uses the OpenAI-compatible adapter's `complete_summary()`
capability when available. Providers without it retain their existing completion
contract. Title inference and chat keep using bounded `complete()` even when they
share the same client. The summary transport uses standard Chat Completions SSE:
- Exactly one HTTP request per summary call (`stream=true`, the same model,
  messages and temperature as before). There is no empty-truncation retry at a
  larger token budget and no provider fallback or SDK retry.
- No `max_tokens`/`max_completion_tokens` cap is sent on summary calls, so the
  provider's own output ceiling applies; Zabt keeps the bounded default budget
  for titles, chat, highlights, and structured extraction.
- The stream is consumed as text deltas only; hidden reasoning deltas are
  ignored. A `length`/`max_tokens`, `content_filter`, or refusal result, an
  interrupted stream with no terminal `finish_reason` fails with a safe,
  credential-free provider error. A completed blank stream returns empty text
  for the summary service to reject before saving.
- Summary stream reads may idle up to `3600` seconds between chunks (the
  one-hour allowance), while connect keeps the bounded `120`-second default;
  this per-operation timeout is not a total wall-clock deadline for the whole
  summary. The allowance applies only to summary calls. The completion client
  for all other requests keeps its per-request `120`-second timeout; bounding
  requests does not guarantee that an unavailable or overloaded provider will
  respond.

## Upload confirmation

External S3-compatible storage has no local object-storage webhook wired into this Compose file;
there is no MinIO webhook in the full-cloud topology.
After the browser finishes the presigned upload, the client must call
`POST /api/v1/meetings/{meeting_id}/confirm-upload`. The web app already does this when
the API reports `storage_provider: "s3"`; custom clients must preserve the same confirmation step.
Without confirmation, the meeting remains `pending_upload` and the Celery transcription chain is
not started.

## Actsis transcription boundary

This topology explicitly uses the verified Actsis diarization model over HTTPS. Compose selects
`TRANSCRIPTION_MODEL=whisper-diarize`, `TRANSCRIPTION_RESPONSE_FORMAT=diarized_json`,
`TRANSCRIPTION_CLOUD_DIARIZATION=true`, `TRANSCRIPTION_SPEAKER_REQUIRED=true`, and
`TRANSCRIPTION_CHUNKING_STRATEGY=auto`. The provider accepts this model only for a custom endpoint
with cloud diarization enabled; official OpenAI and generic compatible models keep their existing
non-diarized defaults.

Use `https://ai.actsis.internal/v1` (or another explicitly provisioned HTTPS Actsis endpoint) in
the root `.env`. Do not use the HTTP URL, add a local GPU/pyannote worker, or add
`HF_TOKEN` to this no-GPU topology.

The full-cloud profile uses a `7200`-second request timeout, zero provider retries, and a
`9000`-second stale-recovery grace. The grace must remain above the blocking request timeout so
Beat does not claim or dispatch a live Actsis request at the timeout boundary. For the equivalent
no-GPU local topology and its `:8000` API / `:3001` web ports, use
the `local-actsis` profile instead of changing the base generic `600`/`2`/`900` fallbacks.

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
