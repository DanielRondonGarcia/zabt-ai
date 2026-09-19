# Delta for user-owned-groups-embeddings

This delta represents the full canonical specification for the `user-owned-groups-embeddings` capability because no prior canonical spec existed. Archive-time tooling will copy this content into `openspec/specs/user-owned-groups-embeddings/spec.md`.

## ADDED Requirements

### Requirement: Owner-scoped Group CRUD with explicit authorization

The system MUST enforce owner-scoped access on every Group operation: list, create, get, update, and delete. Every read or write MUST fail with 404 if the group does not exist and 403 if the authenticated user is not the group owner; cross-owner access MUST never succeed.

#### Scenario: User lists only groups they own

- GIVEN user A has created two groups and user B has created one group
- WHEN user A calls the list groups endpoint authenticated as themselves
- THEN the response includes only user A's two groups and excludes user B's group

#### Scenario: User cannot read a group owned by another user

- GIVEN user A owns group X and user B owns group Y
- WHEN user A attempts to read group Y authenticated as themselves
- THEN the system returns 404 Not Found and does not reveal user B's group exists

#### Scenario: User cannot modify or delete a group owned by another user

- GIVEN user A owns group X and user B owns group Y
- WHEN user A sends an update or delete request for group Y authenticated as themselves
- THEN the system returns 404 Not Found for both operations and does not perform any mutation

### Requirement: Nullable group_id foreign key on Meeting

Every Meeting row MUST have a nullable `group_id` column referencing Group.id. Existing meetings without a group assignment MUST have `group_id = NULL` and remain fully functional without any indexing behavior.

#### Scenario: New meetings start ungrouped

- GIVEN no group assignment is provided in the meeting creation payload
- WHEN a meeting is created
- THEN the meeting is stored with `group_id = NULL` and the meeting lifecycle proceeds without indexing triggers

#### Scenario: Meeting assignment triggers indexing

- GIVEN a meeting with `group_id = NULL` and a valid group owned by the same user
- WHEN the meeting's `group_id` is set to the group's identifier
- THEN an asynchronous indexing task is enqueued and the meeting's group assignment is persisted

### Requirement: Configurable embedding provider with validated dimensions

The embedding provider MUST be selected by configuration only (provider name, base URL, model, and dimension). The system MUST validate the selected provider exists, require API keys for providers that need them, assert a positive dimension, and fail at startup or first use if configuration is invalid; no provider selection or fallback is permitted.

#### Scenario: Default Ollama provider works with configured dimension

- GIVEN configuration sets `EMBEDDING_PROVIDER=ollama`, `EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1`, `EMBEDDING_MODEL=nomic-embed-text`, and `EMBEDDING_DIMENSION=768`
- WHEN the embedding provider adapter initializes
- THEN it connects to the Ollama endpoint and asserts returned vectors have length 768; if the assertion fails, an explicit error is raised

#### Scenario: Invalid configuration raises at startup

- GIVEN `EMBEDDING_PROVIDER=openai` is set without `EMBEDDING_API_KEY` or `OPENAI_API_KEY`
- WHEN the application starts and validates settings
- THEN startup fails with an explicit validation error that does not fall back to another provider

### Requirement: Deterministic chunking and canonicalization for meeting content

Transcript and transliterated text MUST be chunked into ~512-token windows with 200-token overlap; summary, structured output, and other content kinds MUST be emitted as single points. Every point MUST be keyed deterministically using `uuid5(NAMESPACE, f"{meeting_id}:{kind}:{chunk_index}")` so reindexing replaces prior points without duplication.

#### Scenario: Long transcript produces multiple deterministic chunks

- GIVEN a transcript longer than 512 tokens
- WHEN embedding is performed
- THEN the transcript is split into overlapping ~512-token chunks, each point receives a `chunk_index` starting at 0, and the same chunk_index produces the same UUID across multiple runs

#### Scenario: Summary content is indexed as a single point

- GIVEN a meeting has summary text
- WHEN embedding is performed
- THEN exactly one point is created with `kind=summary` and `chunk_index=0`

#### Scenario: Reindex replaces prior points deterministically

- GIVEN a meeting has existing embedding points for its content
- WHEN the same meeting is re-indexed
- THEN existing points are replaced by new points with the same UUID, not duplicated

### Requirement: Asynchronous indexing on group assignment and source-content changes

Indexing MUST be performed asynchronously by Celery tasks appended to the dispatch pipeline after final content is available (after summarization and intelligence extraction). Group assignment, source-content mutation (re-transcribe, re-summarize, summary edit), and group/unassign/deletion MUST each enqueue the appropriate indexing or cleanup task; all tasks MUST be idempotent upserts or deletes.

#### Scenario: Assigning a meeting to a group triggers indexing

- GIVEN a meeting with content is created ungrouped
- WHEN the meeting is assigned to a group owned by the same user
- THEN the assignment endpoint enqueues an indexing task and returns 200; indexing completes asynchronously

#### Scenario: Re-transcribing a meeting triggers reindexing

- GIVEN a meeting is assigned to a group and has existing embedding points
- WHEN the meeting is re-transcribed (producing new transcript text)
- THEN the transcript mutation enqueues an indexing task that replaces prior points for that meeting

#### Scenario: Group deletion enqueues vector cleanup

- GIVEN a group has meetings with embedding points
- WHEN the group is deleted by its owner
- THEN the delete operation enqueues a cleanup task that removes all points tagged with that `group_id`

### Requirement: Failure-isolated indexing lifecycle

Indexing and cleanup tasks MUST never mark the main meeting pipeline as failed. Embedding or Qdrant errors MUST be logged, instrumented, and retried within configured bounds; exhaustion MUST leave the indexing status stale but not break the meeting's `completed` status or future pipeline progress.

#### Scenario: Embedding failure does not fail the meeting pipeline

- GIVEN a meeting is assigned to a group and the embedding provider is unreachable
- WHEN the indexing task fails after retries
- THEN the meeting's status remains `completed`, the indexing failure is logged and instrumented, and future meeting updates can trigger new indexing attempts

#### Scenario: INDEXING_ENABLED=false short-circuits all indexing

- GIVEN `INDEXING_ENABLED=False` is set in configuration
- WHEN any indexing or cleanup task is dispatched
- THEN the task is a no-op and no vector operations are attempted

### Requirement: Qdrant payload isolation by owner_id and group_id

Every vector point MUST carry `owner_id` and `group_id` as payload tags. Every retrieval search MUST enforce `owner_id` and `group_id` filters server-side from the authenticated user and resolved group, never trust client-provided filters.

#### Scenario: Retrieval filters are enforced server-side

- GIVEN user A owns group X and user B owns group Y
- WHEN user A queries group X's retrieval endpoint
- THEN the query filters by `owner_id=user_A_id` AND `group_id=group_X_id` and returns only results from user A's group X, never results from user B's group Y

### Requirement: Internal authorized group-scoped retrieval contract

The group-scoped retrieval endpoint MUST be internal (no public chat UI), require owner authorization, and return ranked semantic matches referencing source meetings. When Qdrant is unavailable or indexing is disabled, the endpoint MUST return 503 with a clear unavailable message rather than partial or cross-owner data.

#### Scenario: Authorized retrieval returns only owner's group content

- GIVEN user A owns group X with indexed meetings
- WHEN user A calls the retrieval endpoint with a query and `group_id=group_X_id`
- THEN the response includes only ranked matches from group X, referenced by meeting_id, kind, and chunk_index

#### Scenario: Retrieval fails explicitly when Qdrant is unavailable

- GIVEN `INDEXING_ENABLED=True` but the Qdrant service is unreachable
- WHEN a retrieval request is made
- THEN the endpoint returns 503 with `{"detail": "retrieval unavailable"}` and no partial or cross-owner results

### Requirement: Manual group-level reindex endpoint for recovery

A manual reindex endpoint MUST be available for each group to restore the index after loss or corruption. The endpoint MUST return 202, dispatch an asynchronous task that re-runs indexing over all meetings in the group, and be idempotent.

#### Scenario: Owner restores index via manual reindex

- GIVEN a group's index has been lost (e.g., collection dropped or corrupted)
- WHEN the owner calls the manual reindex endpoint for that group
- THEN the endpoint returns 202, enqueues a reindex task, and the task re-creates points for all meetings in the group

### Requirement: Rollout and rollback boundaries

The indexing lifecycle MUST be toggled via `INDEXING_ENABLED` and be safely disableable at any time. Rolling back this feature MUST return the stack to a functional state where groups and meetings remain usable and retrieval returns explicit unavailable rather than partial data.

#### Scenario: Rollback via INDEXING_ENABLED=False leaves groups functional

- GIVEN groups and meetings are in use with indexing enabled
- WHEN `INDEXING_ENABLED=False` is applied and services restart
- THEN group CRUD and meeting operations continue to function normally; retrieval endpoints return 503 unavailable; existing indexing points remain inert
