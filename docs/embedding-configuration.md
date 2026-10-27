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
| `EMBEDDING_MAX_BATCH` | Maximum number of inputs in each provider HTTP request. Provider adapters split larger meeting indexes into sequential requests. |
| `QDRANT_URL` | Qdrant HTTP endpoint. |
| `QDRANT_COLLECTION_PREFIX` | Prefix for active embedding collections. |

The active Qdrant collection name is provider- and dimension-scoped:

```text
{QDRANT_COLLECTION_PREFIX}_{EMBEDDING_PROVIDER}_{EMBEDDING_DIMENSION}
```

Changing provider or dimension intentionally writes to a separate collection.

## Chunking and request batching

Embedding content is split into deterministic windows of 128 whitespace-separated
words with a 32-word overlap. This is a conservative word-based safety margin,
not a tokenizer-exact token limit; it leaves headroom for gateways with a
physical limit of 512 provider tokens. The same bounded windows apply to
transcripts, transliterations, long summaries, and long structured output.

Every emitted embedding input is additionally capped at 480 UTF-8 bytes
(`EMBEDDING_INPUT_MAX_BYTES`, a chunker constant, not a setting). This keeps
long single inputs such as meeting 15's failing 1,312-byte summary window out
of the provider request; it does not guarantee success if the gateway fails for
another reason or has a smaller physical token limit. Over-budget word windows
are subdivided with the same 32-word overlap; a single word longer than the
budget is split at character boundaries. Every source character appears in at
least one emitted chunk. Overlap repeats some words, while long-word fragments
are carried by consecutive chunks. Chunk IDs remain deterministic; citation
text contains only source text or its ordered fragments.

The optional group introduction is part of each embedding input and is kept
verbatim when it fits within `GROUP_CONTEXT_MAX_BYTES` (96 UTF-8 bytes). Longer
introductions are truncated deterministically at word boundaries within that
byte budget and marked with a trailing ellipsis (`…`); an introduction whose
first word alone exceeds the budget keeps a UTF-8-safe character prefix of that
word plus the ellipsis, so the bounded context is never empty. This is a
deliberate bounded-context policy: short introductions (the common case) are unchanged,
an oversized introduction never silently replaces meeting text, and meeting
evidence itself is always embedded in full.

`EMBEDDING_MAX_BATCH` limits the number of input strings sent in one HTTP request,
not the number of chunks allowed in one meeting index. The OpenAI-compatible and
Ollama adapters send larger meeting indexes in sequential batches and concatenate
the validated responses in input order.

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
