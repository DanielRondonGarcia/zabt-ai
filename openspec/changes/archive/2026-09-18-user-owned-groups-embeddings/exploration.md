---
status: explored
executive_summary: >-
  Groups, group-scoped embeddings, and Qdrant retrieval are all greenfield in
  this codebase. There is no Group model, no embedding service, no Qdrant
  client, and no vector dependency; Qdrant was deliberately removed from
  docker-compose (ROADMAP #18) pending real RAG work. The lowest-risk path is
  to introduce an owner-scoped Group model plus a group_id foreign key on
  Meeting (and any future artifact), following the established owner-scoped
  service/endpoint pattern (templates.py / TemplateService), re-introduce a
  Qdrant compose service, and add a Celery-driven embedding task that reuses the
  existing LLM seam (ai_agent.py) or a dedicated embedding model, with group_id
  as the retrieval payload filter. MCP/agent access and in-product chat remain
  explicitly out of scope.
artifacts:
  - path: openspec/changes/user-owned-groups-embeddings/exploration.md
    store: openspec
    type: exploration
next_recommended: sdd-research
risks:
  - Qdrant is absent from docker-compose and has no client library dependency; the vector stack must be re-introduced and an embedding model/provider chosen before any indexing can run.
  - Meetings are the only first-class content artifact; "videos, audios, and related artifacts" are currently represented as Meeting.file_path plus stored objects, not as distinct models.
  - The embedding generation seam (LLM vs dedicated embedding model) and its worker-vs-sync placement are undecided and materially affect cost, latency, and recovery design.
  - Group membership/ownership is single-owner only in the existing model; shared/multi-user group access and MCP authorization are future concerns with no precedent.
skill_resolution:
  mode: none
  injected_skill_paths: []
---

## Exploration: User-Owned Groups + Embeddings for Retrieval

### Current State

The requested feature is new surface area across the entire stack: a user-owned
`Group` entity that can contain meetings and their related artifacts (videos,
audios, files), automatic embedding generation for group-associated content into
Qdrant, and use of `group_id` as a retrieval filter for future MCP/agent access.
In-product chat is a separate, later feature and is out of scope here.

None of the core pieces exist today. This exploration documents the reusable
seams, the greenfield gaps, and the decisions a proposal must settle.

#### No group, embedding, or vector code exists

- A repository-wide search for `group|Group|embedding|Embedding|qdrant|Qdrant|vector`
  found no `Group` model, no embedding service, and no Qdrant client. The only
  matches were unrelated regex `match.group(0)` usages and template seed strings.
- `backend/pyproject.toml` declares no Qdrant, fastembed, sentence-transformers,
  or embedding-related dependency.
- `docker-compose.yml` services are `redis`, `api`, `worker`, `beat` (always-on)
  plus profile-gated `db`, `minio`, `minio-init`, `worker-gpu`, `web`,
  `worker-bot`, and `zabt-vision-worker`. Qdrant is absent; ROADMAP #18 records
  that the Qdrant container + volume were removed "deferred to when RAG is
  actually built."

#### Qdrant and RAG are recognized but unbuilt roadmap items

- ROADMAP #14 ("AI Chat over meetings (RAG or long context)") is Medium priority
  and explicitly lists two approaches to evaluate: RAG via Qdrant embeddings +
  retrieval vs. long-context windowing, possibly hybrid (long context for
  single-meeting chat, RAG for cross-meeting search). This is adjacent to, but
  distinct from, the requested group-scoped embeddings feature.
- `openspec/config.yaml` still names "Qdrant (vector DB)" in the infra
  description, so the intent to reintroduce it is consistent with the project's
  declared stack, even though no code or compose service currently backs it.

#### Ownership and the canonical owner-scoped CRUD pattern

- Auth is first-party local JWT via `backend/app/api/deps.py`
  (`get_current_user`, `get_current_active_user`), with `owner_id` foreign keys
  already present on `SummaryTemplate`, `StyleProfile`, `Meeting`, and others.
- `backend/app/services/base.py` provides `BaseService` with `save`, `get`,
  `delete`, and `get_all(model, owner_id, skip, limit)` where `get_all` filters
  `model.owner_id == owner_id`; audit hooks `on_before_action`/`on_after_action`
  are print-based.
- `backend/app/api/v1/endpoints/templates.py` + `backend/app/services/template.py`
  (`TemplateService`) is the canonical owner-scoped CRUD example:
  `list_for_user`, `create_custom`, `get_accessible`, `update_custom`,
  `delete_custom`, `set_user_default`, with explicit 404/403 handling and
  `Depends(deps.get_current_active_user)`. A `GroupService`/`groups.py` endpoint
  should mirror this shape.

#### Meeting is the only first-class content artifact

- `backend/app/models/base.py` defines `Meeting` (with `owner_id`,
  `file_path`, `source_type` for `upload` vs `youtube`, `transcript_text`,
  `summary_text`, `segments` relationship, and visual-breakdown columns).
  There is no separate `Video`, `Audio`, or `File` model. "Videos, audios, and
  related artifacts" are currently represented as a `Meeting` row whose
  `file_path` points at an object in MinIO/S3.
- `MeetingService` (`backend/app/services/meeting.py`) already manages the full
  lifecycle, including status transitions, heartbeat, and visual-breakdown epochs
  under row locks. A `group_id` foreign key and a back-reference would extend
  this model, and the service already has the owner-scoping discipline needed.

#### Storage seam reusable for group artifacts

- `backend/app/services/storage.py` exposes a runtime-checkable
  `StorageProvider` Protocol (presigned upload/download URLs, `upload_file`,
  `delete_file`, `delete_prefix` cascade cleanup, multipart upload helpers) with
  boto3/botocore S3/MinIO clients. Group-associated video/audio/file objects
  would continue to live in MinIO/S3 under the existing bucket, with no new
  storage abstraction required.

#### LLM and async seams for embedding generation

- `backend/app/services/ai_agent.py` is the central AI entry point
  (`summarize_transcript`, `infer_title`, `summarize_context`, `_client` built
  from `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL`). `meeting_intelligence.py`
  adds structured extraction via `langfuse.openai` + `beta.parse`. There is no
  embedding call yet.
- `backend/app/worker.py` is a Celery app (Redis broker/backend) with a
  chain-based pipeline (`dispatch_pipeline`), `send_notification`,
  `stage_visual_breakdown`, and recovery via `meeting_recovery.py`. An
  embedding step would naturally be a new Celery task appended after summary/
  intelligence completion, reusing the existing dispatch chain and Sentry/
  Logfire instrumentation.

### Affected Areas

- `backend/app/models/base.py` (and `__init__.py` re-exports) — add a `Group`
  model, `GroupRead`/`GroupCreate` schemas, and a `group_id` foreign key on
  `Meeting`; define a `GroupMembership` table if multi-user sharing is in scope
  (otherwise defer).
- `backend/alembic/versions/` — new migration for `group` and the `meeting.group_id`
  column; 18 migrations already exist with mixed naming conventions.
- `backend/app/services/` — new `group.py` service mirroring `TemplateService`;
  a new embedding service (or extension of `ai_agent.py`) to produce vectors.
- `backend/app/api/v1/endpoints/groups.py` — new owner-scoped CRUD endpoints;
  extend `meetings.py` to accept/return `group_id`.
- `backend/app/worker.py` — new `stage_embedding`/embedding Celery task wired
  into `dispatch_pipeline` after transcription/summary.
- `backend/app/core/config.py` — new settings for Qdrant URL/collection and the
  embedding model/provider.
- `docker-compose.yml` — reintroduce a `qdrant` service + volume (and wire
  `QDRANT_URL` into `api` and `worker`).
- `backend/pyproject.toml` — add the Qdrant client and embedding library
  dependencies.
- `frontend-2/app/(dashboard)/` and `frontend-2/app/components/` — optional
  group list/detail UI; out of scope for the core backend embedding path but
  implied by "user-owned groups."

### Approaches

1. **Owner-scoped groups + group_id FK + worker-driven embeddings** — Add a
   `Group` model and a `group_id` column on `Meeting`, reintroduce Qdrant in
   compose, and append a Celery embedding task that upserts vectors tagged with
   `group_id` (and `owner_id`) into a Qdrant collection.
   - Pros: follows the established owner-scoped pattern; keeps embedding work out
     of the request path; reuses the existing pipeline and recovery tooling;
     `group_id` becomes a first-class payload filter for future MCP/agent access.
   - Cons: requires choosing a Qdrant client + embedding model, defining a
     re-embed/delete strategy on group or meeting mutation, and a migration.
   - Effort: Medium-High

2. **Embedding-as-a-service via the existing LLM endpoint** — Use the existing
   OpenAI-compatible client to call an embeddings endpoint (or a dedicated
   provider) rather than a local embedding model, avoiding a new local service.
   - Pros: no local model/GPU dependency; minimal new infrastructure beyond
     Qdrant; consistent with the OpenRouter/LM Studio/Ollama flexibility already
     in `ai_agent.py`.
   - Cons: per-call cost and latency; depends on the configured provider
     supporting embeddings; ties embedding quality to the provider's model.
   - Effort: Medium

3. **Local embedding worker with a dedicated model** — Add fastembed or
   sentence-transformers and run embeddings in the existing worker (or a new
   profile service) without an external API.
   - Pros: zero per-embedding cost; deterministic, offline-capable.
   - Cons: heavier dependency footprint and memory/CPU; a second embedding stack
     to operate alongside the LLM one; may not match the project's current
     cloud-first LLM posture.
   - Effort: Medium-High

### Recommendation

Adopt approach 1, with the embedding producer chosen between approaches 2 and 3
as a research decision. Introduce an owner-scoped `Group` model and a nullable
`group_id` foreign key on `Meeting`, following the `TemplateService` /
`templates.py` pattern exactly (owner-scoped list/create/get/update/delete with
explicit 403/404). Reintroduce a Qdrant service + volume in `docker-compose.yml`
and add Qdrant settings to `Settings`. Add a Celery `stage_embedding` task
appended to `dispatch_pipeline` that upserts group-associated content vectors
with `group_id` and `owner_id` as payload filters, and define an idempotent
re-embed/delete path for group and meeting mutations.

Because "videos, audios, and related artifacts" are currently all `Meeting`
rows plus stored objects, the proposal should explicitly decide whether the
first slice indexes only meeting transcripts/summaries (recommended) or whether
a separate artifact model must be introduced first. Keep MCP/agent access and
in-product chat out of scope: the deliverable is the group entity and the
group-filtered vector index, not a retrieval UI or agent.

### Risks

- **Greenfield vector stack:** Qdrant is not in compose and no client/embedding
  dependency exists; the proposal must select a client library, collection
  schema, and embedding model before indexing can be validated.
- **Artifact modeling gap:** There is no `Video`/`Audio`/`File` model today;
  everything is a `Meeting` with a `file_path`. Supporting arbitrary artifact
  types may require a new model hierarchy rather than a single `group_id` on
  `Meeting`.
- **Embedding producer undecided:** LLM-endpoint embeddings vs. a local embedding
  model has material cost, latency, quality, and operations implications and is
  not settled by repository evidence.
- **Lifecycle consistency:** Re-embedding or deleting vectors on meeting
  re-processing, summary edits, group membership changes, or group deletion must
  be idempotent and aligned with the existing recovery/epoch discipline in
  `meeting.py`.
- **Multi-user access:** The current model is single-owner; shared/multi-user
  groups and MCP authorization have no precedent and would need a membership
  model that is explicitly deferred or designed now.
- **Scope creep toward chat:** ROADMAP #14 (RAG chat) is adjacent; the proposal
  must keep in-product chat out of scope to avoid coupling the group-embedding
  work to a chat UI.

### Ready for Proposal

Yes, after one research decision. The repository evidence is sufficient to
propose the group entity, the `group_id` foreign key, the Qdrant compose
re-introduction, and the worker-driven embedding task. A short research phase is
warranted to select the embedding producer (LLM endpoint vs. local model) and
the Qdrant client/collection schema, and to confirm whether "videos/audios/
files" require a new artifact model or can ride on `Meeting` for the first slice.
