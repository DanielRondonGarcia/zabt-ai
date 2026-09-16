---
status: explored
executive_summary: >-
  Zabt already has a small provider Protocol and a shared WhisperX GPU pipeline,
  but provider selection is an enum-backed singleton that only distinguishes the
  local GPU worker from RunPod. The safest direction is a provider registry of
  adapters around a stable normalized batch contract, retaining the existing
  GPU/RunPod transport while adding a separately configured OpenAI audio adapter;
  the first OpenAI model and API shape must remain unresolved until official,
  source-backed research confirms timestamp, language, speaker, file, and cost
  capabilities.
artifacts:
  - path: openspec/changes/modular-transcription-providers/exploration.md
    store: openspec
    type: exploration
next_recommended: sdd-research
risks:
  - The current provider contract mixes local file input, storage-key transport, batch transcription, and realtime chunks; changing it without an explicit compatibility boundary can break the Celery pipeline and CLI.
  - OpenAI audio model/API capabilities, current model identifiers, timestamp granularity, diarization behavior, limits, pricing, and compatible-endpoint support are not established by repository evidence.
  - The current OpenAI and Ollama abstractions are for chat/vision inference, not speech recognition, and their settings must not be reused implicitly.
  - `openspec/config.yaml` is absent even though the native dispatcher selected the repository-local OpenSpec store.
skill_resolution:
  mode: paths-injected
  injected_skill_paths:
    - C:\Users\daniel.rondon\.config\opencode\skills\sdd-explore\SKILL.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\sdd-phase-common.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\openspec-convention.md
---

## Exploration: Modular Transcription Providers

### Current State

#### Existing contract and selection

- `backend/app/services/transcription/provider.py:12-31` defines `TranscriptionProvider` with synchronous batch `process_audio(audio_path, config, on_status_change, on_heartbeat) -> TranscriptionResult`, asynchronous `transcribe_chunk(data) -> str`, and `get_provider_name()`. It is a structural `Protocol`, not a runtime registry or capability model.
- `backend/app/services/transcription/types.py:17-53` contains the normalized in-memory types. `WordTimestamp` has `word`, `start`, `end`, optional `speaker_label`, and optional `confidence`; `ResultSegment` has segment times, text, speaker, and words; `TranscriptionResult` adds language, provider/method labels, duration, and estimated cost. `TranscriptionConfig` carries language constraints, speaker bounds, `storage_key`, and `TranscriptionType` (`general` or `medical`).
- `backend/app/services/transcription/factory.py:19-68` has one module singleton, `_gpu_client`, and `get_provider()` dispatches only on `settings.TRANSCRIPTION_BACKEND`. `TranscriptionBackend` in `backend/app/models/base.py:16-19` has only `runpod` and `gpu-local`. `user_tier` is accepted by the factory but is currently unused.
- `backend/app/services/transcription/__init__.py:3-31` re-exports the factory, Protocol, and domain types. There is no OpenAI or Ollama speech provider in this package.

The current abstraction therefore names a provider, but the implementation is really a transport-specific GPU worker client. It also couples a provider-neutral config to `storage_key`, which is only required by the GPU/RunPod path.

#### Backend, local GPU, and RunPod flow

- `backend/app/worker.py:144-172` runs `stage_download`. It skips the shared temp-file download for RunPod because RunPod fetches a public presigned URL; for `gpu-local` it downloads a presigned URL to `/media/tmp/zabt_meeting_{id}.audio`.
- `backend/app/worker.py:214-371` runs `stage_transcribe`: it resolves the meeting language and allowed Whisper language set (`_resolve_meeting_language_for_transcription`), obtains the factory provider, builds config, adds `meeting.file_path` as `config.storage_key`, sets `transcription_type`, and calls `provider.process_audio` with status and heartbeat callbacks. It writes normalized segments to `TranscriptSegment`, serializes words into JSONB, stores `transcript_text`, and derives `Meeting.duration_seconds` from `result.audio_duration_seconds`.
- `backend/app/services/transcription/gpu_client.py:29-233` implements both backends in one class. `__init__` creates either `runpod.Endpoint` or an `httpx.Client`; `_submit` calls RunPod `.run()` or local `POST /run`; `_poll` uses RunPod request status/output or local `GET /status/{job_id}`; `_cancel` only works for RunPod. `process_audio` requires `config.storage_key`, generates a presigned URL, and does not use its `audio_path` argument to submit local bytes.
- `zabt-gpu-worker/src/main.py:3-19` selects the worker mode from its own `MODE` setting. `local` starts FastAPI; every other value starts the RunPod handler.
- `zabt-gpu-worker/src/server.py:56-147` implements the local `/run` and `/status/{job_id}` API with an in-memory job table and one-worker executor. `zabt-gpu-worker/src/handler.py:32-103` implements the RunPod job handler. Both construct `PipelineConfig`, download the URL, validate the audio stream, run the common pipeline, and return a formatted result.
- The current environment surface is split between `backend/app/core/config.py:93-107,159-161` (`TRANSCRIPTION_BACKEND`, GPU URL/timeouts, RunPod credentials/polling, speaker bounds) and `zabt-gpu-worker/src/config.py:6-23` (`MODE`, `WHISPER_MODEL`, `MEDASR_MODEL`, diarization model/token, speaker bounds, cost, port). `docker-compose.yml:152-159` defaults the backend to `gpu-local`, while `backend/app/core/config.py:94` defaults the Pydantic setting to `runpod`; this discrepancy is resolved by compose/runtime environment, not by a single application default.

#### Worker pipeline, normalization, and capability requirements

- `zabt-gpu-worker/src/pipeline.py:51-151` performs WhisperX transcription, language resolution, and alignment. `:244-311` selects general WhisperX or medical MedASR, then runs diarization. With no `HF_TOKEN`, diarization is skipped and segments receive `SPEAKER_UNKNOWN`; with a token, pyannote diarization and `assign_word_speakers` are used.
- `zabt-gpu-worker/src/pipeline.py:154-241` shows an important capability difference: MedASR creates one full-duration segment and no word timestamps. `:314-368` (`format_result`) converts raw output to the shared shape, prefixes full text with speaker labels, derives duration from the maximum segment end, and sets word confidence to `None`.
- `zabt-gpu-worker/src/models.py:6-45` duplicates the input/result schema as Pydantic transport models. The backend then duplicates normalization again in `GpuTranscriptionClient._parse_response` (`backend/app/services/transcription/gpu_client.py:198-233`) into dataclasses. A provider-neutral design should establish one canonical domain normalization boundary and keep worker wire models separate.
- The persisted contract is narrower than the in-memory result: `backend/app/models/base.py:190-198` stores segment times, text, optional speaker, and word JSONB; `backend/app/worker.py:279-297` stores word `speaker_label` under the key `speaker`. The shared TypeScript contract in `packages/shared/src/types.ts:5-17` exposes only word text and times, so confidence and word-level speaker metadata are currently dropped for clients.
- Provider status is not fully normalized. `GpuTranscriptionClient.process_audio` emits `transcribing` immediately and `diarizing` when a polled job becomes `IN_PROGRESS`; local pipeline stages also emit `aligning`, but the local HTTP status schema does not expose those progress stages. A future adapter contract should define optional progress/capability semantics rather than assuming every provider can align or diarize.

#### CLI and realtime gaps

- `backend/app/cli/transcribe.py:49-170` calls `get_provider()` and constructs a bare `TranscriptionConfig()`. Since `GpuTranscriptionClient.process_audio` requires `storage_key`, the current CLI path is not compatible with the active GPU provider contract even though it accepts a local `audio_path`. Its progress UI assumes `transcribing`, `aligning`, and `diarizing` stages.
- `backend/app/api/v1/endpoints/transcriptions.py:34-105` authenticates a WebSocket, calls `provider.transcribe_chunk`, and persists each response. `GpuTranscriptionClient.transcribe_chunk` (`:153-154`) always raises `NotImplementedError`, so there is no working realtime implementation. The endpoint timestamps chunks with `time.time()` (`:77-91`), which is wall-clock request time rather than media-relative audio time, and the integration test `backend/tests/integration/test_websocket.py:7-24` is only a connectivity stub with no assertion.
- The batch and realtime methods should likely be separate capability protocols or explicit optional capabilities. A batch provider that cannot accept arbitrary chunks should not need to pretend to implement realtime transcription.

#### Existing OpenAI/Ollama abstractions

- The backend already depends on `openai>=2.21.0` (`backend/pyproject.toml:7-18`). `backend/app/core/config.py:185-188` defines `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`; `backend/app/services/ai_agent.py:84-89,248-257` and `backend/app/services/meeting_intelligence.py:15-18,197-239` use the Langfuse-instrumented OpenAI client for chat completions and structured summaries. These settings are summary/LLM settings, not speech settings.
- `zabt-vision-worker` has the closest provider-selection pattern. `zabt-vision-worker/zabt_vision/inference/factory.py:69-89` selects `OllamaInference` or `OpenAIInference`, validates endpoint/egress policy, and fails instead of silently switching providers. `openai_backend.py:29-115` uses the official OpenAI client with a configurable `base_url`, sanitized errors, and structured response validation; `ollama_backend.py:19-51` uses `ollama.Client` and the Ollama chat API. `zabt-vision-worker/tests/test_factory.py:11-120` covers selection, missing credentials, endpoint policy, and local/cloud boundaries.
- No current repository implementation demonstrates OpenAI audio transcription or Ollama speech-to-text. The vision factory is a useful registry/validation pattern, but its chat/image request and privacy capabilities cannot be copied as an audio contract without research.

#### Tests and coverage

- `backend/tests/unit/test_provider_factory.py:1-46` covers only `build_config`; it does not verify provider selection, unknown values, constructor validation, or singleton isolation.
- `backend/tests/unit/test_gpu_client_language.py:9-45` verifies language payload fields. `backend/tests/unit/test_gpu_client_timeout.py:36-211` covers local versus RunPod timeout selection, presigned URL choice, polling timeout/cancel, status callbacks, and heartbeat.
- `backend/tests/unit/test_transcription_types.py:3-7` covers one config field, and `backend/tests/unit/test_transcription_language_resolution.py:21-73` covers database-to-Whisper language mapping.
- `zabt-gpu-worker/tests/test_pipeline_language.py:3-40` covers only `_resolve_language_after_detect`; there is no worker handler/server/formatter contract coverage found. `backend/tests/e2e/test_whisper_upload_flow.py:7-25` is a placeholder rather than a real transcription assertion.
- `backend/tests/test_transcription_service.py:5-37` imports `TranscriptionService` and patches old `settings`, `torch`, and `whisper` symbols that are absent from the current public package (`backend/app/services/transcription/__init__.py:14-31`). It appears stale and should be reconciled during planning rather than treated as current provider coverage.

#### Source-backed questions required before proposal

The repository is not sufficient to select the first cloud model. The next research phase should use official, current provider documentation and the installed SDK/API references to answer:

1. What are the currently supported OpenAI speech-to-text model identifiers, and which exact endpoint/request shape is authoritative for batch file transcription? Confirm the current `openai` Python SDK method and response types rather than assuming the chat-completions pattern used by summaries or vision.
2. Which candidate supports the required output contract: language detection/forcing, segment timestamps, word timestamps, predictable duration metadata, supported input formats/size/duration limits, and acceptable behavior for the product's language catalog? Capture official response examples and limitations.
3. Does the candidate provide speaker diarization or speaker-labeled words, and if not, should OpenAI results be accepted with `SPEAKER_UNKNOWN`, passed through a local/RunPod diarization stage, or excluded from meetings that require speaker data? Include the medical (`TranscriptionType.MEDICAL`) path in this decision.
4. What are the current price, latency, rate-limit, retention, and data-use constraints for a first test? Determine whether the API accepts only multipart uploads or also URLs, and how the backend should handle storage downloads, retries, timeouts, cancellation, and privacy/egress policy.
5. Does an OpenAI-compatible `base_url` reliably support audio transcription for OpenAI-compatible providers, including the actual request and response extensions? Do not infer audio compatibility from existing chat/vision compatibility. Separately verify whether current Ollama releases/models expose a suitable speech-to-text API and timestamp behavior.
6. Can the selected API support the existing realtime WebSocket requirement, or should realtime remain a separate provider capability and a later scope? Establish whether chunk boundaries can preserve media-relative timestamps and whether a batch-only first slice is acceptable.
7. Define a conformance fixture and acceptance matrix using a short, known audio sample: text, language, segments, words, speakers, duration, provider metadata, error normalization, and cost. The result should make a provider eligible only when its verified capabilities meet the requested mode.

The existing `gpt-4o-mini` values in the vision worker and production compose files are not evidence for speech recognition and must not be reused as the first audio model without this research.

### Affected Areas

- `backend/app/services/transcription/provider.py` and `types.py` — stabilize or split the provider-neutral request/result and optional realtime contracts.
- `backend/app/services/transcription/factory.py` and `__init__.py` — replace the enum-only singleton dispatch with a validated provider registry while preserving the public factory entry points.
- `backend/app/services/transcription/gpu_client.py` — retain RunPod and local GPU behavior, but isolate transport, URL/source handling, progress, and wire-result normalization behind an adapter.
- `backend/app/core/config.py`, `.env.example`, and `docker-compose.yml` — introduce a distinct transcription provider/config namespace; preserve existing RunPod/local deployment aliases and avoid overloading summary `OPENAI_*` settings.
- `backend/app/worker.py` — make source handling provider-aware, preserve language preferences, medical mode, heartbeat, retry/cleanup, and database persistence without assuming storage URLs or local files.
- `zabt-gpu-worker/src/models.py`, `server.py`, `handler.py`, and `pipeline.py` — keep the shared WhisperX/pyannote/MedASR path and wire schema stable while exposing a conformance-normalized result and any provider capability limitations.
- `backend/app/cli/transcribe.py` — resolve the current local-file versus storage-key mismatch and define behavior for providers without alignment/diarization/realtime support.
- `backend/app/api/v1/endpoints/transcriptions.py` — either implement a real provider capability for chunks or decouple realtime from the batch provider contract; correct media-relative timestamp semantics if realtime remains in scope.
- `backend/app/services/ai_agent.py`, `meeting_intelligence.py`, and existing `OPENAI_*` settings — verify that summary/structured-output configuration remains independent from transcription provider credentials and model selection.
- `zabt-vision-worker/zabt_vision/inference/factory.py`, `openai_backend.py`, `ollama_backend.py`, `settings.py`, and `tests/test_factory.py` — reuse only the registry, validation, and sanitized-error patterns as architectural precedent; do not share the image/chat contract.
- `backend/tests/unit/test_provider_factory.py`, GPU client tests, worker/contract tests, CLI tests, WebSocket tests, and `zabt-gpu-worker/tests` — add provider selection, conformance normalization, capability failure, compatibility, and first-test fixture coverage. Remove or update stale `test_transcription_service.py` assumptions.

### Approaches

1. **Provider registry with stable batch adapters** — Keep one normalized domain result and register concrete adapters such as `RunPodProvider`/`GpuWorkerProvider` and `OpenAITranscriptionProvider`; select one by a dedicated `.env` provider key, with provider-specific validation and explicit capabilities.
   - Pros: smallest migration from the current factory; preserves RunPod and local GPU behavior; isolates OpenAI API mapping; makes tests and future providers additive; supports no-automatic-fallback policy.
   - Cons: the current `process_audio`/`storage_key` contract must be repaired; providers with different timestamp or diarization capabilities need explicit behavior.
   - Effort: Medium

2. **Generic OpenAI-compatible audio adapter** — Use one adapter with `base_url`, API key, model, and a response normalizer for OpenAI, Ollama, LM Studio, or other compatible endpoints.
   - Pros: attractive `.env` ergonomics and low class count; aligns with existing OpenAI-compatible chat/vision patterns.
   - Cons: audio compatibility is not guaranteed by chat compatibility; timestamp, diarization, upload, and error semantics vary; a generic adapter could hide unsupported capabilities and produce misleading normalized data.
   - Effort: Medium initially, High once compatibility exceptions are handled.

3. **Composable transcription, alignment, and diarization stages** — Let a provider supply raw text, then select independent timestamp and speaker stages, possibly local or RunPod, to fill the normalized result.
   - Pros: best long-term capability coverage and can preserve speaker/timestamp requirements when a cloud transcriber lacks them.
   - Cons: broader orchestration change, more media transfers/latency/cost, harder idempotency and failure semantics, and unnecessary scope before the first OpenAI proof.
   - Effort: High

### Recommendation

Start with Approach 1. Define a provider-neutral batch request/result contract that treats source location, language constraints, speaker requirements, timestamps, medical/general mode, callbacks, and cost as explicit concerns. Keep realtime as a separate optional capability instead of requiring every batch provider to implement `transcribe_chunk`.

Retain the existing GPU worker and RunPod path behind an adapter that preserves its wire payload and normalized result during the first migration. Add an OpenAI adapter using the official `openai` SDK and a separate transcription configuration namespace, with an optional configurable `base_url` only after research proves the endpoint is compatible. The registry should fail fast for unknown providers or missing credentials and must not silently fall back or switch providers.

Before proposal, run the source-backed research lane and record the exact first-test model/API, its verified output gaps, and the acceptance fixture. The proposal should then decide whether OpenAI is batch-only, whether diarization is optional or composed with the retained GPU/RunPod pipeline, and whether realtime is explicitly deferred. Do not select a model from the existing summary or vision defaults.

### Risks

- A cloud response may lack one or more required fields, especially speaker labels, word timestamps, or reliable duration; normalization must not fabricate them.
- `TranscriptionConfig.storage_key` and the unused `audio_path` argument encode GPU assumptions that can make CLI and future file-upload providers fail unexpectedly.
- The backend and GPU worker duplicate Pydantic/dataclass normalization, so a wire-schema change can produce silent divergence unless one conformance boundary is established.
- Language forcing currently uses Whisper codes and a re-transcription fallback; OpenAI language codes and allowed-language behavior need an explicit mapping and tests.
- Medical MedASR currently returns a single segment with no word timestamps; a generic result contract must preserve this limitation rather than promising parity.
- The WebSocket method is unimplemented for the current provider and uses wall-clock timestamps; adding a provider without resolving this could expose a misleading realtime feature.
- Existing summary `OPENAI_*` and vision `VISION_OPENAI_*` settings have different owners and privacy policies; sharing them would create accidental provider coupling or data-egress changes.
- `openspec/config.yaml` is missing, so no repository-specific SDD rules beyond the existing specs were available for this exploration.

### Ready for Proposal

No. The repository map and modular direction are ready, but proposal should wait for `sdd-research` to answer the official OpenAI model/API and compatibility questions above. After research records a source-backed model candidate and capability matrix, the next phase can produce a proposal and continue the complete SDD pipeline.
