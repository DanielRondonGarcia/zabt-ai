# Design: Modular Transcription Providers

## Technical Approach

Replace the current fat protocol and module-level `_gpu_client` with a registry of provider builders. `get_provider()` remains the compatibility entry point but returns a fresh, operation-scoped adapter selected by canonical `TRANSCRIPTION_PROVIDER` (`gpu-local`, `runpod`, or `openai-file`). Batch adapters share one normalized domain boundary; realtime stays a separate, out-of-scope capability. The GPU/RunPod adapter preserves current wire payloads and MedASR, while the OpenAI adapter uses the official file API. `gpt-transcribe` is first; `gpt-4o-mini-transcribe` is the configurable comparison, not a final default.

## Architecture Decisions

| Decision | Choice | Alternative rejected | Rationale |
|---|---|---|---|
| Registry and lifecycle | Builders only; scoped providers implement `close()` and sync context-manager methods | Global singleton | Prevents cross-provider reuse and guarantees HTTP/SDK cleanup in workers and CLI. |
| Capability gate | Explicit `cloud_diarization` plus output capabilities | Infer speakers from returned fields | `speaker_required` must be enforceable before I/O, not satisfied by hidden local processing. |
| Batch boundary | Separate batch protocol and optional realtime protocol | Require `transcribe_chunk` on every provider | Batch-only providers remain honest; realtime is not exposed by this slice. |
| Source and medical policy | `AudioSource` resolves local path/storage key; OpenAI is general-only | Mutate provider-specific config or replace MedASR | Preserves GPU/RunPod transport and the existing local/RunPod medical path. |

## Data Flow

```text
Worker/CLI -> AudioSourceResolver -> Registry validation -> scoped adapter
          -> canonical normalizer -> existing persistence/output
          -> close() in the caller's finally/context-manager scope
```

The worker materializes a local file only for providers that require it; GPU-local/RunPod retain their existing signed-URL behavior. The CLI supplies its local file directly. Provider failures and unsupported requests stop the flow; no silent provider fallback is attempted.

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    batch: bool
    realtime: bool
    medical: bool
    segments: bool
    words: bool
    speakers: bool
    duration: bool
    cloud_diarization: bool

class BatchTranscriptionProvider(Protocol):
    capabilities: ProviderCapabilities
    def transcribe(self, request: BatchTranscriptionRequest, *, on_status_change=None,
                   on_heartbeat=None) -> TranscriptionResult: ...
    def close(self) -> None: ...
    def __enter__(self) -> "BatchTranscriptionProvider": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
```

`BatchTranscriptionRequest` contains `AudioSource`, language policy, `TranscriptionType`, timestamp mode, and `speaker_required`. `UnsupportedCapabilityError` carries `capability`, provider, and model. Before submission, `speaker_required=True` requires `cloud_diarization=True`; otherwise the adapter raises that typed error with no local/RunPod post-diarization and no fallback. First-slice profiles set `cloud_diarization=False` for `gpu-local`, `runpod`, `gpt-transcribe`, and `gpt-4o-mini-transcribe`; only a future verified `gpt-4o-transcribe-diarize` profile may set it true. When speakers are optional, absent labels remain `SPEAKER_UNKNOWN` (and absent word labels remain `None`). Results preserve optional timing/duration fields, provider/model metadata, and raw usage units.

The worker and CLI own the provider scope: `with get_provider() as provider`, with cleanup guaranteed on success and failure before temporary-file cleanup. The realtime endpoint must use a separate protocol or return the same typed unsupported-capability error; no realtime implementation is added.

## File Changes

| File | Action | Description |
|---|---|---|
| `backend/app/services/transcription/{types,provider,factory,source,errors,openai_provider}.py` | Create/modify | Canonical contracts, registry, source resolution, OpenAI adapter, typed errors, lifecycle. |
| `backend/app/services/transcription/gpu_client.py`, `backend/app/models/base.py` | Modify | Preserve local/RunPod wire and MedASR behavior while exposing capabilities and cleanup. |
| `backend/app/core/config.py`, `.env.example`, `docker-compose.yml` | Modify | Dedicated `TRANSCRIPTION_*` provider/model/optional credential override/options; `OPENAI_API_KEY` is the explicit `openai-file` fallback, while summary `OPENAI_BASE_URL`/model and vision settings remain independent. |
| `backend/app/worker.py`, `backend/app/cli/transcribe.py`, `backend/app/api/v1/endpoints/transcriptions.py` | Modify | Shared registry, source-aware flow, guarded optional fields, scoped cleanup, and realtime separation. |
| `backend/tests/**`, `zabt-gpu-worker/tests/**` | Add/modify | Registry, conformance, lifecycle, and GPU/RunPod/MedASR regression coverage. |

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Selection, no fallback, normalization, `cloud_diarization` gate, `close()` | Mock settings/SDK/storage; RED tests assert typed speaker failure and no post-diarization. |
| Integration | Worker/CLI source handling, cleanup, persistence, unchanged GPU/RunPod payloads and medical jobs | Fake adapters and transport clients; guard unknown duration. |
| Conformance | `gpt-transcribe` and configured mini comparison on WAV/MP3 | Credential-gated fixture records text, language, timing gaps, `SPEAKER_UNKNOWN`, metadata, and original usage units. |

## Threat Matrix

Provider routing/process integration is covered by the capability and no-fallback tests. The required VCS/shell boundary rows are all N/A:

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no executable-file classification | None | None |
| Git repository selection | N/A — no Git path routing | None | None |
| Commit state | N/A — no commit automation | None | None |
| Push state | N/A — no push automation | None | None |
| PR commands | N/A — no PR automation | None | None |

## Migration / Rollout

No database or transcript-schema migration is required. Keep the compose default on the existing GPU path and retain `TRANSCRIPTION_BACKEND` only as a non-conflicting local/RunPod compatibility alias. Roll out GPU/RunPod regressions first, then enable OpenAI for the fixture. Rollback is configuration-only; no persisted data conversion is needed.

## Deferred Risks and Open Questions

Realtime, UI changes, cloud medical parity, generic compatible-audio `base_url`, Ollama audio, retry/cancellation semantics, privacy/retention policy, file ceilings, and production quality/latency benchmarking remain deferred. Exact language mapping, provider timestamp/duration gaps, and incomparable cost units remain unknown or fail explicitly rather than being fabricated.

- [ ] Confirm language mapping and production default after conformance.
- [ ] Define later retry, cancellation, privacy, and file-limit policy.
- [ ] Reconsider the future cloud-diarizing profile only with a separately verified contract.
