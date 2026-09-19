# Actsis server embeddings, transcription, and diarization

## Goal
Use the OpenAI-compatible Actsis gateway at `https://ai.actsis.internal/v1` for embeddings and audio transcription, keep retrieval chat and vision on official OpenAI, and preserve a deliberate diarization fallback because the current Actsis transcription contract is not speaker-aware.

## Provider mapping
- Actsis endpoint: `https://ai.actsis.internal/v1`.
- Embeddings: model `nomic-embed`, dimension configured at 768 pending endpoint confirmation.
- Transcription: model `whisper-1`, OpenAI-compatible `/audio/transcriptions` route.
- Diarization: tested against the gateway and currently **not supported** by its `whisper-1` model; `/audio/transcriptions` accepts only `json`, `verbose_json`, `text`, `srt`, and `vtt` and rejects `diarized_json` with HTTP 422. Keep the existing GPU WhisperX + pyannote API as the known-good diarization path until Actsis exposes a dedicated speaker-aware endpoint/model.
- Chat and vision: official OpenAI settings remain unchanged.
- Secret: put the value in the ignored root `.env` as `ACTSIS_API_KEY=...`; never place it in source, screenshots, or committed docs. Runtime maps it to both embedding and transcription providers.

## Tasks

- [x] Define Actsis provider mapping and secret boundary.
- [x] Configure Actsis embeddings and transcription adapters plus Compose defaults.
- [x] Validate server diarization support boundary (gateway currently rejects `diarized_json`).
- [x] Run focused provider tests and live endpoint smoke checks.
- [x] Add the Actsis key and activation values to the ignored root `.env` (credential was written without exposing it in output).
- [ ] Obtain a dedicated Actsis speaker-aware endpoint/model; current transcription remains non-diarized because `diarized_json` is rejected.

## Acceptance criteria

- API and worker receive the Actsis endpoint/model settings without exposing the key in logs or source.
- Embeddings use the Actsis `/v1/embeddings` endpoint and do not fall back to the official OpenAI key when the endpoint is custom.
- Transcription uses the Actsis `/v1/audio/transcriptions` endpoint and the `whisper-1` model without sending the official OpenAI key to the internal gateway.
- The adapter supports `diarized_json` only when explicitly enabled and the gateway advertises it; current Actsis `whisper-1` rejects it, so active configuration must keep `TRANSCRIPTION_CLOUD_DIARIZATION=false` and use the GPU API for speakers.
- Chat and vision continue using official OpenAI configuration.
- Existing Qdrant data is treated as stale after changing embedding provider/model; a group reindex is required after the key is configured.
- Selecting Actsis `whisper-1` currently provides transcription without speakers; it must not be presented as diarization.

## Verification evidence

- Actsis `GET /v1/models` — HTTP 200; confirmed `whisper-1` and `nomic-embed` are available.
- Actsis `POST /v1/embeddings` with `nomic-embed` — HTTP 200; returned 768-dimensional vectors.
- Actsis `POST /v1/audio/transcriptions` with `whisper-1` and `response_format=json` — HTTP 200.
- Actsis `POST /v1/audio/transcriptions` with `response_format=diarized_json` — HTTP 422; server explicitly accepts only `json`, `verbose_json`, `text`, `srt`, and `vtt` for this model.
- Live provider smoke test passed through the application adapters: `nomic-embed` returned 768 dimensions and `whisper-1` returned HTTP 200 for a WAV probe.
- The adapters use the OS trust store plus optional `ACTSIS_CA_BUNDLE`; the backend image now installs the public ACTSIS root CA because the Linux container trust store did not include the internal gateway CA.
- Focused provider tests pass after adding custom gateway URL/key isolation, TLS handling, and optional diarized parsing.
- Reproduced the chat 503 from the browser: retrieval failed before Qdrant/LLM because the API container raised `CERTIFICATE_VERIFY_FAILED` while calling Actsis embeddings. Rebuilt API/worker/beat with the ACTSIS root CA; an in-container embedding call now returns 768 dimensions.
- Root `.env` now contains the Actsis activation values and key without exposing the credential in source/output; rotate the key because it was pasted into chat, then restart services if a replacement is issued.

## Delivery

- Keep changes uncommitted until explicitly requested.
