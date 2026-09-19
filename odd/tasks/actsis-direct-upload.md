# Actsis direct full-file transcription upload

## Goal
Allow the Actsis OpenAI-compatible transcription gateway to receive one complete audio upload when its configured request limits support it, while preserving official OpenAI's 25 MB chunking behavior and a safe fallback for rejected oversized requests.

## Scope
- Provider adapter and configuration only.
- Actsis runtime configuration and worker redeploy.
- Focused adapter tests and a bounded runtime smoke check.

## Non-goals
- No change to diarization support.
- No change to official OpenAI chat or vision.
- No parallel chunk scheduling.
- No credential changes or delivery commands.

## Tasks
- [x] Add provider-specific direct-upload policy and preserve official OpenAI limits.
- [x] Add tests for Actsis full-file routing and OpenAI chunking.
- [x] Configure/redeploy worker after the active meeting 512 task completed.
- [x] Verify runtime settings, worker concurrency, API health, and meeting 512 completion.

## Acceptance criteria
- Official OpenAI remains bounded below its 25 MB file limit.
- Actsis can opt into a larger direct-upload threshold without relaxing official OpenAI validation.
- Direct upload sends audio-only media where possible and does not expose credentials.
- Unsupported/oversized Actsis requests fall back to deterministic chunks on a bounded, recognized request-size failure.
- Focused tests pass and the redeployed worker loads the intended settings.

## Evidence
- Current universal chunking is implemented in `backend/app/services/transcription/openai_provider.py` and `backend/app/services/transcription/chunks.py`.
- Meeting 512 was approximately 79 minutes and 245 MB as an MP4; it completed before redeployment.
- Actsis advertises an OpenAI-compatible LiteLLM transcription endpoint but does not publish a maximum upload size in its OpenAPI document.
- Focused provider tests: `33 passed, 1 warning`.
- Configuration-focused tests: `14 passed, 19 deselected, 1 warning`.
- Runtime worker reports `concurrency: 2 (prefork)` and loads `TRANSCRIPTION_DIRECT_UPLOAD_MAX_BYTES=300000000`, `TRANSCRIPTION_TIMESTAMP_MODE=none`, and `TRANSCRIPTION_RESPONSE_FORMAT=verbose_json`.
- API transcription health returned HTTP 200; meeting 512 is `completed`.

## Delivery
- Keep changes uncommitted until explicitly requested.
