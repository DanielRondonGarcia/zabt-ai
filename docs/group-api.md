# Group API

All endpoints require an active authenticated user. Group access is owner-scoped.

## Groups

### `POST /groups/`

Create a group for the current user.

Request:

```json
{"name":"Clinical cases","description":"Optional notes"}
```

Response: `201 GroupRead`.

### `GET /groups/`

List only groups owned by the current user.

### `GET /groups/{group_id}`

Return one owned group. Missing groups return `404`; foreign groups return `403`.

### `PATCH /groups/{group_id}`

Update `name` and/or `description` for an owned group.

### `DELETE /groups/{group_id}`

Delete an owned group. Vector cleanup is enqueued through `delete_group_vectors(group_id)` and remains failure-isolated from the API deletion.

## Meeting assignment

### `PATCH /meetings/{meeting_id}/assign-group`

Assign, reassign, or unassign a meeting.

Request:

```json
{"group_id": 123}
```

Use `{"group_id": null}` to unassign.

Lifecycle behavior:

- assign: commit `Meeting.group_id`, then enqueue `stage_embedding(meeting_id)`;
- reassign: enqueue `delete_meeting_vectors(meeting_id)`, then `stage_embedding(meeting_id)`;
- unassign: commit `Meeting.group_id = null`, then enqueue `delete_meeting_vectors(meeting_id)`.

The target group must be owned by the current user.

## Search

### `POST /groups/{group_id}/search`

Search indexed chunks for one owned group.

Request:

```json
{"query":"follow-up plan","limit":10,"kinds":["summary"]}
```

Response:

```json
{
  "group_id": 123,
  "results": [
    {"meeting_id": 456, "kind": "summary", "chunk_index": 0, "score": 0.91, "text": "..."}
  ]
}
```

Contract:

- foreign group: `403`;
- missing group: `404`;
- provider/Qdrant/indexing unavailable: `503 {"detail":"retrieval unavailable"}`;
- server-side vector filter: current `owner_id` plus requested `group_id`, optional `kinds`.

## Reindex

### `POST /groups/{group_id}/reindex`

Authorize the group and enqueue deterministic reindexing for all currently assigned meetings.

Response: `202`.

```json
{"status":"accepted","task_id":"celery-task-id"}
```

The endpoint does not block until indexing completes.

## Deferred scope

No MCP endpoints or chat API are part of this group API slice.
