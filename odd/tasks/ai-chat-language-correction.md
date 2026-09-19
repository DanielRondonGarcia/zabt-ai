# AI chat and explicit transcription language correction

## Goal
Fix explicit meeting-language handling so selecting Spanish cannot silently accept English detection, and expose a secure group-scoped `/ai-chat` experience backed by the existing retrieval service.

## Assumptions
- `/ai-chat` searches one user-owned group at a time; the UI lets the user select the group.
- Chat answers use a chat-specific OpenAI-compatible client that defaults to host Ollama (`qwen3-vl:8b-thinking`) and only retrieved snippets as evidence; summary/image OpenAI routing remains separate.
- Existing owner/group authorization and retrieval `503` behavior remain authoritative.
- No MCP integration, chat history persistence, or delivery commands are in scope.

## Tasks

- [x] Define explicit language override semantics and add GPU/backend regression tests.
- [x] Implement authenticated AI chat service and endpoint with bounded context and source citations.
- [x] Add the dashboard `/ai-chat` page, API helper, and homepage query routing.
- [x] Run focused tests, frontend checks, and local stack validation; record failures or warnings.
- [x] Route AI chat through host Ollama by default while keeping summary/vision OpenAI settings separate.
- [ ] Resolve pre-existing legacy `backend/tests/` collection blockers before claiming the entire repository test tree is green.

## Verification evidence

- `cd zabt-gpu-worker && uv run pytest tests/test_pipeline_language.py -q` — 6 passed, 1 warning.
- `env DATABASE_URL=postgresql://app:app@127.0.0.1:5433/zabt MINIO_ENDPOINT=127.0.0.1:9000 MINIO_PUBLIC_ENDPOINT=http://127.0.0.1:9000 bash -lc 'cd backend && uv run pytest tests/unit/test_transcription_language_resolution.py -q'` — 4 passed, 2 warnings.
- The GPU worker now gives an explicit `forced` Whisper language precedence over detected language, even when the detected language is present in `allowed_languages`.
- `POST /api/v1/ai-chat/` authorizes through `retrieval_service.search` before the LLM, bounds evidence to 6,000 characters, preserves source snippets, and returns stable 503 errors for chat-provider failures.
- AI chat is routed to host Ollama by default (`qwen3-vl:8b-thinking` through `host.docker.internal:11434`); summary and image analysis retain their separate OpenAI cloud settings.
- Service/API focused tests pass independently; service and endpoint files were kept under unique pytest module names to avoid import collisions.
- The dashboard page is session-only, uses one selected group, links every returned source to its meeting, and routes the existing home query bar to `/ai-chat?q=...`.
- Chat provider settings are independently overridable without changing summary/vision routing.
- `backend/app/tests/` passes 280 tests after the chat-provider routing correction; the focused chat service suite passes 7 tests with local Ollama routing. The full `backend/tests/` tree remains blocked during collection by missing Playwright, an obsolete `TranscriptionService` import, and a `Settings` import collision. These failures are outside this change's surfaces.
- `frontend-2 && npm run build` passes and the rebuilt stack exposes `/api/v1/ai-chat/`, `/api/v1/groups/`, web `/ai-chat`, Qdrant, host Ollama embeddings, and CUDA GPU health. GPU logs retain the known torchcodec/pyannote warning.
- Chat provider routing now uses `AI_CHAT_BASE_URL`, `AI_CHAT_MODEL`, and `AI_CHAT_API_KEY`, defaulting to host Ollama (`http://host.docker.internal:11434/v1`, `qwen3-vl:8b-thinking`, local compatibility key `ollama`) for API and worker process configuration.
- Native review inspection reported `rdd_disabled`; no review lineage was started. Delivery remains uncommitted and requires the user's normal commit/PR decision.
- After rebuild, the API container reports `AI_CHAT_BASE_URL=http://host.docker.internal:11434/v1` and `qwen3-vl:8b-thinking`; container-to-host Ollama `/v1/models` returned both `nomic-embed-text` and `qwen3-vl:8b-thinking`.

## Delivery

- Keep changes uncommitted as requested; no push, pull request, release, or destructive cleanup.
