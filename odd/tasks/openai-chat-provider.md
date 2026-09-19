# OpenAI retrieval chat and server model integration boundary

## Goal
Route retrieval-grounded chat through the configured OpenAI-compatible cloud endpoint instead of local Ollama, improve greeting/language behavior, and document how the separately mounted model server and GPU diarization API fit the architecture.

## Provider decision
- Current immediate choice: official OpenAI-compatible settings from `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY`; default chat model `gpt-4o-mini`.
- The server model catalog shown by the user is a LiteLLM/OpenAI-compatible gateway, but its base URL and authentication were not supplied. Do not invent or hard-code that endpoint.
- Future server mapping can use chat `gpt-oss-120b`, embeddings `nomic-embed`, and transcription `whisper-1` once the gateway URL/key are provided.
- Diarization is not an OpenAI-compatible chat model. The existing GPU worker exposes `/run` and `/status/{job_id}` and runs WhisperX + pyannote (`DIARIZATION_MODEL`) locally; the backend already calls this API through `GpuTranscriptionClient`.

## Tasks

- [x] Define OpenAI chat and server integration scope.
- [x] Route retrieval chat through official OpenAI settings.
- [x] Improve small-talk and answer-language behavior.
- [x] Validate focused tests, runtime provider settings, and GPU API boundary.

## Acceptance criteria

- Chat defaults to `https://api.openai.com/v1` and `gpt-4o-mini`, while explicit `AI_CHAT_*` overrides remain supported.
- Chat falls back to `OPENAI_API_KEY` when no chat-specific key is supplied.
- A greeting such as `Hola` does not surface irrelevant meeting chunks as answer sources.
- Substantive questions remain retrieval-first and owner/group-authorized.
- Diarization remains available through the existing GPU HTTP API and is clearly separated from the LiteLLM model catalog.
- No provider secret is copied into source code or committed artifacts.

## Verification evidence

- `cd backend && uv run pytest app/tests/unit/services/test_ai_chat_service.py -q` — 9 passed.
- `env DATABASE_URL=postgresql://app:app@127.0.0.1:5433/zabt MINIO_ENDPOINT=127.0.0.1:9000 MINIO_PUBLIC_ENDPOINT=http://127.0.0.1:9000 bash -lc 'uv run pytest app/tests/unit/api/v1/test_ai_chat.py -q'` — 8 passed.
- `docker compose config` confirms chat defaults to `https://api.openai.com/v1` and `gpt-4o-mini`; the chat-specific key is intentionally empty and the service falls back to the shared `OPENAI_API_KEY` without copying it into source.
- Running API container reports `AI_CHAT_BASE_URL=https://api.openai.com/v1`, `AI_CHAT_MODEL=gpt-4o-mini`, and shared OpenAI key present; `/v1/models` returned HTTP 200.
- `docker compose up -d --build api worker beat` completed and all backend containers are running.
- Chat prompt now prevents irrelevant sources for simple greetings and instructs the model not to mix languages. Substantive chat remains retrieval-first and owner/group-authorized.
- Diarization boundary confirmed: GPU worker `/run` + `/status/{job_id}` runs WhisperX and pyannote; no separate LiteLLM model is required.

## Follow-up

- Obtain the user's LiteLLM gateway base URL (including `/v1`) and intended auth method before switching embeddings/transcription/vision modules to those server models.
- Existing repository-wide frontend lint blockers remain tracked separately.

## Delivery

- Keep changes uncommitted until explicitly requested.
