---
status: proposed
executive_summary: >-
  Introduce a user-owned Group entity (owner-scoped CRUD following the
  TemplateService/templates.py pattern) with a nullable group_id foreign key on
  Meeting, and build group-scoped semantic indexing: a configurable embedding
  provider (local/Ollama first, OpenAI selectable later by configuration via
  an adapter boundary) writes vectors into a reintroduced Qdrant service,
  driven automatically by asynchronous Celery tasks on group assignment and
  source-content changes, with a manual reindex operation for recovery. An
  internal group-scoped retrieval contract/API with owner authorization and
  group_id filtering is the deliverable seam for future agent access; the MCP
  server, in-product chat, and any generic artifact model are explicitly out of
  scope. External provider details (Qdrant client/collection contract, Ollama
  embeddings endpoint) are recorded as implementation assumptions requiring
  validation during design/apply because web research was unavailable.
artifacts:
  - path: openspec/changes/user-owned-groups-embeddings/proposal.md
    store: openspec
    type: proposal
next_recommended: sdd-design
risks:
  - The optional research child could not access web tools in this runtime, so Qdrant client version/API, collection/payload-filter contract, embedding dimensions, and the Ollama embeddings endpoint contract are unvalidated assumptions that must be confirmed empirically during design/apply.
  - Provider switchability (Ollama now, OpenAI later by configuration) implies vector-dimension coupling; a collection/dimension strategy must be designed or switching will break the index.
  - Expected change size (model + migration + service + endpoints + Celery tasks + provider adapters + vector client + compose + tests) exceeds the 400-line review budget; the ask-on-risk delivery gate must produce a chain/scope decision before apply.
  - Indexing lifecycle must be idempotent and failure-isolated: embedding failures must not break the existing meeting pipeline, and orphaned vectors on unassign/delete must not leak across owners or groups.
skill_resolution:
  mode: none
  injected_skill_paths: []
---

# Proposal: User-Owned Groups + Group-Scoped Embeddings

## Intent

Users currently have a flat, unorganized set of meetings. This change introduces
user-owned **Groups** as the organizational unit and builds **group-scoped
semantic indexing** over each group's meeting content (transcript/summary text
derived from the associated audio/video), so that authorized, group-filtered
retrieval becomes possible inside the backend. The deliverable of this slice is
the group entity, the asynchronous indexing lifecycle, and an internal
group-scoped retrieval contract — the seam a future MCP server or in-product
chat can consume. No chat UI, no MCP server, and no generic artifact model are
built here.

## User Impact

- Users can create, update, and delete groups they own, and assign existing
  meetings to a group (or remove them), through a documented backend API.
- Content that belongs to a group is indexed automatically in the background;
  users pay no per-item action cost for indexing and see no added latency in
  meeting workflows.
- Group content becomes semantically retrievable by the owner (and, in the
  future, by authorized agents) through a group-scoped retrieval API — the
  first step toward ROADMAP #14 (AI chat over meetings) without committing to
  the chat UX now.
- A manual reindex operation gives the owner a recovery path when the index is
  lost, stale, or corrupted.

## Confirmed Product Decisions

These decisions are parent-confirmed for this change and are binding inputs;
the proposal encodes them, it does not re-litigate them.

1. **Configurable embedding provider, local/Ollama first.** Embedding
   generation goes through an adapter boundary. The default provider is a
   local Ollama-compatible endpoint; OpenAI remains selectable later purely by
   configuration, with no code change.
2. **Content = existing Meeting rows.** The first slice groups existing
   `Meeting` rows; their associated audio/video and transcript/summary data are
   the indexed content. No generic artifact model is introduced now.
3. **Automatic asynchronous lifecycle + manual recovery.** Indexing and
   reindexing happen automatically and asynchronously on group assignment and
   on source-content changes (e.g., re-transcription, summary edit), plus a
   manual reindex operation for recovery.
4. **Internal retrieval contract, agent surfaces deferred.** Implement an
   internal group-scoped retrieval contract/API with owner authorization and
   `group_id` filtering. The actual MCP server and in-product chat are separate
   future features.

## Goals

- An owner-scoped `Group` model and CRUD API consistent with the established
  owner-scoped service/endpoint pattern (`TemplateService` / `templates.py`),
  with explicit 403/404 handling and no cross-owner access.
- A nullable `group_id` foreign key on `Meeting` (with back-reference) plus an
  Alembic migration, leaving all existing meetings ungrouped and fully
  functional.
- A provider-adapter embedding seam: `EmbeddingProvider` boundary with an
  OpenAI-compatible adapter pointed by configuration at a local Ollama
  endpoint by default; provider, base URL, model, and dimensions are settings,
  not code.
- A reintroduced Qdrant compose service with persistent volume and backend
  settings, following the stack posture already declared in
  `openspec/config.yaml`.
- Asynchronous, idempotent indexing/reindexing/vector-cleanup driven by Celery
  tasks, plus an authorized manual reindex endpoint.
- An internal retrieval service + endpoint that enforces owner authorization
  and `group_id` filtering, returning ranked content matches for future agent
  consumption.
- Failure isolation: embedding/Qdrant problems never break the existing
  meeting pipeline.

## Non-Goals

- **MCP server and in-product chat** (ROADMAP #14's UX) — separate future
  features; this change ships only the internal retrieval seam they will call.
- **Generic artifact model** (`Video`/`Audio`/`File` entities) — meetings and
  their `file_path`-referenced media remain the content representation.
- **Multi-user group sharing/membership** — groups are single-owner in this
  slice, matching the current `owner_id` architecture; a membership model is a
  future decision (see Open Issues).
- **Frontend group-management UI** — recommended as a follow-up change; the
  first slice is backend + API (see Open Issues for confirmation).
- **Multimodal (audio/video-native) embeddings** — the embeddable
  representation in this slice is meeting transcript/summary text; media files
  are the source of that content, not separately embedded vectors (see Open
  Issues for confirmation of this interpretation).
- **Vector search over ungrouped meetings**, global/cross-group search, hybrid
  long-context strategies, or ranking-quality tuning beyond baseline semantic
  retrieval.

## Capabilities

### New Capabilities
- `user-owned-groups-embeddings`: Owner-scoped group management, group-scoped
  semantic indexing with a configurable embedding provider and asynchronous
  lifecycle (automatic + manual recovery), and an internal group-scoped,
  authorization-enforcing retrieval contract.

### Modified Capabilities
- None. Existing published capabilities (`multimodal-summary`,
  `video-transcription-e2e`) are unaffected; the `Meeting` model gains a
  nullable column that no existing spec behavior depends on.

## Scope

### In Scope
- `Group` SQLModel model + `GroupCreate`/`GroupRead`/`GroupUpdate` schemas,
  owner-scoped `GroupService` (list/create/get/update/delete with explicit
  403/404), new `groups.py` router registered like `templates.py`.
- Nullable `Meeting.group_id` FK + back-reference, Alembic migration, and
  meeting endpoints accepting/returning `group_id` plus a group-assignment
  operation that triggers asynchronous indexing.
- `EmbeddingProvider` adapter boundary + OpenAI-compatible adapter configured
  for a local Ollama endpoint by default; OpenAI selectable by configuration;
  new `EMBEDDING_*` and `QDRANT_*` settings in `Settings` with explicit
  validation failures (no silent fallbacks).
- Qdrant compose service + persistent volume; backend vector-store client
  (upsert/delete/payload-filtered search) with `owner_id`, `group_id`,
  `meeting_id`, and content-kind payload tags.
- Celery tasks appended after summarize/intelligence in the dispatch chain and
  triggered on assignment: index on join, reindex on source-content change,
  delete vectors on unassign/meeting deletion/group deletion — all idempotent.
- Authorized manual reindex endpoint (202 async pattern, mirroring
  `visual-breakdown`) for group-level index recovery.
- Internal retrieval contract/API: owner-authorized, `group_id`-filtered
  semantic search returning ranked matches with source meeting references.
- Failure isolation: embedding/indexing failures are recorded and retried or
  surfaced without failing the meeting pipeline; the stack boots with or
  without Qdrant reachable.
- Repository pattern conformance tests (owner scoping, authorization,
  idempotency, lifecycle transitions).

### Out of Scope
- MCP server implementation, in-product chat, any retrieval UX.
- Generic artifact models, group file storage changes (media continues to live
  in MinIO/S3 under existing paths via `file_path`).
- Multi-user sharing, permissions beyond owner scope.
- Frontend UI work (pending Open Issues confirmation, recommended follow-up).
- Embedding-model quality benchmarking and provider cost analysis beyond
  recording what is configurable.

## Approach

1. **Data model.** Add `Group` (owner-scoped, name, timestamps) following the
   `SummaryTemplate` shape; add nullable `Meeting.group_id` FK with
   back-reference; one Alembic migration consistent with the 18 existing
   migrations. All existing rows remain `group_id = NULL` and untouched.
2. **Service/endpoint layer.** `GroupService(BaseService)` mirrors
   `TemplateService` discipline (`list_for_user`, create, `get_accessible`,
   update, delete with explicit 403/404); `groups.py` mirrors `templates.py`
   with `Depends(deps.get_current_active_user)`. Meeting endpoints gain
   `group_id` read/write and an assignment operation.
3. **Embedding provider seam.** A `EmbeddingProvider` protocol + registry
   mirroring the existing provider-abstraction precedent
   (`StorageProvider`, transcription providers): an OpenAI-compatible client
   adapter whose provider/base-URL/model/credentials come entirely from
   `Settings` (default: local Ollama-compatible endpoint). Selecting OpenAI
   later is a configuration change. Invalid configuration fails explicitly
   at startup/use — never silently switches providers.
4. **Vector store.** Reintroduce the Qdrant compose service + volume (the
   pattern previously shipped in `019-vps-lift-shift` and removed by ROADMAP
   #18) and `QDRANT_URL`/collection settings. A thin backend client encapsulates
   upsert, delete-by-filter, and payload-filtered search; points are keyed
   deterministically per meeting (chunking strategy is a design decision — see
   Open Issues) and tagged with `owner_id`, `group_id`, `meeting_id`,
   content kind.
5. **Indexing lifecycle (decision 3).** New Celery task(s) in the existing
   `stage_*` style, appended to `dispatch_pipeline` after
   `stage_summarize`/`stage_extract_intelligence`, plus task dispatch on group
   assignment and on source-content mutations (re-transcribe, re-summarize,
   summary edit). Vector deletion accompanies unassignment, meeting deletion,
   and group deletion. All operations are idempotent upserts/deletes so manual
   reindex is always safe. Embedding failures isolate to the indexing status;
   they never mark the meeting pipeline failed.
6. **Retrieval contract (decision 4).** An internal retrieval service + owner-
   scoped API endpoint that embeds the query, filters by `owner_id` and
   `group_id`, and returns ranked matches referencing source meetings. This is
   the stable seam a future MCP server calls; it is deliberately internal
   (no public chat surface).
7. **Recovery.** A manual, authorized reindex endpoint (per group; per-meeting
   optional — see Open Issues) that re-runs indexing over the group's meetings
   asynchronously (202), restoring the index after loss or corruption.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/models/base.py`, `models/__init__.py` | Modified | `Group` model, group schemas, `Meeting.group_id` FK + back-reference. |
| `backend/alembic/versions/` | Modified | New migration: `group` table + nullable `meeting.group_id`. |
| `backend/app/services/group.py` | New | Owner-scoped `GroupService` mirroring `TemplateService`. |
| `backend/app/services/embeddings/` (or equivalent) | New | `EmbeddingProvider` adapter boundary, Ollama-compatible default, config-selected OpenAI. |
| `backend/app/services/vector_store.py` (or equivalent) | New | Qdrant client wrapper: upsert, delete-by-filter, payload-filtered search. |
| `backend/app/services/retrieval.py` (or equivalent) | New | Group-scoped retrieval contract enforcing authorization + `group_id` filter. |
| `backend/app/api/v1/endpoints/groups.py` | New | Owner-scoped group CRUD, assignment surface, manual reindex (202), retrieval endpoint. |
| `backend/app/api/v1/endpoints/meetings.py` | Modified | Accept/return `group_id`; trigger async indexing/cleanup on mutation. |
| `backend/app/worker.py` | Modified | New `stage_embedding`-style tasks wired into `dispatch_pipeline`; idempotent index/reindex/delete. |
| `backend/app/core/config.py` | Modified | `EMBEDDING_*` and `QDRANT_*` settings with explicit validation. |
| `docker-compose.yml` | Modified | Reintroduce `qdrant` service + persistent volume; wire settings into `api`/`worker`. |
| `backend/pyproject.toml` | Modified | Qdrant client dependency (embedding client is HTTP-based). |
| `backend/tests/` | Modified | Authorization, lifecycle, idempotency, and failure-isolation coverage. |
| `frontend-2/` | Deferred | Group-management UI recommended as follow-up (Open Issues). |

## Implementation Assumptions Requiring Validation

The optional research child could not access web tools in this runtime, so no
current external citations exist. The following are **assumptions to validate
empirically during design/apply**, not established facts:

- **Qdrant client/library:** the Python client choice, version, collection
  schema API, payload-filter syntax, and upsert/delete-by-filter semantics
  must be verified against the actual client at design time. Historical
  repository evidence (specs/archive/019-vps-lift-shift) shows
  `qdrant/qdrant:latest` with a `qdrant_data` volume and health check worked
  in this compose stack.
- **Ollama embeddings contract:** whether the local Ollama endpoint exposes an
  OpenAI-compatible `/v1/embeddings` route usable by the standard client, and
  which embedding models are available, must be validated in the dev
  environment. If incompatible, the adapter boundary absorbs the difference
  (that is precisely its purpose).
- **Embedding dimensions:** the default model's vector dimension (and any
  OpenAI alternative's) must be confirmed before fixing the collection schema;
  dimension mismatch on provider switch is a designed-for risk (see Risks).
- **Resource envelope:** local Ollama embedding-model RAM/CPU footprint on the
  VPS (12 GB RAM, CPU-only worker) must be measured during apply; Qdrant idle
  cost was previously estimated around ~512 MB RAM (repository record, to
  re-verify).

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Unvalidated external contracts (Qdrant client API, Ollama embeddings route, dimensions) force design/apply changes | High | Adapter boundary isolates provider specifics; validate empirically in design with a spike; record deltas as they surface. |
| Provider switch (Ollama → OpenAI) changes vector dimensions and breaks the collection | Medium | Encode dimension in settings/collection naming; design a recreate-or-migrate-on-switch policy; manual reindex is the recovery path. |
| Indexing lifecycle drift: orphaned or duplicate vectors on assignment/reassignment/deletion | Medium | Deterministic point IDs, idempotent upsert/delete-by-filter, owner+group payload tags on every point, lifecycle tests. |
| Embedding failure degrades the existing meeting pipeline | Medium | Failure-isolated stage: indexing status separate from meeting status; retries/logfire/sentry instrumentation per existing stage pattern; stack boots without Qdrant. |
| Cross-owner/cross-group data leakage through retrieval | Low impact, must not ship | Hard authorization in retrieval contract (`owner_id` + `group_id` filters enforced server-side, never client-supplied-only); leakage tests are release-blocking acceptance criteria. |
| Scope creep toward chat/MCP or a generic artifact model | Medium | Explicit non-goals; this proposal's scope is the group entity, indexing lifecycle, and retrieval contract only. |
| Review budget: estimated well above 400 changed lines | High | ask-on-risk gate: present chain candidates to the user before apply (e.g., group entity+CRUD / indexing infrastructure+lifecycle / retrieval contract); do not auto-chain. |
| Local embedding resource cost on VPS | Medium | Measure in apply; keep provider configurable so a hosted provider is a config-only escape hatch. |

## Rollback Plan

- Disable indexing via settings (feature toggle / unset `QDRANT_URL`) —
  meetings and groups remain functional; retrieval returns an explicit
  unavailable error rather than partial data.
- Remove `qdrant` compose service + volume; orphaned vectors are inert.
- Remove groups router registration, embedding/vector/retrieval services, and
  worker task wiring; the `dispatch_pipeline` returns to its current shape.
- Migration reversal is safe: `meeting.group_id` is nullable, so dropping it
  loses only grouping assignments, not meeting data. Group rows can be dropped
  with no cascading effect on meetings.

## Dependencies

- Qdrant client library (version selected and validated at design time).
- A running local Ollama endpoint with a usable embedding model (default
  provider; validated during design/apply).
- Existing infrastructure unchanged: PostgreSQL (SQLModel), Redis/Celery,
  MinIO/S3, first-party JWT auth (`deps.py`), Alembic.

## Success Criteria (Acceptance Direction)

- [ ] Owner-scoped group CRUD works with explicit 403/404 on foreign or
      unauthorized access; no cross-owner reads/writes anywhere in the surface.
- [ ] Assigning a meeting to a group triggers asynchronous indexing; vectors
      carry `owner_id` + `group_id` + `meeting_id` payload tags.
- [ ] Source-content change (re-transcribe, re-summarize, summary edit)
      triggers reindexing; unassignment, meeting deletion, and group deletion
      remove vectors — all idempotently (re-running is safe).
- [ ] The manual reindex endpoint (202, owner-authorized) restores a group's
      index after simulated loss.
- [ ] The internal retrieval contract returns only results from the
      authorized owner and requested `group_id`; cross-owner and cross-group
      leakage tests pass (release-blocking).
- [ ] Provider selection is configuration-only: default Ollama-compatible
      endpoint works; switching settings to an OpenAI-compatible endpoint uses
      the same adapter with no code change; invalid configuration fails
      explicitly.
- [ ] Qdrant runs in compose with a persistent volume and a health check;
      the full stack boots and existing transcription/summarization flows are
      unaffected when Qdrant or the embedding provider is unreachable.
- [ ] Existing repository patterns are followed verbatim where applicable:
      `BaseService` owner scoping, `templates.py` endpoint discipline,
      `stage_*` Celery conventions, provider-protocol seam, 202 async
      operations.

## Open Issues

These are decision gaps for the spec/design phases; none block this proposal.

1. **Frontend UI:** is minimal group-management UI (create group, assign
   meetings) required in the first slice, or is backend+API sufficient with UI
   as a follow-up change? Recommended default: follow-up change.
2. **Media interpretation:** confirm that "associated audio/video … are the
   indexed content" means media is the *source* of indexed text (transcript/
   summary), not that raw audio/video is separately embedded in this slice.
   Recommended default: text-only embeddings now; multimodal embeddings are a
   future capability.
3. **Chunking strategy:** one vector per meeting versus chunked points per
   transcript — a retrieval-quality vs. index-size design decision to settle
   in design (point IDs must be deterministic either way).
4. **Manual reindex granularity:** group-level only, or also per-meeting?
   Recommended default: group-level in this slice.
5. **Dimension-switch policy:** exact behavior when configuration changes the
   provider/dimension (recreate collection + manual reindex vs. per-provider
   collections) — design decision.
6. **Delivery shape:** estimated size exceeds the 400-line review budget;
   the ask-on-risk gate must present chain candidates to the user before
   apply. Candidate split: (a) group model + CRUD + migration, (b) embedding
   provider + Qdrant + lifecycle tasks, (c) retrieval contract + reindex.
7. **Multi-user sharing:** owner-only groups are assumed for this slice;
   membership/sharing remains a future change requiring its own authorization
   design.