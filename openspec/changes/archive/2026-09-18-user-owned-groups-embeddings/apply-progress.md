# Apply Progress — user-owned-groups-embeddings

## Scope

PR 1 remains previously completed. This update reconciles the aborted PR 2 notes and records only evidence observed in this worktree.

## PR 2 status

Implemented in this pass:

- `EmbeddingProvider` protocol and provider dispatcher with no provider fallback.
- Ollama and OpenAI-compatible HTTP embedding adapters using `httpx`, configured model/base URL/API key/max batch, and configured-dimension assertions.
- Embedding settings and validation in `Settings`, including `INDEXING_ENABLED` rollback toggle.
- Runtime `qdrant-client` dependency added through `uv add qdrant-client` and lockfile updated.
- `QdrantVectorStoreClient` with provider+dimension collection naming, idempotent collection creation, payload indexes, deterministic point IDs through the chunking layer, upsert, delete-by-filter, and owner+group filtered search.
- Qdrant Compose service corrected to the `local` profile with persistent `qdrant_data`, `/qdrant/storage`, and healthcheck; embedding/Qdrant env vars added to API and worker.
- Deterministic canonicalization and chunking for summary, transcript, transliterated text, and completed structured output.
- Celery indexing lifecycle tasks: `stage_embedding`, `delete_meeting_vectors`, `delete_group_vectors`, `reindex_group`.
- Pipeline wiring after `stage_extract_intelligence`; the embedding stage is deliberately not linked to `on_stage_failure` because that handler marks meetings failed. This corrects the task text in favor of the binding design requirement: embedding failure must be isolated from meeting completion.
- Assignment/reassignment/unassignment, meeting summary edit/restore/source-field update, meeting delete, and group delete enqueue indexing or cleanup.
- Focused fake/unit tests for providers, chunking/canonicalization, vector store filters, deterministic IDs, lifecycle no-op behavior, indexing upserts, assignment cleanup, and failure-isolated pipeline wiring.

## Corrections to prior aborted claims

The prior PR 2 section claimed completed provider/vector-store behavior without observed tests and listed stale task names (`stage_index_group`, `stage_cleanup_orphaned_vectors`, etc.) that do not match the accepted tasks.md PR 2 contract. Those claims are superseded by this document and the current `tasks.md` evidence.

No live Ollama/OpenAI embedding call was run or claimed. Qdrant was validated both with local fakes and a bounded local Qdrant server probe recorded under PR3b; no credentials were used.

## Observed validation

- RED: `cd backend && uv run pytest app/tests/unit/services/embeddings/test_pr2_contract.py app/tests/unit/test_embedding_lifecycle.py -q` initially failed with `ModuleNotFoundError: No module named 'app.services.embeddings.canonicalize'`.
- GREEN/TRIANGULATE: `cd backend && uv run pytest app/tests/unit/services/embeddings/test_pr2_contract.py app/tests/unit/test_embedding_lifecycle.py -q` → `10 passed, 1 warning`.
- Dependency/settings smoke: `cd backend && uv run python -c "from app.core.config import settings; import qdrant_client; print(settings.EMBEDDING_PROVIDER, settings.EMBEDDING_DIMENSION, qdrant_client.__version__ if hasattr(qdrant_client, '__version__') else 'qdrant-client')"` → `ollama 768 qdrant-client`.
- Bounded correctness pass: `cd backend && uv run pytest app/tests/unit/services/embeddings/test_pr2_contract.py app/tests/unit/test_embedding_lifecycle.py -q` → `11 passed, 1 warning`; includes a focused assignment test that records `commit`/`refresh` before mocked `delete_meeting_vectors.delay` and `stage_embedding.delay`.
- Legacy `backend/tests/services/embeddings` was not used as validation because `backend/tests/conftest.py` imports `app.main`, which imports storage and attempts to contact MinIO (`http://minio:9000`) in this environment.

## TDD Cycle Evidence

Strict TDD evidence is recorded by delivery slice. `RED` means the test file was written before the associated implementation or validation slice; where a historical failing run was not captured, that limitation is stated rather than reconstructed.

| Task slice | RED | GREEN | TRIANGULATE | SAFETY NET | REFACTOR |
|---|---|---|---|---|---|
| 1.1–1.11 groups/model/migration | ✅ Written | ✅ Passed — model, service, endpoint, and migration checks passed | ✅ Multiple model/service/API scenarios | ✅ Existing group tests rerun | ✅ Production code kept scoped to group ownership |
| 2.1–2.17 embeddings/Qdrant/chunking/lifecycle | ✅ Written — initial missing-module RED observed | ✅ Passed — focused lifecycle/provider suites and local Qdrant evidence | ✅ Provider, filters, IDs, lifecycle, retry, cleanup, and disabled-indexing cases | ✅ Existing embedding/lifecycle suite rerun | ✅ Failure isolation and current Qdrant API drift documented |
| 3.1–3.4 retrieval/reindex/observability | ✅ Written — `7 failed, 13 passed` RED observed | ✅ Passed — `27 passed, 2 warnings` | ✅ Auth ordering, 503s, filters, endpoint contracts, telemetry | ✅ Existing groups/lifecycle tests rerun | ✅ Endpoint defense-in-depth and bounded telemetry |
| 3.5–3.7 leakage/integration/docs | ✅ Written | ✅ Passed — focused PR3b `24 passed, 2 warnings`; docs read back | ✅ Cross-owner/group, failure, endpoint, and lifecycle variants | ✅ Combined focused suite rerun | ✅ Docs and tests limited to accepted PR3b scope |
| 3.8 full suite/coverage | ✅ Written — coverage validation tests added before final rerun | ✅ Passed — `265 passed, 3 warnings`, services coverage `70%` | ✅ 15 focused service-coverage modules plus full-suite safety net | ✅ Before/after baseline observed: `63 passed` → `265 passed` | ✅ Tests only; no production behavior changes |
| 3.9 empirical provider/Qdrant checks | ➖ Validation-only | ✅ Passed or documented — Qdrant live probe passed; Ollama timeout documented | ✅ Qdrant API drift, filter isolation, dimension, and Ollama availability variants | ➖ N/A | ✅ Deviations recorded without claiming unavailable evidence |

The coverage-expansion tests are validation-only additions for the configured 70% gate; they do not claim unobserved production RED failures. No TDD evidence is inferred from task descriptions.

## PR 3a status

Implemented in this pass:

- `GroupRetrievalService` in `backend/app/services/retrieval.py` with authorization before embedding/vector calls, configured provider query embedding, server-derived `owner_id` + `group_id` filters, optional `kinds`, and a single HTTP 503 shape (`retrieval unavailable`) for disabled indexing or provider/Qdrant failures.
- `POST /groups/{group_id}/search` with validated request/response models returning `{group_id, results}` and result fields `meeting_id`, `kind`, `chunk_index`, `score`, `text`.
- `POST /groups/{group_id}/reindex` with explicit authorization before enqueue, HTTP 202 response `{status: "accepted", task_id}`, and lazy worker import to avoid test-time `app.main`/storage side effects.
- Bounded start/complete/error logging and analytics events for indexing, meeting-vector deletion, group-vector deletion, and reindexing. Event properties exclude indexed text, API keys, provider payloads, and cross-owner data. Embedding failures still re-raise for Celery retry and are not linked to meeting failure status.

## PR 3a strict-TDD evidence

- RED: `cd backend && uv run pytest app/tests/unit/services/test_retrieval.py app/tests/unit/api/v1/test_groups.py -q` initially failed with missing `app.services.retrieval`, missing `retrieval_service`, and missing `reindex_group` endpoint wiring (`7 failed, 13 passed, 2 warnings`).
- GREEN/TRIANGULATE: `cd backend && uv run pytest app/tests/unit/services/test_retrieval.py app/tests/unit/api/v1/test_groups.py app/tests/unit/test_embedding_lifecycle.py -q` → `27 passed, 2 warnings`.
- Covered PR3a behavior: authorization-before-provider, preserved 403, `INDEXING_ENABLED=False` 503, provider/Qdrant failure 503, server-derived owner/group/kind filters, search endpoint response shape, reindex authorization before enqueue, and telemetry without text payloads.

## PR 3b status

Implemented in this pass:

- Release-blocking retrieval isolation tests using fakes/minimal apps only: foreign-group authorization, cross-owner and cross-group isolation, provider/Qdrant unavailable `503` without partial results, endpoint-level foreign-group denial before retrieval, and hard server-side Qdrant `owner_id` + `group_id` + optional `kind` filters.
- Concrete PR3 defect fix: `POST /groups/{group_id}/search` now calls `group_service.get_accessible(group_id, current_user.id)` before invoking retrieval, matching the endpoint-level foreign-group 403 acceptance test. Retrieval service authorization remains in place as a defense-in-depth boundary.
- Bounded lifecycle test with fake meeting/provider/vector store: assignment/indexing payload tags, deterministic re-run IDs, `reindex_group` scheduling, unassignment cleanup, and group deletion cleanup. This is not claimed as a DB/worker/live-provider integration pass.
- English docs: `docs/group-embedding-overview.md`, `docs/group-api.md`, `docs/embedding-configuration.md`, plus a concise README link. Docs explicitly defer MCP/chat.
- Empirical local Qdrant validation with temporary collections and no credentials. Current installed `qdrant-client` has no `search` method; `query_points` confirmed collection create/upsert and payload filter hit/miss behavior.
- Local Ollama `/v1/embeddings` probe was attempted only against the configured local endpoint/model and timed out; no OpenAI call was made and no credentials were printed.

## PR 3b observed validation

- Focused PR3b: `cd backend && uv run pytest app/tests/unit/services/test_retrieval_pr3b.py app/tests/unit/api/v1/test_groups.py app/tests/unit/test_embedding_lifecycle_pr3b.py -q` → `24 passed, 2 warnings`.
- Combined focused retrieval/indexing suite: `cd backend && uv run pytest app/tests/unit/services/test_retrieval.py app/tests/unit/services/test_retrieval_pr3b.py app/tests/unit/api/v1/test_groups.py app/tests/unit/test_embedding_lifecycle.py app/tests/unit/test_embedding_lifecycle_pr3b.py app/tests/unit/services/embeddings/test_pr2_contract.py -q` → `40 passed, 2 warnings`.
- Qdrant drift probe: `cd backend && uv run python - <<'PY' ... client.search(...) ... PY` failed with `AttributeError: 'QdrantClient' object has no attribute 'search'`, confirming installed-client API drift.
- Qdrant adaptive probe: `cd backend && uv run python - <<'PY' ... client.query_points(...) ... PY` → `QDRANT_OK collection=pr3b_validation_1bcb03f4 hit_count=1 miss_count=0 point_id=0b83d0cb-9c23-438e-ac5b-249e3885d6ab`; cleanup attempted immediately after the probe.
- Ollama local probe: `cd backend && uv run python - <<'PY' ... urllib.request.urlopen(.../v1/embeddings) ... PY` → `OLLAMA_UNAVAILABLE_OR_DEVIATION base_url=http://host.docker.internal:11434/v1 model=nomic-embed-text error=URLError: <urlopen error timed out>`.
- Coverage tooling fix: `cd backend && uv add --dev pytest-cov` → installed `coverage==7.16.1` and `pytest-cov==7.1.0`, updated `backend/pyproject.toml` and `backend/uv.lock`.
- Initial configured backend uv full-suite/coverage command after adding `pytest-cov`: `cd backend && uv run pytest app/tests/ -v --cov=app/services --cov-report=term-missing` → `63 passed, 2 warnings`, total services coverage `29%`.
- Coverage expansion added focused tests under `backend/app/tests/unit/services/` for template/integration, meeting, storage, transcription chunks/OpenAI provider, auth, Microsoft Graph/email share, multimodal context, bot orchestration, transcription registry, visual segments, and devices. Production behavior was unchanged.
- Final configured backend uv full-suite/coverage command: `cd backend && uv run pytest app/tests/ -q --cov=app/services --cov-report=term-missing` → `265 passed, 3 warnings`, total services coverage `70%`; task 3.8 acceptance is met.

## Remaining limitations / not claimed

- Ollama route/dimension and RAM/CPU could not be confirmed because the configured local endpoint timed out. No OpenAI or remote embedding-provider call was made.
- The lifecycle test is bounded with fakes; no full DB/Celery worker/live-provider integration pass is claimed.
- Native SDD verification and archive remain pending; no commit, push, PR, or release was created.
