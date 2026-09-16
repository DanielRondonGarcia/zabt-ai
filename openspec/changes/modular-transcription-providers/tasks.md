# Tasks: Modular Transcription Providers

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated authored lines | 650–850 |
| Risk | High: adapter, routing, integrations, regressions, fixture, and docs span 12+ paths |
| Delivery | ask-on-risk; stacked-to-main chain approved |
| Split | PR 1 contracts/config → PR 2 providers/wiring → PR 3 conformance/docs |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Workload decision: Split into chained PR slices approved by the maintainer; no `size:exception`.

### Suggested Work Units

| Unit | Focused verification | Runtime harness | Rollback boundary |
|---|---|---|---|
| 1. Contracts/config | `cd backend && uv run pytest tests/services/transcription -k "contract or registry"` | N/A — deterministic fakes | Revert contracts, registry, resolver, and settings |
| 2. Providers/wiring | `cd backend && uv run pytest tests -k "transcription or medasr"` | N/A — GPU/RunPod services external | Revert legacy/integration paths, retaining no OpenAI files |
| 3. OpenAI/conformance | `cd backend && uv run pytest tests/services/transcription -k openai` | Credential-gated WAV/MP3 fixture | Remove adapter, registration, fixture, and docs |

## Phase 1: Contracts, Registry, Configuration

- [x] 1.1 Create `backend/app/services/transcription/types.py`, `provider.py`, `errors.py`: define `ProviderCapabilities`, `BatchTranscriptionProvider`, `BatchTranscriptionRequest`, `TranscriptionResult`, `AudioSource`, usage, and typed errors; pre: design; check contract/gate tests; accept absent fields and typed speaker/realtime failures.
- [x] 1.2 Create `backend/app/services/transcription/factory.py`: add `get_provider()` builders for `gpu-local`, `runpod`, `openai-file`; pre: 1.1; check fresh scope, cleanup, `TRANSCRIPTION_BACKEND` alias, no-fallback tests; accept recorded provider/model.
- [x] 1.3 Create `backend/app/services/transcription/source.py` `AudioSourceResolver`; pre: 1.1; check local path/storage-key resolution; accept OpenAI files without changing GPU/RunPod signed URLs.
- [x] 1.4 Modify `backend/app/core/config.py`, `.env.example`, `docker-compose.yml`; pre: 1.2; check dedicated provider/model/credential/language/timestamp option validation and `OPENAI_*` isolation; accept explicit invalid-config failure.

## Phase 2: Provider Implementations

- [x] 2.1 Modify `backend/app/services/transcription/gpu_client.py`, `backend/app/models/base.py`; pre: 1.1–1.2; check wire, cleanup, and MedASR regressions; accept local/RunPod medical behavior unchanged.
- [x] 2.2 Create `backend/app/services/transcription/openai_provider.py` `OpenAIFileProvider.transcribe`; pre: 1.1–1.4; check official SDK multipart `audio.transcriptions.create`, model/format validation, language/timestamps/response mapping, usage, duration, absent fields, and no fallback; accept `gpt-transcribe` and `gpt-4o-mini-transcribe`.

## Phase 3: Worker, CLI, Persistence

- [x] 3.1 Modify `backend/app/worker.py` batch entry point; pre: 2.1–2.2; check scoped cleanup, temporary-file ordering, normalized progress/errors; accept existing persistence readability.
- [x] 3.2 Modify `backend/app/cli/transcribe.py` and `backend/app/api/v1/endpoints/transcriptions.py`; pre: 3.1; check shared registry/local files and batch-only routing; accept explicit realtime unsupported error.
- [x] 3.3 Modify persistence mapping in `backend/app/models/base.py`; pre: 3.1; check optional fields and `SPEAKER_UNKNOWN`; accept no transcript/schema migration and no fabricated values.

## Phase 4: Regression, Conformance, Boundaries

- [x] 4.1 Add `backend/tests/services/transcription/test_registry.py`; pre: 1.2; RED/check no-fallback, cloud-diarization gate, future-only diarize, realtime, generic compatible-audio, cloud-medical parity, and Ollama rejection; accept no out-of-scope registration.
- [x] 4.2 Add `backend/tests/services/transcription/test_openai_provider.py`; pre: 2.2; check mocked multipart WAV/MP3, mapping, usage units, missing fields, invalid options, and failures; accept normalized errors.
- [x] 4.3 Modify `backend/tests/**`, `zabt-gpu-worker/tests/**`; pre: 2.1–3.1; check GPU/RunPod payloads, MedASR, progress, cleanup, and persistence; accept legacy behavior.
- [x] 4.4 Add `backend/tests/fixtures/transcription/` and credential-gated conformance test; pre: 4.2; run both candidates on WAV/MP3; accept oracle text, language/timing gaps, speaker policy, metadata, and original usage units.

## Phase 5: Documentation and Rollback

- [x] 5.1 Update `docs/configuration.md`, `README.md`, and `docker-compose.yml`; pre: 1.4–4.4; check settings, capability limits, medical separation, and excluded realtime/Ollama claims; accept reproducible setup.
- [x] 5.2 Verify `backend/app/services/transcription/factory.py`, `backend/app/core/config.py`, and `docker-compose.yml` rollback to explicit `gpu-local`/`runpod`; pre: all phases; check fresh startup and persisted-result readability; accept no database migration, no silent fallback, and removable OpenAI registration/settings.
