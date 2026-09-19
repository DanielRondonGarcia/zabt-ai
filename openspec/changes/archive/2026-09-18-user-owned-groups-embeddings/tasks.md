# Implementation Tasks: User-Owned Groups + Group-Scoped Embeddings

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 650-800 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3a → PR 3b (see Chain Strategy) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

---

## Decision needed before apply: No
## Chained PRs recommended: Yes
## Chain strategy: stacked-to-main
## 400-line budget risk: High

---

## Chain Boundary Summary

The change is explicitly split into **4 autonomous PR slices** with clear rollback boundaries:

1. **PR 1 (Group Entity + CRUD + Migration)**: Data model, `GroupService`, `groups.py` router, migration. No vector code. Testable: CRUD operations, authorization, migration up/down.
2. **PR 2 (Indexing Infrastructure + Lifecycle)**: Embedding provider seam, Qdrant client, Celery tasks, assignment triggers. Testable: indexing idempotency, cleanup, failure isolation (no retrieval contract needed).
3. **PR 3a (Retrieval + Observability)**: Internal retrieval service/endpoint, manual reindex endpoint, and instrumentation. Testable: authorized group filtering and operational events.
4. **PR 3b (Validation + Documentation)**: Leakage/integration tests, documentation, coverage/full-suite verification, and empirical provider/Qdrant validation.

---

## PR 1: Group Entity, Ownership/CRUD, Meeting Assignment, Migration

**Goal**: Owner-scoped group management with nullable `group_id` on `Meeting` and a migration. No vector code. This slice is fully testable without Qdrant.

- [x] 1.1 Add `Group` SQLModel model to `backend/app/models/base.py` following `SummaryTemplate` shape (id, name, description, owner_id FK → user.id with index, created_at, updated_at).
  - Acceptance: `Group` compiles, passes mypy type checks, follows existing model conventions.
  - Dependencies: None.
  - Evidence: Tests in `backend/app/tests/unit/models/test_group.py` pass; model follows `SummaryTemplate` pattern with `Relationship(back_populates="meetings")`.

- [x] 1.2 Add `GroupRead`, `GroupCreate`, `GroupUpdate` schemas to `backend/app/models/base.py` following `SummaryTemplateRead` pattern.
  - Acceptance: Schemas validate required fields, `owner_id` is optional in create/update, IDs are `int | None`.
  - Dependencies: 1.1.
  - Evidence: Schemas added to `base.py` with proper SQLModel inheritance; exported from `app/models/__init__.py`.

- [x] 1.3 Add nullable `Meeting.group_id` FK and optional back-reference to `Meeting` model in `backend/app/models/base.py`.
  - Acceptance: `Meeting.group_id` is `int | None`, FK to `group.id`, index is present.
  - Dependencies: 1.1.
  - Evidence: `group_id` column with `foreign_key="group.id"` and `index=True`; `Relationship(back_populates="meetings")` on both models.

- [x] 1.4 Create Alembic migration `backend/alembic/versions/8f0dd5fa9efd_add_group_and_meeting_group_id.py` (reusing existing naming convention).
  - Acceptance: Migration up creates `group` table and `meeting.group_id` column with index; migration down drops column then table; both are safe (nullable, no data loss).
  - Dependencies: 1.1, 1.3.
  - Evidence: Migration file created with `op.create_table` for `group` and `op.add_column` for `meeting.group_id`; down migration reverses operations in correct order.

- [x] 1.5 Implement `GroupService(BaseService)` in `backend/app/services/group.py` mirroring `TemplateService`:
    - `list_for_user(user_id)` → filter by `owner_id`.
    - `get_accessible(group_id, user_id)` → 404 if missing, 403 if owner mismatch.
    - `create(user_id, name, description)` → save with `owner_id`.
    - `update(group_id, user_id, ...)` → 404/403 then mutate + commit.
    - `delete(group_id, user_id)` → 404/403 then delete (no vector cleanup in this slice).
  - Acceptance: All methods have explicit 403/404 paths, owner scoping is enforced, no cross-owner access.
  - Dependencies: 1.1, 1.3.
  - Evidence: Service implemented with all 5 methods; tests in `backend/app/tests/unit/services/test_group.py` cover owner scoping, 403/404 paths, CRUD.

- [x] 1.6 Implement `groups.py` router (`backend/app/api/v1/endpoints/groups.py`) mirroring `templates.py`:
    - `POST /groups` (create), `GET /groups` (list), `GET /groups/{id}` (get), `PATCH /groups/{id}` (update), `DELETE /groups/{id}` (delete).
    - All endpoints use `Depends(deps.get_current_active_user)`, return 403/404 explicitly.
  - Acceptance: All 5 endpoints compile, pass OpenAPI validation, authorization tests pass.
  - Dependencies: 1.5.
  - Evidence: Router implemented with all 5 CRUD endpoints; each endpoint validates ownership before operation.

- [x] 1.7 Register `groups.router` in `backend/app/api/api.py` with prefix `/groups`, tag `["groups"]`.
  - Acceptance: Routes appear in `/docs`, registered endpoint count is 5.
  - Dependencies: 1.6.
  - Evidence: Router included in `api_router` with `prefix="/groups"` and `tags=["groups"]`.

- [x] 1.8 Update meeting endpoints to accept/return `group_id` and expose assignment operation:
    - `GET /meetings/{id}` returns `group_id`.
    - `PATCH /meetings/{id}/assign-group` (new endpoint) accepts `{group_id: int | None}` and updates `Meeting.group_id`.
  - Acceptance: Assignment endpoint updates DB, returns updated meeting with `group_id`; unassignment sets to NULL.
  - Dependencies: 1.6.
  - Evidence: `AssignGroupPayload` model and `PATCH /meetings/{id}/assign-group` endpoint implemented in `meetings.py`.

- [x] 1.9 Write unit tests for `GroupService` (owner scoping, 403/404 paths, CRUD).
  - Acceptance: Tests cover all 5 service methods, foreign-group denial returns 403, missing group returns 404.
  - Dependencies: 1.5.
  - Evidence: `cd backend && uv run pytest app/tests/unit/services/test_service_group.py -q` passes 19 tests covering CRUD, validation, 403/404, and owner scoping using isolated SQLite Group-only metadata with patched service engine symbols.

- [x] 1.10 Write endpoint tests for `groups.py` (authorization, 403/404, create/list/get/update/delete).
  - Acceptance: All 5 endpoints tested with JWT auth, foreign-owner denial returns 403.
  - Dependencies: 1.6, 1.9.
  - Evidence: `cd backend && uv run pytest app/tests/unit/api/v1/test_groups.py -q` passes 13 tests covering CRUD, PATCH-only update, not-found, and foreign-owner 403 paths in a minimal FastAPI app with `get_current_active_user` overridden to avoid `app.main`/MinIO side effects.

- [x] 1.11 Verify migration: run `alembic upgrade head`, confirm `group` table and `meeting.group_id` exist; run `alembic downgrade`, confirm both are dropped.
  - Acceptance: Migration up/down runs without error; DB schema matches expected state.
  - Dependencies: 1.4.
  - Evidence: Local PostgreSQL `db` service accepted the migration upgrade/downgrade; `group`, `meeting.group_id`, indexes, and foreign key appeared on upgrade and were removed on downgrade; Alembic restored revision `n2o3p4q5r6`.

---

## PR 2: Configurable Local-First Embedding Provider, Qdrant Deployment/Client, Content Chunking, Asynchronous Indexing/Deletion Lifecycle

**Goal**: Embedding provider seam (Ollama default, OpenAI selectable by config), Qdrant compose service + client, deterministic chunking, idempotent Celery lifecycle tasks. No retrieval contract needed; failure isolation is the验收 criteria.

- [x] 2.1 Add `EmbeddingProvider` protocol to `backend/app/services/embeddings/__init__.py`:
    - `def embed(self, texts: list[str]) -> list[list[float]]: ...`
    - `def dimension(self) -> int: ...`
  - Acceptance: Protocol compiles, mypy validates, can be implemented by adapters.
  - Dependencies: None.

- [x] 2.2 Implement `OllamaEmbeddingProvider` in `backend/app/services/embeddings/ollama.py`:
    - HTTP POST to `/v1/embeddings` using existing `httpx` client.
    - Configuration: `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`.
    - Assert returned vector dimension matches `EMBEDDING_DIMENSION` (raise on mismatch).
  - Acceptance: Provider initializes, embeds test texts, dimension assertion passes; fails fast on invalid config.
  - Dependencies: 2.1.

- [x] 2.3 Implement `OpenAIEmbeddingProvider` in `backend/app/services/embeddings/openai.py`:
    - HTTP POST to OpenAI-compatible `/v1/embeddings` route.
    - Configuration: `EMBEDDING_BASE_URL` (default OpenAI endpoint), `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`.
    - Same dimension assertion as Ollama.
  - Acceptance: Provider initializes, embeds test texts, dimension assertion passes; API key validation works.
  - Dependencies: 2.1.

- [x] 2.4 Implement `get_embedding_provider()` dispatch in `backend/app/services/embeddings/__init__.py`:
    - Dispatch on `settings.EMBEDDING_PROVIDER` ("ollama" | "openai").
    - Raise on invalid provider or missing API key (when required).
  - Acceptance: Returns correct provider, raises on misconfiguration, no silent fallback.
  - Dependencies: 2.2, 2.3.

- [x] 2.5 Add embedding settings to `backend/app/core/config.py`:
    - `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `EMBEDDING_API_KEY`, `EMBEDDING_MAX_BATCH`, `INDEXING_ENABLED`.
    - `@model_validator(mode="after")` to enforce provider in set and API key presence.
  - Acceptance: Settings compile, validation fails fast on misconfiguration, `INDEXING_ENABLED` is a toggle.
  - Dependencies: 2.1.

- [x] 2.6 Implement `QdrantVectorStoreClient` in `backend/app/services/vector_store.py`:
    - Initialize client from `settings.QDRANT_URL`.
    - `upsert_points(points: list[Point])` → upsert with payload tags (owner_id, group_id, meeting_id, kind, chunk_index, chunk_count).
    - `delete_by_filter(owner_id: int, group_id: int | None, meeting_id: int | None)`.
    - `search_filtered(embedding: list[float], owner_id: int, group_id: int, kinds: list[str] | None, limit: int)` → payload-filtered search.
    - Assert collection exists / create if not (lazy, idempotent).
  - Acceptance: Client connects, upsert/delete/search operations work with payload filters; collection naming uses provider+dimension.
  - Dependencies: 2.1, 2.4, 2.5.

- [x] 2.7 Add Qdrant compose service to `docker-compose.yml` (profile-gated, persistent volume, health check):
    ```yaml
    qdrant:
      image: qdrant/qdrant:latest
      profiles: ["local"]
      ports: ["6333:6333"]
      volumes: ["qdrant_data:/qdrant/storage"]
      healthcheck: {test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/6333 && echo ok"], interval: 10s, timeout: 5s, retries: 5}
    volumes: {qdrant_data: {}}
    ```
  - Acceptance: Service starts, health check passes, volume persists across restarts.
  - Dependencies: 2.6.

- [x] 2.8 Implement content canonicalization in `backend/app/services/embeddings/canonicalize.py`:
    - `str.strip()`, collapse internal whitespace, drop empty chunks.
    - Flatten `structured_output` JSON (sorted keys) to text.
  - Acceptance: Canonicalization is deterministic; re-running produces identical inputs.

- [x] 2.9 Implement chunking in `backend/app/services/embeddings/chunk.py`:
    - Transcript/transliterated: ~512-token windows with 200-token overlap on whitespace.
    - Summary/structured: single point per kind (no chunking).
    - Deterministic `uuid5(NAMESPACE, f"{meeting_id}:{kind}:{chunk_index}")`.
  - Acceptance: Chunking is reproducible, overlap is correct, IDs are stable across runs.

- [x] 2.10 Implement Celery tasks in `backend/app/worker.py`:
    - `stage_embedding(meeting_id)` → canonicalize → chunk → embed → upsert points (only when `meeting.group_id` is set).
    - `delete_meeting_vectors(meeting_id)` → delete by `meeting_id`.
    - `delete_group_vectors(group_id)` → delete by `group_id`.
    - `reindex_group(group_id)` → fetch group meetings, re-run `stage_embedding` per meeting.
  - Acceptance: Tasks are idempotent (deterministic IDs), cleanup is safe, indexing is skipped for ungrouped meetings.

- [x] 2.11 Wire `stage_embedding` into `dispatch_pipeline` and `dispatch_youtube_pipeline` after `stage_extract_intelligence`.
  - Embedding is deliberately not linked to `on_stage_failure` because that handler marks meetings failed; embedding retries/failure must remain isolated from meeting processing status.
  - Acceptance: Pipeline completion remains owned by `stage_extract_intelligence`; embedding retry/failure does not mark the meeting failed or change processing status.

- [x] 2.12 Add assignment triggers to meeting mutations:
    - Group assignment (set `group_id`): enqueue `stage_embedding(meeting_id)`.
    - Unassignment (set `group_id` → NULL): enqueue `delete_meeting_vectors(meeting_id)`.
    - Reassignment: `delete_meeting_vectors` then `stage_embedding`.
    - Source-content change (re-transcribe, re-summarize, summary edit): enqueue `stage_embedding(meeting_id)`.
  - Acceptance: Triggers dispatch tasks, idempotent on re-run.

- [x] 2.13 Add group/meeting deletion triggers:
    - Meeting deletion: enqueue `delete_meeting_vectors` before DB row removal.
    - Group deletion: enqueue `delete_group_vectors`.
  - Acceptance: Cleanup tasks enqueue correctly, orphaned vectors are prevented.

- [x] 2.14 Add retry logic and failure isolation:
    - `autoretry_for` network/timeout, `max_retries=3`, `retry_backoff=True`.
    - Exhaustion logs + Sentry/Logfire, indexing status stale; meeting pipeline never fails.
    - `INDEXING_ENABLED=False` short-circuits all indexing tasks (no-op).
  - Acceptance: Failure isolation tests pass; stack boots with Qdrant unreachable.

- [x] 2.15 Write unit tests for `OllamaEmbeddingProvider` and `OpenAIEmbeddingProvider` (dimension assertion, error paths).
  - Acceptance: Provider tests pass, dimension mismatch raises, API key validation works.

- [x] 2.16 Write contract tests for `QdrantVectorStoreClient` using fake Qdrant or in-memory mock.
  - Acceptance: `upsert`, `delete_by_filter`, `search_filtered` work with payload filters; owner+group are always server-side.

- [x] 2.17 Write lifecycle tests (Celery tasks):
    - Idempotent re-run produces identical IDs.
    - Unassign/delete/group-delete enqueue correct cleanup.
    - `INDEXING_ENABLED=False` no-ops.
  - Acceptance: All lifecycle scenarios tested, failure isolation verified.


---

## PR 2 Evidence (updated 2026-09-18)

Focused strict-TDD evidence for tasks 2.1–2.17:

- RED: `cd backend && uv run pytest app/tests/unit/services/embeddings/test_pr2_contract.py app/tests/unit/test_embedding_lifecycle.py -q` initially failed with `ModuleNotFoundError: No module named 'app.services.embeddings.canonicalize'`.
- GREEN/TRIANGULATE: same command passed with `10 passed, 1 warning`.
- Dependency/settings smoke: `cd backend && uv run python -c "from app.core.config import settings; import qdrant_client; print(settings.EMBEDDING_PROVIDER, settings.EMBEDDING_DIMENSION, qdrant_client.__version__ if hasattr(qdrant_client, '__version__') else 'qdrant-client')"` printed `ollama 768 qdrant-client`.
- No live Qdrant/Ollama/OpenAI call was run or claimed; Qdrant client behavior is covered by fake/contract tests against installed `qdrant-client` model types.
- The task text for 2.11 mentions `link_error=[on_stage_failure.s()]`; implementation intentionally does not attach that handler to `stage_embedding` because `on_stage_failure` marks meetings failed. This preserves the binding PR2 failure-isolation requirement.

## PR 3a / PR 3b: Authorized Retrieval, Reindex, Validation, Observability, Tests, and Docs

**Goal**: PR 3a delivers the authorized internal retrieval/reindex contract and observability. PR 3b delivers leakage/integration tests, documentation, coverage/full-suite verification, and empirical provider/Qdrant validation.

**Chain boundary:** PR 3a contains tasks 3.1–3.4. PR 3b contains tasks 3.5–3.9.

- [x] 3.1 Implement `GroupRetrievalService` in `backend/app/services/retrieval.py`:
    - `search(group_id: int, user_id: int, query: str, limit: int = 10, kinds: list[str] | None = None)`:
        - Resolve group via `GroupService.get_accessible(group_id, user_id)` → 404/403.
        - Embed query via `get_embedding_provider().embed([query])`.
        - Search with hard-coded server-side filters: `owner_id == user_id AND group_id == group_id`.
        - Return ranked results with source meeting references.
    - Raise `503` when Qdrant unreachable or `INDEXING_ENABLED=False`.
  - Acceptance: Authorization is enforced server-side, cross-owner leakage impossible, 503 returned when unavailable.

- [x] 3.2 Add retrieval endpoint to `groups.py`:
    - `POST /groups/{group_id}/search` (body: `{query: str, limit: int = 10, kinds: list[str] | null}`).
    - Returns ranked results with `meeting_id`, `kind`, `chunk_index`, `score`, `text`.
  - Acceptance: Endpoint compiles, authorization tests pass, 503 returned when unavailable.

- [x] 3.3 Add manual reindex endpoint to `groups.py`:
    - `POST /groups/{group_id}/reindex` (202 pattern, mirrors `visual-breakdown`).
    - Enqueues `reindex_group(group_id)` asynchronously.
    - Returns `{status: "accepted", task_id: str}`.
  - Acceptance: Endpoint enqueues task, returns 202, task_id is valid Celery ID.

- [x] 3.4 Add observability instrumentation per existing pattern:
    - Logfire/Sentry events on indexing/reindex/deletion start/complete/error.
    - Optional PostHog event on reindex completion (consistent with existing stage telemetry).
  - Acceptance: Events appear in Logfire/Sentry, no data leakage, task tracing works.
  - Evidence: PR3a focused tests pass with `cd backend && uv run pytest app/tests/unit/services/test_retrieval.py app/tests/unit/api/v1/test_groups.py app/tests/unit/test_embedding_lifecycle.py -q` → `27 passed, 2 warnings`; tests cover authorization-before-provider, server-side owner/group filters, 503 unavailable translation, search/reindex endpoints, no enqueue before authorization, and bounded telemetry without text payloads.

- [x] 3.5 Write retrieval tests:
    - Authorization: owner-authorized `group_id` filter, foreign group returns 403.
    - Leakage tests: cross-owner and cross-group queries return empty (release-blocking acceptance).
    - Failure isolation: Qdrant unreachable returns 503, never partial data.
  - Acceptance: All retrieval scenarios tested, leakage tests pass.
  - Evidence: `cd backend && uv run pytest app/tests/unit/services/test_retrieval_pr3b.py app/tests/unit/api/v1/test_groups.py app/tests/unit/test_embedding_lifecycle_pr3b.py -q` → `24 passed, 2 warnings`; retrieval tests cover foreign-group 403 before provider/vector calls, cross-owner/cross-group fake-store isolation, empty cross-group result, provider/Qdrant 503 with no partial result, and hard `owner_id` + `group_id` + `kind` Qdrant filter construction. The endpoint now authorizes group ownership before calling retrieval as a concrete defect fix proven by the foreign-group endpoint test.

- [x] 3.6 Write integration tests for the full chain:
    - Create group, assign meeting, verify vector is indexed with correct payload tags.
    - Reindex, verify same vectors (idempotent).
    - Unassign meeting, verify vectors are deleted.
    - Delete group, verify all group vectors are deleted.
  - Acceptance: Full lifecycle integration test passes, no orphaned vectors.
  - Evidence: `cd backend && uv run pytest app/tests/unit/test_embedding_lifecycle_pr3b.py -q` passed as part of the focused PR3b suite; bounded fake vector-store lifecycle covers assignment/indexing payload tags, deterministic re-run IDs, `reindex_group` scheduling assigned meetings, meeting unassignment cleanup, and group deletion cleanup without app.main/MinIO/live provider calls.

- [x] 3.7 Write documentation:
    - `docs/group-embedding-overview.md`: Group entity, indexing lifecycle, retrieval contract.
    - `docs/group-api.md`: CRUD, assignment, search, reindex endpoints.
    - `docs/embedding-configuration.md`: Provider settings (Ollama/OpenAI), dimension strategy, rollback.
    - Update `README.md` if needed.
  - Acceptance: Documentation is readable, configuration examples are accurate, rollback procedure is clear.
  - Evidence: Added the three docs in English and a concise README feature link. Docs cover group ownership, `Meeting.group_id`, automatic Celery lifecycle, Qdrant payload isolation, provider settings/dimensions, search/reindex contracts, rollback, and MCP/chat deferral.

- [x] 3.8 Run full test suite and verify coverage:
    - `pytest backend/app/tests/ -v --cov=backend/app/services --cov-report=term-missing`.
    - Target: 70% coverage (per `openspec/config.yaml`).
  - Acceptance: Coverage targets met, all tests pass.
  - Evidence: Added missing backend dev dependency `pytest-cov` with `cd backend && uv add --dev pytest-cov`, then added focused coverage tests for the previously under-tested service modules without changing production behavior. Exact backend uv command `cd backend && uv run pytest app/tests/ -q --cov=app/services --cov-report=term-missing` passed with `265 passed, 3 warnings` and total `70%` services coverage.

- [x] 3.9 Validate Qdrant/Ollama assumptions empirically (as recorded in design §15):
    - Spike Qdrant Python client, confirm collection-create API and payload-filter syntax.
    - Verify Ollama `/v1/embeddings` route exists and returns correct dimension.
    - Measure local Ollama embedding-model RAM/CPU on VPS.
  - Acceptance: Assumptions confirmed or documented as deviations; any API drift is captured.
  - Evidence: Local Qdrant probe with a temporary collection confirmed create/upsert/query payload filters using current `qdrant-client` `query_points` (`QDRANT_OK ... hit_count=1 miss_count=0`); an initial probe captured API drift because `QdrantClient.search` is absent in the installed client. Local Ollama probe to configured `http://host.docker.internal:11434/v1/embeddings` for `nomic-embed-text` timed out, so route, dimension, and RAM/CPU could not be confirmed in this environment and are documented as validation limits.

---

## Validation Gates

All slices must pass the following before merge:

- [x] All tests pass (unit, integration, leakage). Verified: 265 passed; focused leakage/lifecycle suites pass.
- [x] Authorization is enforced server-side in retrieval contract (no client-supplied-only filters). Verified: endpoint/service authorize before provider/vector calls and derive owner/group filters.
- [x] Cross-owner and cross-group leakage tests pass (release-blocking acceptance). Verified: focused tests and live Qdrant payload-filter probe.
- [x] Migration up/down runs without error. Verified: live Postgres upgrade/downgrade/upgrade cycle restored the prior revision.
- [x] Qdrant compose service starts and responds to health check. Verified: local Qdrant container healthy and `/readyz` succeeds.
- [x] Embedding provider dimension assertion passes (or fails fast on mismatch). Verified: provider assertions and live Qdrant wrong-dimension rejection.
- [x] `INDEXING_ENABLED=False` short-circuits indexing without affecting meetings. Verified: all indexing/cleanup tasks no-op without vector-store calls.

---

## Rollback Procedure

- **PR 1 rollback**: Drop `groups` router registration, remove `GroupService` + schemas, downgrade migration.
- **PR 2 rollback**: Disable indexing via `INDEXING_ENABLED=False`; remove `groups.py` assignment triggers, vector client, Celery tasks; remove Qdrant compose service.
- **PR 3 rollback**: Remove retrieval endpoint, reindex endpoint, retrieval service.

All rollbacks are safe and independent.
