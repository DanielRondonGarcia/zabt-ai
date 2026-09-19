---
status: proposed
store: openspec
type: design
change: user-owned-groups-embeddings
---

# Design: User-Owned Groups + Group-Scoped Embeddings

## 1. Summary

Introduce a single-owner `Group` entity (mirroring `TemplateService` /
`templates.py`), a nullable `group_id` foreign key on `Meeting`, and a
group-scoped semantic index in a reintroduced Qdrant service. Embedding
generation flows through an `EmbeddingProvider` adapter boundary that is
configuration-only (Ollama-compatible local endpoint by default, OpenAI-compatible
later). Indexing is asynchronous and idempotent, driven by Celery tasks appended
to the existing dispatch chain and triggered on group assignment/source-content
mutation, with a manual group-level reindex for recovery. The deliverable seam is
an internal, owner-authorized, `group_id`-filtered retrieval contract. MCP server,
in-product chat, and generic artifact models are out of scope.

This document settles the open issues from `proposal.md` (§13) and records the
unvalidated Qdrant/Ollama assumptions as explicit validation steps (§12).

## 2. Binding Decisions (carried from proposal)

1. Configurable embedding provider, local/Ollama first (adapter boundary).
2. Content = existing `Meeting` rows; transcript/summary text is the indexed
   content (media is the *source*, not separately embedded).
3. Automatic async indexing + manual reindex recovery.
4. Internal retrieval contract only; MCP/chat deferred.

## 3. Data Model & Migration

### 3.1 `Group` model (`backend/app/models/base.py`)

Add, following the `SummaryTemplate` shape (single-owner, no membership table):

```python
class Group(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

Read/list schemas (`GroupRead`, `GroupListItem`) mirror `SummaryTemplateRead` /
`SummaryTemplateListItem`. No membership model in this slice (single-owner,
deferred — see proposal Open Issue #7).

### 3.2 `Meeting.group_id`

Add nullable FK + back-reference on `Meeting`:

```python
group_id: Optional[int] = Field(default=None, foreign_key="group.id", index=True)
```

A `Relationship` back-reference is optional; indexing code reads `meeting.group_id`
directly. Existing rows stay `group_id = NULL` and are unaffected.

### 3.3 Migration

One new Alembic migration, `backend/alembic/versions/<rev>_add_group_and_meeting_group_id.py`:
- Create `group` table (id, name, description, owner_id FK → user.id with index,
  created_at, updated_at).
- `ALTER TABLE meeting ADD COLUMN group_id INTEGER NULL REFERENCES "group"(id)` +
  index on `meeting.group_id`.
- Downgrade drops the column then the table. Both are safe: `group_id` is nullable
  and no meeting data is lost.

Reuse the mixed naming convention already present in the 18 existing migrations.

## 4. Owner Authorization (service + endpoint layer)

`GroupService(BaseService)` in `backend/app/services/group.py` mirrors
`TemplateService` verbatim:

- `list_for_user(user_id)` → `select(Group).where(Group.owner_id == user_id)`.
- `get_accessible(group_id, user_id)` → 404 if missing, 403 if
  `owner_id != user_id`.
- `create(user_id, name, description)` → `save()`.
- `update(group_id, user_id, ...)` → 404/403 then mutate + commit.
- `delete(group_id, user_id)` → 404/403 then delete; **also dispatch async vector
  cleanup** (see §7).

`groups.py` router (`backend/app/api/v1/endpoints/groups.py`) mirrors
`templates.py`: `APIRouter`, `Depends(deps.get_current_active_user)`, explicit
status codes. Registered in `backend/app/api/api.py` as
`api_router.include_router(groups.router, prefix="/groups", tags=["groups"])`.

Authorization invariants (release-blocking):
- Every query to Qdrant carries **both** `owner_id` and `group_id` payload filters
  that are derived server-side from the authenticated user + resolved group, never
  trusted from the client. The group must be resolved through
  `get_accessible(group_id, current_user.id)` before any vector operation, so a
  foreign group cannot be addressed at all.

## 5. Qdrant Collection / Payload / Index Strategy

### 5.1 Compose service

Reintroduce Qdrant in `docker-compose.yml` (profile-gated like `db`/`minio`,
matching the prior `019-vps-lift-shift` shape) with a persistent volume and health
check:

```yaml
  qdrant:
    image: qdrant/qdrant:latest
    profiles: ["local"]
    ports: ["6333:6333"]
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/6333 && echo ok"]
      interval: 10s
      timeout: 5s
      retries: 5
```

Add `qdrant_data:` to the top-level `volumes:` block. Wire `QDRANT_URL` into the
`api` and `worker` environments.

### 5.2 Collection strategy (dimension coupling)

The provider-switch dimension risk (proposal Risk #2) is solved by **per-dimension
collection naming** plus a single active collection pointer:

- Collection name = `meeting_embeddings_{provider}_{dim}`, e.g.
  `meeting_embeddings_ollama_768`. The active collection is derived at runtime
  from the configured provider + its declared vector dimension, so switching
  providers/dimensions selects a new collection automatically without corrupting
  the old one.
- `EMBEDDING_DIMENSION` is an explicit setting (default 768 for a local Ollama
  `nomic-embed-text`-class model). When the provider adapter initializes, it
  asserts the actual returned vector length matches the configured dimension and
  raises on mismatch (§6.2).
- **Switch policy:** switching provider/dimension selects a fresh collection; the
  old collection is retained (inert). The group's index is restored into the new
  collection via the manual reindex endpoint (§7.4). No automatic cross-collection
  migration in this slice. A `collection_exists`/`create_collection` is performed
  lazily on first use (idempotent).

### 5.3 Vector config

- Distance: **Cosine** (default and appropriate for normalized text embeddings).
- On-disk payload index for `owner_id`, `group_id`, `meeting_id`, `kind`
  (all keyword/integer payload filters used in retrieval).

### 5.4 Point payload schema

Every point carries:

```json
{
  "owner_id": 42,
  "group_id": 7,
  "meeting_id": 123,
  "kind": "summary",       // "transcript" | "summary" | "transliterated" | "structured"
  "chunk_index": 0,
  "chunk_count": 3,
  "source_type": "upload", // "upload" | "youtube"
  "model": "nomic-embed-text"
}
```

## 6. Provider Interface & Model/Dimension Configuration

### 6.1 `EmbeddingProvider` protocol + registry

New module `backend/app/services/embeddings/` with:

```python
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...

def get_embedding_provider() -> EmbeddingProvider:
    # dispatch on settings.EMBEDDING_PROVIDER; raises on invalid config
```

`OllamaEmbeddingProvider` and `OpenAIEmbeddingProvider` are thin HTTP adapters
using the existing `httpx` dependency. Both target an OpenAI-compatible
`POST /v1/embeddings` route; the default points `EMBEDDING_BASE_URL` at the local
Ollama endpoint. This mirrors the existing `StorageProvider` /
`get_provider`/`ProviderName` seam already in the codebase.

### 6.2 Settings (`backend/app/core/config.py`)

Add explicit, validated settings with **no silent fallback**:

```
EMBEDDING_PROVIDER: str = "ollama"          # "ollama" | "openai"
EMBEDDING_BASE_URL: str = "http://host.docker.internal:11434/v1"
EMBEDDING_MODEL: str = "nomic-embed-text"
EMBEDDING_DIMENSION: int = 768
EMBEDDING_API_KEY: str = ""                 # only required when provider=openai
EMBEDDING_MAX_BATCH: int = 32
QDRANT_URL: str = "http://qdrant:6333"
QDRANT_COLLECTION_PREFIX: str = "meeting_embeddings"
INDEXING_ENABLED: bool = True               # feature toggle / rollback switch
```

Validation (`@model_validator(mode="after")`):
- `EMBEDDING_PROVIDER in {"ollama", "openai"}`.
- `openai` requires `EMBEDDING_API_KEY` (or `OPENAI_API_KEY`) non-empty.
- `EMBEDDING_DIMENSION > 0`.
- `EMBEDDING_BASE_URL` non-empty; no provider silently falls back.

## 7. Chunking & Canonicalization

### 7.1 Content selection

Indexed content kinds (all already on `Meeting`), in priority order for retrieval
weighting:

1. `summary_text` (or `original_summary_text` if summary not edited) — highest
   signal density.
2. `transcript_text` — chunked (see §7.2).
3. `transliterated_text` — chunked, indexed only when present (non-English
   workflows).
4. `structured_output` (JSONB) — flattened key/value text; indexed only when
   `structured_output_status == "completed"`.

Each content kind is emitted as one or more points tagged with `kind`.

### 7.2 Chunking

- **Transcript/transliterated text**: chunked into ~512-token overlapping windows
  (200-token overlap) on whitespace, so long transcripts produce multiple points.
- **Summary / structured output**: single point per kind (not chunked).
- **Deterministic point IDs**: `uuid5(NAMESPACE, f"{meeting_id}:{kind}:{chunk_index}")`.
  Re-running produces identical IDs, so upsert is naturally idempotent and
  re-chunking replaces prior points for that meeting deterministically.

### 7.3 Canonicalization

- `str.strip()`; collapse internal whitespace to single spaces; drop empty/whitespace-
  only chunks.
- Normalize `structured_output` via a stable JSON flatten (sorted keys) to text.
- Embedding is always over the canonicalized text, so reindex is reproducible.

## 8. Celery Task Lifecycle (idempotency / retry / deletion)

New tasks in `backend/app/worker.py`, following the `stage_*` convention. All are
**failure-isolated**: they touch only indexing status, never `meeting.status` or the
pipeline result.

### 8.1 Tasks

- `stage_embedding(meeting_id)` — canonicalize → chunk → `embed()` → upsert all
  points for the meeting into the active collection. Idempotent (deterministic IDs).
  Only runs when `meeting.group_id` is set (ungrouped meetings are skipped).
- `delete_meeting_vectors(meeting_id)` — `delete` by filter `meeting_id == X`.
- `delete_group_vectors(group_id)` — `delete` by filter `group_id == X`.
- `reindex_group(group_id)` — fetch group's meetings, re-run `stage_embedding` per
  meeting. Used by the manual reindex endpoint.

### 8.2 Pipeline wiring

Append `stage_embedding` to `dispatch_pipeline` and `dispatch_youtube_pipeline`
after `stage_extract_intelligence` (final content is available only after
summary/intelligence complete). `link_error=[on_stage_failure.s()]` is applied so an
embedding failure never fails the chain — it is reported and the meeting still
completes via `mark_completed`.

### 8.3 Mutation triggers (idempotent dispatch)

- **Group assignment** (`meeting.group_id` set): enqueue `stage_embedding(meeting_id)`.
- **Unassignment** (`group_id` → NULL): enqueue `delete_meeting_vectors(meeting_id)`.
- **Re-assignment** to a different group: `delete_meeting_vectors` then
  `stage_embedding` (single upsert overwrites via deterministic IDs, but explicit
  delete-by-filter first avoids leaking the stale `group_id` tag).
- **Source-content change** (re-transcribe, re-summarize, summary edit
  `PATCH /meetings/{id}/summary`, restore): enqueue `stage_embedding(meeting_id)`.
  Hook via `MeetingService.update_field`/`save_summary` and the summary endpoints.
- **Meeting deletion** (`delete_meeting`): enqueue `delete_meeting_vectors(meeting_id)`
  before the DB row is removed (read `meeting_id` first).
- **Group deletion**: enqueue `delete_group_vectors(group_id)`.

### 8.4 Retry & failure isolation

- `autoretry_for` network/timeout exceptions with `max_retries=3`,
  `retry_backoff=True` (consistent with existing transcription retry posture).
- On exhaustion, log + emit Sentry/Logfire and leave indexing status stale; the
  meeting pipeline is never marked failed.
- `INDEXING_ENABLED=False` short-circuits all indexing/cleanup tasks (no-op) — this
  is the rollback switch (§11).

## 9. Retrieval API Contract (internal)

`backend/app/services/retrieval.py` + endpoint in `groups.py` (internal seam, no
public chat UX).

### 9.1 Endpoint

```
POST /groups/{group_id}/search
Authorization: Bearer <JWT>
Body: { "query": str, "limit": int = 10, "kinds": list[str] | null }
```

Response (ranked, referencing source meetings):

```json
{
  "group_id": 7,
  "results": [
    {
      "meeting_id": 123,
      "kind": "summary",
      "chunk_index": 0,
      "score": 0.87,
      "text": "..."
    }
  ]
}
```

### 9.2 Authorization & filtering

1. Resolve group via `GroupService.get_accessible(group_id, current_user.id)` →
   404/403.
2. Embed the query via `get_embedding_provider().embed([query])`.
3. Search active collection with payload filters **hard-coded server-side**:
   `owner_id == current_user.id AND group_id == group_id`, plus optional `kind` in
   `kinds`.
4. Return ranked results. If Qdrant is unreachable or `INDEXING_ENABLED=False`,
   return `503` with `{"detail": "retrieval unavailable"}` — never partial or
   cross-owner data.

## 10. Deployment & Settings

- `docker-compose.yml`: add `qdrant` service + `qdrant_data` volume (§5.1); wire
  `QDRANT_URL`, `EMBEDDING_*` into `api` and `worker`.
- `backend/pyproject.toml`: add `qdrant-client` dependency. Embedding uses existing
  `httpx` (no fastembed/sentence-transformers — local embedding runs via the Ollama
  endpoint, not in-process).
- `.env`: add `QDRANT_URL`, `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL`,
  `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `INDEXING_ENABLED`.
- `Settings` validation fails fast on invalid config (§6.2).

## 11. Observability

- Logfire/Sentry instrumentation per existing `stage_*` pattern (Logfire already
  instruments Celery via `logfire.instrument_celery()` in `main.py`).
- Indexing status is recorded out-of-band (log events + a lightweight in-memory /
  per-meeting status attribute if needed) — **not** by repurposing
  `meeting.status`. Embedding/Qdrant errors surface via Sentry, never as pipeline
  failures.
- Optional PostHog event on reindex completion (consistent with existing
  per-stage telemetry).

## 12. Tests

- **Unit (services):** `GroupService` owner scoping (403/404), `get_accessible`
  foreign-group denial; `EmbeddingProvider` registry dispatch + dimension-mismatch
  raise; chunking/canonicalization determinism; deterministic point-ID stability.
- **Vector store (fake/contract):** upsert/delete-by-filter/search payload-filter
  wiring — assert `owner_id`+`group_id` are always passed server-side.
- **Lifecycle (Celery):** idempotent re-run produces same IDs; unassign/delete/
  group-delete enqueue correct cleanup; `INDEXING_ENABLED=False` no-ops.
- **Retrieval:** owner-authorized `group_id` filter; cross-owner and cross-group
  leakage tests (release-blocking acceptance).
- **Failure isolation:** embedding/Qdrant exception does not mark pipeline failed;
  stack boots with Qdrant unreachable.

## 13. Open Issue Resolutions

1. **Frontend UI** → follow-up change (backend+API only in this slice).
2. **Media interpretation** → text-only embeddings; media is the source, not a
   separately embedded artifact.
3. **Chunking** → transcript/transliterated chunked (~512 tokens, 200 overlap);
   summary/structured single-point; deterministic `uuid5` IDs.
4. **Reindex granularity** → group-level only (per-meeting reindex via
   `stage_embedding` is an internal primitive, not a public endpoint).
5. **Dimension switch** → per-dimension collection naming + manual reindex (§5.2).
6. **Delivery shape** → see §14 chain recommendation.
7. **Multi-user sharing** → deferred; owner-only.

## 14. Rollout & Rollback

**Rollout order (recommended chains, see below):**
1. Group model + migration + `GroupService` + `groups.py` CRUD (no vector code).
2. Embedding provider seam + Qdrant compose + vector-store client + Celery
   lifecycle tasks + `Meeting.group_id` read/write + assignment endpoint.
3. Retrieval contract + manual reindex endpoint + observability/tests.

**Rollback:**
- `INDEXING_ENABLED=False` disables indexing/cleanup; meetings/groups remain
  functional; retrieval returns `503` (no partial data).
- Remove `qdrant` compose service + `qdrant_data` volume; orphaned vectors inert.
- Remove `groups` router registration, embedding/vector/retrieval services, worker
  task wiring; `dispatch_pipeline` returns to current shape.
- Migration downgrade safe (`group_id` nullable; group rows droppable without
  affecting meetings).

## 15. Validation Steps (unvalidated assumptions — run during apply)

1. **Qdrant client contract:** spike against `qdrant/qdrant:latest` to confirm the
   Python client version, collection-create API, payload-filter syntax, and
   `delete`/`upsert` semantics. Record any API drift here before implementation.
2. **Ollama embeddings route:** verify the local Ollama endpoint exposes an
   OpenAI-compatible `POST /v1/embeddings`; if not, adjust `OllamaEmbeddingProvider`
   to the native route (the adapter absorbs this).
3. **Embedding dimension:** confirm the default model's vector length (and the
   OpenAI alternative's); fix `EMBEDDING_DIMENSION` and the collection name before
   first index.
4. **Resource envelope:** measure local Ollama embedding-model RAM/CPU on the VPS
   (12 GB, CPU-only) and Qdrant idle RAM (~512 MB estimate, re-verify).

## 16. Delivery / Review Budget Note

Expected change size (model + migration + 2 services + router + 4 Celery tasks +
provider adapter + vector client + compose + tests) is **well above the 400-line
review budget**. Per the ask-on-risk gate, present chain candidates to the user
before apply and do **not** auto-chain. Recommended split (matches §14):
(a) group entity + CRUD + migration; (b) embedding provider + Qdrant + lifecycle;
(c) retrieval contract + reindex + observability.
