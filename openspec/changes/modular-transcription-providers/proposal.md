# Proposal: Modular Transcription Providers

## Intent

Zabt currently selects one GPU transport while coupling storage URLs, batch work, and an unimplemented realtime method. Establish a provider-neutral batch boundary without changing persisted transcript semantics. `gpt-transcribe` is the first conformance candidate—not an irreversible production winner; model selection remains environment-configurable.

## Scope

### In Scope
- Add a validated registry and adapters for `gpu-local`, `runpod`, and OpenAI’s official file-transcription SDK/API; never fall back silently.
- Make provider/model selection user-visible through dedicated settings. Unsupported capabilities fail explicitly; missing speakers remain `SPEAKER_UNKNOWN`.
- Normalize text, optional segments/words/speakers/duration, language, metadata, usage units, progress, and errors without fabricating fields.
- Preserve local/RunPod medical MedASR; OpenAI handles general transcription only. Verify a shared fixture with `gpt-transcribe` first and `gpt-4o-mini-transcribe` as a configurable comparison.

### Out of Scope
- Realtime transcription, UI redesign, or a new persisted transcript schema.
- Cloud medical parity or replacing MedASR. If speakers become required later, only cloud-diarizing providers qualify; `gpt-4o-transcribe-diarize` remains optional/future.
- Inventing retry/cancellation, full privacy/retention, generic compatible-audio `base_url`, file-size ceilings, or production benchmarks. Ollama/other providers require a verified audio contract first.

## Capabilities

### New Capabilities
- `modular-transcription-providers`: Configurable provider-neutral batch transcription with explicit capabilities and conformance normalization.

### Modified Capabilities
- None — existing `video-transcription-e2e` is test-only and remains unchanged.

## Approach

Split batch from a separate optional realtime contract; use adapters around one canonical result with explicit capabilities. Preserve GPU/RunPod wire payloads and factory entry points. The OpenAI adapter uses `audio.transcriptions.create`; `gpt-transcribe` leads conformance and `gpt-4o-mini-transcribe` is configuration-selected for comparison. Dedicated `TRANSCRIPTION_*` settings own provider, model, optional credential override, and proven options; `OPENAI_API_KEY` is the default shared credential when the override is empty, while `OPENAI_BASE_URL` remains summary-only and vision settings stay independent. Reject speaker-required mode unless cloud diarization is declared.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/services/transcription/` | Modified | Contracts, registry, adapters, normalization. |
| `backend/app/core/config.py`, `.env.example`, `docker-compose.yml` | Modified | Dedicated settings and validation. |
| `backend/app/worker.py`, CLI, tests, `zabt-gpu-worker/` | Modified | Provider-aware flow and regression coverage; GPU/MedASR behavior stays intact. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Cloud responses omit timestamps, speakers, or duration | High | Capability gates, absent fields, `SPEAKER_UNKNOWN`, no fabrication. |
| Migration regresses GPU/RunPod behavior | Medium | Preserve wire contracts and run transport/medical regressions. |
| Cost/latency is not production-qualified | Medium | Record original units; defer final selection to evidence. |

## Rollback Plan

Remove the OpenAI adapter/registration and dedicated settings; explicitly configure `gpu-local` or `runpod`. No storage or transcript-schema migration is required.

## Dependencies

- Official OpenAI SDK, transcription credentials, and a short known audio fixture.

## Success Criteria

- [ ] Fixture passes for `gpt-transcribe` and the configured comparison, recording normalized fields, capability gaps, metadata, and original cost units.
- [ ] `gpu-local`, `runpod`, CLI, persistence, and medical MedASR remain compatible.
- [ ] Invalid configuration fails explicitly, never switches providers, and never reuses summary/vision endpoints or unsupported settings; the documented shared-key fallback remains explicit.
