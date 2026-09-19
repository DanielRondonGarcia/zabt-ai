# Embedding configuration

Group embeddings are controlled by backend settings and can be disabled independently from meeting processing.

## Settings

| Setting | Purpose |
| --- | --- |
| `INDEXING_ENABLED` | Enables Celery indexing tasks and retrieval. Set `false` for rollback/no-op indexing. |
| `EMBEDDING_PROVIDER` | `ollama` or `openai`. Invalid values fail configuration validation. |
| `EMBEDDING_BASE_URL` | OpenAI-compatible `/v1` base URL used by the selected provider. |
| `EMBEDDING_MODEL` | Embedding model name sent to the provider and stored in Qdrant payloads. |
| `EMBEDDING_DIMENSION` | Expected vector dimension and Qdrant collection size. Provider responses must match it. |
| `EMBEDDING_API_KEY` | API key for providers that require one. Do not log this value. |
| `EMBEDDING_MAX_BATCH` | Maximum provider batch size per embedding request. |
| `QDRANT_URL` | Qdrant HTTP endpoint. |
| `QDRANT_COLLECTION_PREFIX` | Prefix for active embedding collections. |

The active Qdrant collection name is provider- and dimension-scoped:

```text
{QDRANT_COLLECTION_PREFIX}_{EMBEDDING_PROVIDER}_{EMBEDDING_DIMENSION}
```

Changing provider or dimension intentionally writes to a separate collection.

## Ollama

Ollama uses the OpenAI-compatible embeddings route:

```text
POST {EMBEDDING_BASE_URL}/embeddings
```

Use a local base URL such as `http://localhost:11434/v1` when available. The model must return vectors with exactly `EMBEDDING_DIMENSION` values.

## OpenAI-compatible provider

The OpenAI-compatible adapter also posts to `/v1/embeddings`. Configure `EMBEDDING_API_KEY` for remote providers. This PR3b validation did not call OpenAI and no credentials should be printed in test output.

## Dimension strategy

- Keep `EMBEDDING_DIMENSION` aligned with the selected model.
- A dimension mismatch raises before upsert, preventing mixed-size vectors in Qdrant.
- Changing the dimension creates a new active collection name; old collections can be retained during rollout or removed manually after rollback confidence.

## Retrieval and failure behavior

Search requires `INDEXING_ENABLED=true`, a reachable provider, and reachable Qdrant. Failures return one API shape:

```json
{"detail":"retrieval unavailable"}
```

No partial retrieval response should be returned when provider or Qdrant access fails.

## Rollback

Fast rollback:

```text
INDEXING_ENABLED=false
```

Effects:

- indexing tasks short-circuit;
- retrieval returns `503`;
- core transcription/summarization pipeline continues;
- existing Qdrant collections are left untouched for later inspection or deletion.

Full code rollback for this slice removes `GroupRetrievalService`, search/reindex endpoints, and PR3b documentation/tests. PR2 indexing can be disabled independently.

## Deferred scope

MCP and chat integrations are not implemented here. Future chat/MCP work must reuse the owner/group filtered retrieval contract rather than bypassing it.
