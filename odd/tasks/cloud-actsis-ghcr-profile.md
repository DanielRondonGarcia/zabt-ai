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
- Route: single writer for Compose, tests, CI, and docs; independent verification is deferred because the deployment surface is high-risk.
- Change unit: one coherent source-free deployment profile with its tests and operator documentation.
- Do not commit, push, run a release workflow, or deploy from this worker task. Release publication and host deployment are deferred to a later authorized step.

## Acceptance Criteria

- [x] `cloud-actsis` contains API, worker, beat, web, local DB/Redis/MinIO/`mc`, and Qdrant services backed by GHCR images or stable infrastructure images, with no `build:` fallback.
- [x] API and workers receive matching `TRANSCRIPTION_PROVIDER=openai-file` and `TRANSCRIPTION_BACKEND=openai-file`; Actsis endpoint/model settings are aligned and no local GPU or RunPod service is activated.
- [x] A one-shot preflight receives only presence flags and reports missing variable names, never secret values; API startup also rejects a non-HTTPS MinIO public endpoint without logging its value.
- [x] Web runtime public URL settings are container-time settings, with a compatible `/api/runtime-config` web image required; existing `v1.0.0` is explicitly excluded.
- [x] CI/static tests validate service/profile membership, GHCR tag use, the ACT-TWC-11-observed immutable Qdrant digest, absence of build fallback, Actsis selector equality, local MinIO endpoints, configurable host ports, and secret scoping.
- [x] Deployment docs give ACT-TWC-11 NetBird app routes with Preserve Full Path enabled and require a separate HTTPS MinIO hostname for browser presigned uploads.
- [x] Compose and frontend build/runtime-config checks pass without printing or manually inspecting actual `.env` content.
- [ ] ACT-TWC-11's remote root `.env` still lacks `ACTSIS_API_KEY` and a publicly routed HTTPS `MINIO_PUBLIC_ENDPOINT`; deployment also waits for a compatible published image tag.

## Checklist

- [x] T1: Add the source-free `cloud-actsis` profile and presence-only preflight.
- [x] T2: Update targeted static tests, CI validation, and deployment/release docs.
- [x] T3: Run targeted Compose/profile checks, isolated static tests, frontend build/runtime-config checks, and `git diff --check`.
- [ ] T4: Re-verify the bounded Qdrant pin and NetBird documentation corrections before release.
- [ ] T5: In a later authorized step, publish a compatible immutable release and deploy only after ACT-TWC-11's operator-managed root `.env` contains all required values.

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

- Status: source-free `cloud-actsis` profile and NetBird guidance are implemented. Final verification/release and ACT-TWC-11 deployment remain pending. The Compose/runtime configuration work is committed and pushed on `feat/actsis-local-stack` as `9265fe7` and `ffa9f04`; no compatible release has been published.
- Implemented: root Compose profile uses GHCR-pinned API/worker/beat/web/MinIO/`mc` images selected by `ZABT_IMAGE_TAG`; local Postgres/Redis/MinIO/Qdrant and `minio-init` are included without source-tree build contexts or bind mounts. Transcription is pinned to Actsis `openai-file` / `whisper-diarize`; the API validates the public MinIO URL scheme while the separate presence-only guard never receives endpoint or credential values.
- Implemented: CI and unit safeguards cover rendered image references, profile boundaries, provider alignment, MinIO endpoint separation, web public settings, credentials, configurable ports, and fail-closed missing-variable behavior. Deployment, release, and frontend instructions require a later runtime-config-compatible immutable image and exclude `v1.0.0`.
- Correction: Qdrant is pinned to the ACT-TWC-11-observed digest `sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`, and CI/static assertions require that exact image. The deployment guide now specifies the app routes and separate HTTPS MinIO hostname; it does not claim NetBird is configured.
- Host state: ACT-TWC-11 was read-only audited; its root `.env` lacks `ACTSIS_API_KEY` and a publicly routed HTTPS `MINIO_PUBLIC_ENDPOINT`. Its `/home/ubuntu/docker/zabt` directory was empty at audit time. Do not read or print any actual `.env` values.
- Verification: Compose config passed; the targeted test was first red on the old Qdrant image and then green (12 passed); `git diff --check` passed. The root Compose inventory remains one file and the full-cloud env example now directs users to its root profile.
- NetBird route map: `/api/` and `/.well-known/` to API port 8000; `/` to web port 3000; preserve full paths. MinIO needs a separate browser-reachable HTTPS hostname to port 9000.
- Next: finish final static review, integrate the profile into `main`, publish a compatible immutable GHCR release, then update the ACT-TWC-11 deployment directory and `.env` only after the Actsis key and MinIO public endpoint are configured. No actual `.env` values are stored in this task note.
