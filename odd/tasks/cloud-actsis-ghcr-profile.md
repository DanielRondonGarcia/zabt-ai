# Cloud Actsis GHCR Deployment Profile

## Objective

Provide a source-free production deployment on ACT-TWC-11 using one Compose file, GHCR-published application images, local PostgreSQL/Redis/MinIO, and the Actsis transcription endpoint, with no local GPU or RunPod dependency.

## Problem and Why

The existing `local-actsis` profile builds the application from source, but the target deployment directory is intended to contain only Compose and `.env`. The `full-cloud` profile uses GHCR images but requires external S3 and managed dependencies not included in this host topology. The already published `v1.0.0` web image also predates `/api/runtime-config`, so a compatible image release is required before deployment.

## Scope

- Add a `cloud-actsis` profile to the single root `docker-compose.yml`.
- Use immutable GHCR tags for API, worker/beat, web, MinIO, and `mc`; use no local source builds in this profile.
- Use local Postgres, Redis, MinIO, and Qdrant; use Actsis (`openai-file`, `whisper-diarize`, `https://ai.actsis.internal/v1`) for transcription; do not activate local GPU, RunPod, or optional vision services.
- Keep secrets scoped to the services that need them and source deployment values from the root `.env`. Never read, copy, or expose actual `.env` values.
- Add a presence-only preflight, tests, CI checks, and deployment/release documentation.
- Prepare for a compatible immutable release only after verification and integration into `main`; the user authorized publishing it, but this worker task must not run the release workflow.
- Deploy to ACT-TWC-11 only after ACTSIS_API_KEY and a browser-reachable HTTPS MinIO endpoint are configured directly in the host `.env`.

## Constraints

- Do not deploy the currently published `v1.0.0` web image with this profile.
- Do not pass the full `.env` to the web service or print secret values during validation.
- Do not start services before required Actsis and MinIO public endpoint settings are present.
- No RunPod API key or local GPU is used by this deployment profile.
- Artifacts, code, tests, and documentation remain in English.

## Authorized Scope and Route

- Repository: `zabt-ai`, current branch `feat/actsis-local-stack`.
- Route: single writer for Compose, tests, CI, and docs; independent static verification completed because the deployment surface is high-risk.
- Change unit: one coherent source-free deployment profile with its tests and operator documentation.
- Delivery strategy: `stacked-to-main` (user selected option 1); the user explicitly waived issue references and accepted `size:exception` for the oversized Compose consolidation and Cloud Actsis work units. Repository CI has no issue-link validation job.
- Do not commit, push, run a release workflow, or deploy from this worker task. Release publication and host deployment are deferred to a later authorized step.

## Acceptance Criteria

- [x] `cloud-actsis` contains API, worker, beat, web, local DB/Redis/MinIO/`mc`, and Qdrant services backed by GHCR images or stable infrastructure images, with no `build:` fallback.
- [x] API and workers receive `TRANSCRIPTION_PROVIDER=openai-file` and omit the unsupported legacy `TRANSCRIPTION_BACKEND`; Actsis endpoint/model settings are aligned and no local GPU or RunPod service is activated.
- [x] A one-shot preflight receives only presence flags and reports missing variable names, never secret values; API startup also rejects a non-HTTPS MinIO public endpoint without logging its value.
- [x] Web runtime public URL settings are container-time settings, with a compatible `/api/runtime-config` web image required; existing `v1.0.0` is explicitly excluded.
- [x] CI/static tests validate service/profile membership, GHCR tag use, the ACT-TWC-11-observed immutable Qdrant digest, absence of build fallback, the canonical Actsis selector and omission of the unsupported legacy selector, MinIO webhook-secret propagation, Actsis GLM defaults, local MinIO endpoints, configurable host ports, and secret scoping.
- [x] Deployment docs give ACT-TWC-11 NetBird app routes with Preserve Full Path enabled and require a separate HTTPS MinIO hostname for browser presigned uploads.
- [x] Compose and frontend build/runtime-config checks pass without printing or manually inspecting actual `.env` content.
- [x] ACT-TWC-11 has a non-empty `ACTSIS_API_KEY` and HTTPS `MINIO_PUBLIC_ENDPOINT`; NetBird routes are configured and verified.

## Checklist

- [x] T1: Add the source-free `cloud-actsis` profile and presence-only preflight.
- [x] T2: Update targeted static tests, CI validation, and deployment/release docs.
- [x] T3: Run targeted Compose/profile checks, isolated static tests, frontend build/runtime-config checks, and `git diff --check`.
- [x] T4: Independently verify the profile, Qdrant pin, and ACT-TWC-11 NetBird documentation; the full-cloud env-example file's readback remains policy-blocked.
- [x] T5: Deploy cloud-actsis on ACT-TWC-11 after selector/webhook fixes and a passing presence-only preflight; NetBird routes are configured.

## Verification Commands

- `docker compose --profile cloud-actsis config --quiet` — passed; no output.
- `docker compose --profile cloud-prod config --quiet` — passed; no output.
- `uv run pytest --confcutdir=backend/tests/unit backend/tests/unit/test_full_cloud_compose.py -q` — passed; 12 tests.
- `npm --prefix frontend-2 run build` — passed; `/api/runtime-config` is present in the route output. Next.js emitted a non-blocking multiple-lockfiles workspace-root warning.
- `npm --prefix frontend-2 run verify:runtime-config` — passed; runtime public URLs are read from the running container environment.
- `git diff --check` — passed; Git emitted only line-ending conversion notices for edited files.
- After the compatible release and host variables are ready: `docker compose --profile cloud-actsis pull` and `docker compose --profile cloud-actsis up -d --no-build`.

### Bounded correction validation

- `docker compose --profile cloud-actsis config --quiet` — passed; no output.
- `uv run pytest --confcutdir=backend/tests/unit backend/tests/unit/test_full_cloud_compose.py -q` — RED: the Qdrant digest assertion failed against `qdrant/qdrant:latest`; GREEN: 12 tests passed after the pin and assertions were updated.
- `git diff --check` — passed; Git emitted only line-ending conversion notices.
- A stale deleted-Compose reference in the full-cloud env example was corrected to the root `full-cloud` profile. The example was edited by exact-line patch only; no secret values were read.

## Progress and Next Step

- Status: PRs #1–#6 were merged to `main`; compatible GHCR release `v1.0.1` is published. PR #4 removed the unsupported legacy selector, PR #5 injected the webhook secret into worker/beat, and PR #6 routed cloud application defaults through Actsis GLM. Corrected Compose is installed at `/home/ubuntu/docker/zabt/compose.yml`; cloud-actsis is healthy.
- Implemented: root Compose profile uses GHCR-pinned API/worker/beat/web/MinIO/`mc` images selected by `ZABT_IMAGE_TAG`; local Postgres/Redis/MinIO/Qdrant and `minio-init` are included without source-tree build contexts or bind mounts. Transcription is pinned to Actsis `openai-file` / `whisper-diarize`; the API validates the public MinIO URL scheme while the separate presence-only guard never receives endpoint or credential values.
- Implemented: CI and unit safeguards cover rendered image references, profile boundaries, provider alignment, MinIO endpoint separation, web public settings, credentials, configurable ports, and fail-closed missing-variable behavior. Deployment, release, and frontend instructions require a later runtime-config-compatible immutable image and exclude `v1.0.0`.
- Correction: Qdrant is pinned to the ACT-TWC-11-observed digest `sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`, and CI/static assertions require that exact image. The deployment guide now specifies the app routes and separate HTTPS MinIO hostname; it does not claim NetBird is configured.
- Host state: user authorized SSH profile `ACT-TWC-11`. `/home/ubuntu/docker/zabt/compose.yml` and `.env` are colocated; `.env` is mode `0600`. Presence-only checks found non-empty `ACTSIS_API_KEY`, `ZABT_IMAGE_TAG=v1.0.1`, and `MINIO_PUBLIC_ENDPOINT=https://zabt-upload.proxy.actsis.com`. Host ports are API 8002, web 3005, Postgres 5434, MinIO S3 9000, console 9002, and Qdrant 6335. API/web returned HTTP 200; worker/beat restart counts are 0. NetBird routes are configured and verified; do not read or print `.env` values.
- Verification: Remote preflight passed; Compose recreated the stack after selector/webhook/GLM-default fixes; API, worker, beat, web, DB, Redis, MinIO, Qdrant, and MinIO init are healthy/running as expected. API/web health returned HTTP 200; focused tests passed 12/12.
- NetBird route map: preserve full paths; `/api/runtime-config` → web 3005 before generic `/api/` and `/.well-known/` → API 8002; `/` → web 3005. `zabt-upload.proxy.actsis.com/` → MinIO S3 9000 at hostname root; console 9002 private. MinIO health returned HTTP 200 and Zabt login loaded.
- Next: run an authenticated browser upload smoke test if desired. Summary, AI Chat, and direct vision now default to Actsis `glm-5.3-flash`; per-user AI provider settings may override those application defaults.
