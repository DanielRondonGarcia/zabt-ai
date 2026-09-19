# ODD Task Ledger — full-cloud-direct-ai

## Goal
Run Zabt without local AI/model workers: Actsis handles transcription and embeddings, OpenAI handles summary/intelligence and visual inference, and the full-cloud Compose file connects only the application services to managed external dependencies.

## Authorized scope
- Refactor visual processing so the backend/Celery worker no longer requires the separate `zabt-vision-worker` deployment.
- Keep summary and meeting intelligence on the configured OpenAI-compatible cloud provider.
- Add a standalone full-cloud Compose topology with no local database, object storage, Redis, Qdrant, Ollama, GPU worker, or vision worker services.
- Preserve Actsis provider/key isolation and the existing non-diarized transcription boundary.
- Keep changes uncommitted; do not deploy, push, or create a PR unless separately authorized.

## Constraints
- No local AI/model inference or model-cache services in the full-cloud topology.
- Do not expose or copy credentials into source, fixtures, task files, or documentation.
- Preserve visual lifecycle leases, heartbeats, idempotency, fallback outcomes, telemetry, notifications, and artifact cleanup.
- Preserve transcript-only meeting intelligence semantics.
- Prefer work-unit boundaries and keep tests with the behavior they verify.

## Tasks
- [x] Explore current visual, summary, provider, and Compose flows; identify direct-cloud refactor boundary.
- [x] Move/adapt visual media processing and OpenAI vision inference into the backend/Celery worker without requiring `zabt-vision-worker`.
- [x] Preserve visual result/failure/cleanup/idempotency behavior and add focused regression coverage.
- [x] Add a standalone `docker-compose.full-cloud.yml` with external Postgres, Redis, S3-compatible storage, Qdrant, Actsis, and OpenAI settings; exclude all local AI/model and local infrastructure services.
- [x] Update deployment/configuration documentation and Compose validation coverage for the full-cloud topology.
- [x] Run focused tests, Compose config validation, and static checks; record exact evidence here.

## Acceptance criteria
- Visual breakdown can complete through the backend/Celery worker with OpenAI without `zabt-vision-worker`.
- Summary and structured intelligence use cloud configuration and remain independent of local model services.
- Full-cloud Compose contains only deployable app services and no local AI/model or local infrastructure service definitions.
- External dependency URLs, credentials, TLS/CA options, storage, and Qdrant settings are explicit and fail closed when required values are absent.
- Existing Actsis embeddings/transcription routing remains intact; diarization is not claimed for Actsis.
- Existing visual lifecycle and summary/intelligence tests remain green, with new direct-cloud coverage.

## Progress
- Status: full in-process/direct-cloud visual path and standalone full-cloud application topology are implemented; work remains uncommitted for parent reconciliation.
- Verification: the prior isolated direct visual client/service tests pass with a storage stub (`19 passed`, one pre-existing Pydantic deprecation warning); this unit's Compose and Qdrant tests pass. The exact command outcomes are recorded below.
- Current unit: connect only `api`, `worker`, `beat`, and `web` to managed Postgres, Redis, S3-compatible storage, Qdrant, Actsis, and OpenAI services, with no local infrastructure or model workers.
- Next step: parent review and reconciliation of the Engram mirror; no deployment or commit is part of this unit.

## Current-unit evidence

- `docker compose -f docker-compose.full-cloud.yml config --quiet` with dummy environment values: passed.
- `python -m compileall -q app` (from `backend`): passed.
- `uv run pytest tests/unit/test_full_cloud_compose.py tests/unit/test_vector_store_api_key.py -q --noconftest` (from `backend`): `4 passed, 1 warning` (pre-existing Pydantic class-based Config deprecation).
- `git diff --check`: passed; Git emitted only pre-existing LF/CRLF conversion warnings for unrelated tracked files and the touched documentation file.
