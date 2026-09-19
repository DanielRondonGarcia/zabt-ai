# Group embedding overview

User-owned groups partition meeting embeddings by both `owner_id` and `group_id`.

## Ownership model

- A group belongs to exactly one user through `Group.owner_id`.
- `Meeting.group_id` is nullable. Ungrouped meetings are not indexed for group retrieval.
- Group CRUD and assignment endpoints must validate ownership before mutating data or enqueueing work.
- Foreign groups return `403`; missing groups return `404`.

## Indexing lifecycle

Embedding indexing is automatic and asynchronous through Celery:

1. A meeting is assigned to a group.
2. The API commits the `Meeting.group_id` change.
3. `stage_embedding(meeting_id)` canonicalizes meeting content, chunks it deterministically, embeds the chunks, and upserts them into Qdrant.
4. Reassignment deletes the previous meeting vectors and indexes the meeting again for the new group.
5. Unassignment calls `delete_meeting_vectors(meeting_id)`.
6. Group deletion calls `delete_group_vectors(group_id)`.
7. Manual reindex calls `reindex_group(group_id)`, which schedules indexing for every meeting currently assigned to the group.

The embedding stage is intentionally failure-isolated from the core meeting pipeline. It is not linked to the pipeline failure callback that marks meetings failed.

## Qdrant payload isolation

Every vector point stores these payload tags:

- `owner_id`
- `group_id`
- `meeting_id`
- `kind`
- `chunk_index`
- `chunk_count`
- `source_type`
- `model`
- `text`

Search uses server-derived filters only: `owner_id == current_user.id AND group_id == requested_group_id`, with optional `kind` filtering. Clients cannot supply or override owner filters.

## Retrieval contract

`POST /groups/{group_id}/search`:

- Authorizes group ownership before retrieval.
- Embeds the query with the configured provider.
- Searches Qdrant with hard server-side owner and group filters.
- Returns ranked chunks with `meeting_id`, `kind`, `chunk_index`, `score`, and `text`.
- Returns `503` with `retrieval unavailable` when indexing is disabled or the provider/vector store is unavailable; partial results are not returned from failures.

## Rollback

Set `INDEXING_ENABLED=false` to stop indexing/retrieval without affecting core meeting processing. For full PR3 rollback, remove the retrieval and reindex endpoints plus `GroupRetrievalService`.

## Deferred scope

MCP and in-product chat are explicitly deferred. This feature supplies the group-scoped embedding and retrieval substrate only.
