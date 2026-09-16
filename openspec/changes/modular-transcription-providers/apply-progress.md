# Apply Progress: Modular Transcription Providers

## Batch

- Change: `modular-transcription-providers`
- Work unit: `PR1-contracts-config-evidence` / remediation for tasks 1.1–1.4
- Mode: Standard (strict TDD cache: false; no `openspec/config.yaml` was present)
- Artifact store: OpenSpec
- Delivery: stacked PR slice 1, `stacked-to-main`; no `size:exception`
- Parent native attempt token: `sha256:69d7fdafb6044dbd9910ce261457b19eafa7f30cd81a776dffeaec8c5e13af2c`
- Untracked scope: `exclude`, using the parent-bound inventory

## Completed Tasks

- [x] 1.1 Provider-neutral batch/realtime contracts, audio sources, capabilities, usage metadata, scoped lifecycle, and typed errors.
- [x] 1.2 Registry/factory seams for `gpu-local`, `runpod`, and `openai-file`, with fresh scopes, compatibility alias handling, and no fallback.
- [x] 1.3 `AudioSourceResolver` that keeps local paths distinct from storage keys and preserves GPU/RunPod signed-URL inputs.
- [x] 1.4 Dedicated `TRANSCRIPTION_*` configuration, environment template, and Compose propagation with summary/vision `OPENAI_*` isolation.

## Files Changed

| File | Action | What Was Done |
|---|---|---|
| `backend/app/services/transcription/types.py` | Modified | Preserved legacy result/config constructors, exposed the new contract types, and kept unavailable duration as `None` without fabrication. |
| `backend/app/services/transcription/contracts.py` | Created | Added provider names, batch/realtime requests, audio sources, capabilities, progress, and usage metadata. |
| `backend/app/services/transcription/provider.py` | Modified | Exposed batch/realtime protocols and capability validation through the public provider module. |
| `backend/app/services/transcription/provider_contract.py` | Created | Added scoped lifecycle protocols and the pre-I/O speaker/capability gate. |
| `backend/app/services/transcription/errors.py` | Created | Added typed configuration, provider-selection, capability, and source errors. |
| `backend/app/services/transcription/source.py` | Created | Added explicit local-path/storage-key resolution and provider-aware storage-key preference. |
| `backend/app/services/transcription/factory.py` | Modified | Kept the compatibility entry point while delegating to the fresh-provider registry. |
| `backend/app/services/transcription/gpu_client.py` | Modified | Preserved an unavailable provider duration as `None` instead of defaulting it to zero. |
| `backend/app/services/transcription/registry.py` | Created | Registered the three first-slice provider seams, scoped GPU/RunPod adapters, and explicit OpenAI implementation absence. |
| `backend/app/services/transcription/__init__.py` | Modified | Exported the new contracts without removing legacy public types. |
| `backend/app/core/config.py` | Modified | Added canonical provider/model/credential/language/timestamp/response/speaker settings and explicit validation. |
| `.env.example` | Modified | Documented dedicated transcription settings and retained the backend alias as a commented compatibility option. |
| `docker-compose.yml` | Modified | Propagated dedicated transcription settings independently to API and worker services. |
| `backend/tests/services/transcription/test_contracts.py` | Created | Covered typed speaker capability rejection, source separation, and unavailable duration preservation. |
| `backend/tests/services/transcription/test_registry.py` | Created | Covered registry membership, no-fallback OpenAI seam, fresh scopes, and canonical/legacy conflict rejection. |

## Work Unit Evidence

| Evidence | Exact result |
|---|---|
| Focused test command and exact result | Requested `cd backend && uv run pytest tests/services/transcription -k "contract or registry"`: **exit 1 before collection** because `tests/conftest.py` initializes storage and MinIO `http://minio:9000/zabt-ai-bucket` is unavailable (`botocore.exceptions.EndpointConnectionError`). The bounded no-conftest check `cd backend && uv run pytest --noconftest tests/services/transcription -k "contract or registry"` passed: **6 passed, 1 warning**. |
| Compatibility regression check | `uv run pytest --noconftest tests/unit/test_provider_factory.py tests/unit/test_transcription_types.py tests/unit/test_gpu_client_language.py tests/unit/test_gpu_client_timeout.py -q`: **10 passed, 1 warning**. |
| Static/compose checks | `uv run python -m compileall -q app`, `git diff --check`, and `docker compose config --quiet`: **passed**. |
| Runtime harness command/scenario and exact result | **N/A** — PR1 adds contracts and registry seams only; OpenAI is intentionally not implemented, while GPU/RunPod services are external. Deterministic fake registry/source scenarios are covered by the focused tests. |
| Rollback boundary | Revert the listed transcription contract/registry/source/error files, the dedicated settings in `backend/app/core/config.py`, `.env.example`, `docker-compose.yml`, and the two PR1 test files; unrelated frontend, archived changes, and later provider/wiring work remain untouched. |

## Deviations and Issues

- No intentional design deviation. `openai-file` is registered as an explicit not-yet-implemented seam; no fake provider or silent fallback was added.
- The exact parent-authorized test command is environment-blocked before collection by the existing MinIO initialization in `tests/conftest.py`; the isolated focused tests pass and are reported as supplemental evidence, not as a substitution.
- GPU/RunPod capabilities explicitly set `cloud_diarization=False`; speaker-required requests fail before provider I/O.

## Remediation Corrections

- `TranscriptionResult.audio_duration_seconds` and `duration_seconds` now allow `float | None`; the GPU compatibility parser preserves an absent duration as `None`.
- The registry now rejects differing `TRANSCRIPTION_PROVIDER` and `TRANSCRIPTION_BACKEND` values with `TranscriptionConfigurationError` before provider selection.
- A first corrected test invocation exposed a circular import caused by importing the typed error from `app.core.config`; that import was removed, leaving typed conflict enforcement at the registry boundary and restoring normal settings initialization.

## Remediation Evidence (generation 2)

| Evidence | Exact result |
|---|---|
| Original required focused command | `cd backend && uv run pytest tests/services/transcription -k "contract or registry"`: **exit 1 before collection** because the host process inherited the Compose-only MinIO endpoint `http://minio:9000/zabt-ai-bucket`. |
| Fresh corrected-environment focused command | From `backend`, process-only `MINIO_ENDPOINT=localhost:9000` and `DATABASE_URL=postgresql+asyncpg://app:app@localhost:5433/zabt`: **8 passed, 2 warnings**. No `.env` or Compose file was changed for this override. |
| Compile check | From `backend`, `uv run python -m compileall -q app`: **passed**. |
| Compose validation | From repository root, `docker compose config --quiet`: **passed**. |
| Diff validation | From repository root, `git diff --check`: **passed**; only existing CRLF normalization warnings were emitted. |
| Runtime harness command/scenario | **N/A** — PR1 remains contracts/config and deterministic fake-boundary coverage; no provider I/O or runtime service boundary was added. |
| Rollback boundary | Revert the duration contract/parser correction, registry conflict guard, and their focused tests; the broader PR1 rollback remains the contract/registry/source/error/settings/Compose/test set listed above. |

## Workload / PR Boundary

- Strategy: `stacked-to-main`
- Current slice: PR1 / `PR1-contracts-config-evidence` remediation only
- Start: existing legacy transcription types, singleton factory, and shared OpenAI summary settings
- Finish: provider-neutral seams, compatibility registry, source resolver, dedicated configuration, and focused boundary tests
- Out of scope: tasks 2.x and later, OpenAI provider I/O, worker/CLI rewiring, realtime implementation, medical replacement, push, and PR creation
- Native tracked changed-line accounting before excluded untracked files: **308 lines**, below the 350-line attempt cap

## Remediation Workload / PR Boundary

- Strategy: `stacked-to-main`; no `size:exception`.
- Current work unit: `PR1-contracts-config-evidence` only.
- Correction boundary: unavailable duration preservation and canonical/legacy provider conflict rejection, with focused tests.
- Native generation 2 accounting: **6 additional lines**, **314 cumulative**, below the 350-line cap.
- No tasks 2.x or later, OpenAI I/O, worker/CLI rewiring, realtime, medical replacement, push, or PR creation was started.

## Native Attempt Settlement

- Outcome: `failed` (the exact focused harness was unavailable before collection)
- Evidence revision: `sha256:89bc320bb060c74208c259b4eeff36af158397dc5d18ae3df0bd0c490893e23a`
- Final settlement request: `settle-pr1-contracts-config-20260914-v2`
- Settlement result: **state `proceed`**, leaving the parent attempt budget available for a later bounded retry after the MinIO test dependency is restored.
- The native continuation required refreshing the excluded untracked inventory to `sha256:1d516f99dc6b7ca38a3373b0708af37d631ca85f416d763295f7d60d3dc8f8ae`; no second attempt was acquired.
- Native post-settlement status reports no active attempt and requires any future passing retry to declare `--remediates-evidence-revision sha256:89bc320bb060c74208c259b4eeff36af158397dc5d18ae3df0bd0c490893e23a` with distinct fresh verification evidence.

## Remediation Native Attempt Settlement

- Objective generation: **2**, work unit `PR1-contracts-config-evidence`.
- Actor acquire: **state `proceed`**, exact parent token reused; no second attempt was acquired.
- Settlement: **state `complete`**, outcome `passed`, with `--remediates-evidence-revision sha256:89bc320bb060c74208c259b4eeff36af158397dc5d18ae3df0bd0c490893e23a`.
- Fresh evidence revision: `sha256:82c6e6e7d0facac6fac02cc6570d8637f90edcf473df448cdef7b965c718f111`.
- Native changed-line accounting: **314 cumulative**, including **6** lines for generation 2, below the 350-line cap.

## Status

Tasks 1.1–1.4 are implemented and the bounded remediation evidence is complete. The slice is **ready for parent routing**; tasks 2.x and later remain unchecked and were not started.

## PR2 Batch

- Change: `modular-transcription-providers`
- Work unit: `PR2-providers-wiring`
- Mode: Standard (strict TDD disabled; no `openspec/config.yaml` apply rule was present)
- Artifact store: OpenSpec
- Delivery: stacked PR slice 2, `stacked-to-main`; no `size:exception`
- Parent/native attempt token: `sha256:53f2fd998e8e39c4ec921baf6d9c5b9af595bfe875e7af6d89d040ffd753717a`
- Untracked scope: `exclude`, expected inventory `sha256:1d516f99dc6b7ca38a3373b0708af37d631ca85f416d763295f7d60d3dc8f8ae`

## Cumulative Completed Tasks

- [x] 1.1 Provider-neutral batch/realtime contracts, audio sources, capabilities, usage metadata, scoped lifecycle, and typed errors.
- [x] 1.2 Registry/factory seams for `gpu-local`, `runpod`, and `openai-file`, with fresh scopes, compatibility alias handling, and no fallback.
- [x] 1.3 `AudioSourceResolver` that keeps local paths distinct from storage keys and preserves GPU/RunPod signed-URL inputs.
- [x] 1.4 Dedicated `TRANSCRIPTION_*` configuration, environment template, and Compose propagation with summary/vision `OPENAI_*` isolation.
- [x] 2.1 Preserved GPU-local/RunPod payloads and MedASR routing, added typed batch-only realtime behavior, and made cleanup idempotent through the scoped client.
- [x] 2.2 Added the official SDK `OpenAIFileProvider` for local WAV/MP3 and the documented formats, `gpt-transcribe`/`gpt-4o-mini-transcribe`, language/options, timestamps, usage, duration, absent speakers, typed failures, and no fallback.
- [x] 3.1 Rewired the worker to provider-neutral source/request contracts, preserved RunPod storage-key behavior, preserved local-file behavior, ordered provider/temp cleanup, progress, heartbeat, persistence, and optional-duration accounting.
- [x] 3.2 Rewired the CLI to submit a local `AudioSource`; the realtime WebSocket now rejects batch-only providers with an explicit unsupported-capability error and does not implement realtime.
- [x] 3.3 Kept the existing nullable transcript persistence schema compatible while normalizing missing segment speakers to `SPEAKER_UNKNOWN` and preserving missing duration/word-speaker values.

## PR2 Files Changed

| File | Action | What Was Done |
|---|---|---|
| `backend/app/services/transcription/openai_provider.py` | Created | Implemented the official file-upload adapter with model/format/capability validation, response normalization, usage/duration metadata, lifecycle, and no fallback. |
| `backend/app/services/transcription/registry.py` | Modified | Registered the real OpenAI file provider and removed the PR1 placeholder; retained fresh GPU/RunPod scopes and idempotent cleanup. |
| `backend/app/services/transcription/source.py` | Modified | Kept local paths local for providers that require a file, while retaining storage-key preference for GPU-local and RunPod. |
| `backend/app/services/transcription/gpu_client.py` | Modified | Added typed batch-only realtime rejection and idempotent HTTP cleanup without changing GPU/RunPod wire payloads. |
| `backend/app/services/transcription/types.py` | Modified | Preserved unknown duration and optional cost in the canonical result while retaining legacy-compatible fields. |
| `backend/app/models/base.py` | Modified | Documented nullable speaker persistence compatibility without changing database schema. |
| `backend/app/worker.py` | Modified | Routed batch work through `BatchTranscriptionRequest`, preserved medical/local/RunPod behavior, preserved extension-aware temp files, cleanup, progress, heartbeat, and optional persistence accounting. |
| `backend/app/cli/transcribe.py` | Modified | Used `build_config`, local `AudioSource`, scoped provider lifecycle, and safe unknown duration/cost rendering. |
| `backend/app/api/v1/endpoints/transcriptions.py` | Modified | Added an explicit batch-only WebSocket capability rejection and provider cleanup. |
| `backend/tests/services/transcription/test_registry.py` | Modified | Updated the PR1 placeholder expectation to assert explicit missing-credential failure without fallback. |

## PR2 Work Unit Evidence

| Evidence | Exact result |
|---|---|
| Focused test command and exact result | Required command `cd backend && uv run pytest tests -k "transcription or medasr"`: original host run exited before collection because Compose-only MinIO `http://minio:9000/zabt-ai-bucket` was unavailable. The required process-only rerun with `MINIO_ENDPOINT=localhost:9000` and `DATABASE_URL=postgresql+asyncpg://app:app@localhost:5433/zabt` reached collection but exited with three unrelated collection errors: missing `playwright`, stale `TranscriptionService` import, and the `Settings` import failure in `test_local_auth`. Supplemental corrected selection excluding those three collection blockers passed **15 passed, 195 deselected, 2 warnings**; the focused provider/legacy regression selection passed **15 passed, 2 warnings**. |
| Runtime harness command/scenario and exact result | Deterministic fake SDK scenario using a real temporary WAV file and an injected fake `audio.transcriptions.create` client passed: file bytes, model, language, word timestamp control, normalized duration/usage, `SPEAKER_UNKNOWN`, and cleanup were asserted. `tests/integration/test_websocket.py` also passed as part of the corrected supplemental selection. No live credentials or network behavior was added. |
| Static and configuration checks | `uv run python -m compileall -q app`: **passed**; `docker compose config --quiet`: **passed**; `git diff --check`: **passed** with existing CRLF normalization warnings; focused `uv run ruff check ...`: **passed**. |
| Rollback boundary | Revert only the PR2 OpenAI provider, registry/source/GPU/type changes, worker/CLI/WebSocket wiring, persistence comments, and the updated stale registry expectation; retain all PR1 contracts/config/Compose changes and unrelated frontend/archive work. No database migration or UI change is included. |

## PR2 Deviations and Issues

- No intentional design deviation. `gpt-4o-transcribe-diarize` remains outside this slice; speaker-required mode is still gated by `cloud_diarization` and no realtime implementation was added.
- The exact required full pytest selection remains collection-blocked after the allowed environment override because three unrelated test modules fail to import. The focused corrected tests and deterministic provider/runtime harness passed; no unrelated test modules were modified.
- The worker preserves the original source extension in shared temp paths so OpenAI receives a supported file suffix while GPU-local/RunPod continue using their existing storage-key transport.

## PR2 Workload / PR Boundary

- Strategy: `stacked-to-main`; no `size:exception`.
- Current work unit: `PR2-providers-wiring` only.
- Start: PR1 provider-neutral contracts/config and explicit OpenAI seam.
- Finish: local/RunPod-preserving provider implementations, official OpenAI file provider, worker/CLI/WebSocket wiring, and schema-compatible persistence handling.
- Out of scope: tasks 4.x conformance/boundary suites, task 5.x documentation/rollback verification, UI changes, realtime implementation, medical OpenAI parity, generic compatible-audio `base_url`, Ollama audio, push, and PR creation.

## PR2 Status

At PR2 completion, tasks 1.1–1.4 and 2.1–3.3 were implemented; tasks 4.1–4.4 and 5.1–5.2 were reserved for the next stacked slice. That historical batch was **ready for parent SDD verification**, subject to the recorded full-suite collection blockers.

## PR2 Native Attempt Settlement

- Actor acquire: **state `proceed`**, exact parent token reused; no second attempt was created.
- First settlement continuation: the native contract rejected the acquire request ID because settlement requires its own distinct request ID; no attempt state was changed.
- Second settlement continuation: the native contract required the current excluded-untracked inventory after the new provider file was created. The current digest was refreshed through the provider-requested status path and no reset/rescope/supersede was used.
- Final settlement: **state `complete`**, outcome `passed`, evidence revision `sha256:f76f2db9053b5b571cfa743c1b7ac7a3be1233eb61253514a54d3325afb7d482`.
- Final native changed-line accounting: **231**, below the explicit 350-line attempt cap.
- Final excluded-untracked inventory used by the native continuation: `sha256:a87f7d8a8078041c9e25a2f0e7165234f98810cf73e2d1dda117b6af072ee6e5`.
- Native runtime status is terminal for objective `PR2-providers-wiring`; the next ordered work unit requires a new acquire.

## PR3 Batch

- Change: `modular-transcription-providers`
- Work unit: `PR3-conformance-docs`
- Mode: Standard (strict TDD disabled; no `openspec/config.yaml` is present)
- Artifact store: OpenSpec
- Delivery: stacked PR slice 3, `stacked-to-main`; no `size:exception`
- Parent/native attempt token: `sha256:482d7c050dfa410f55b7479449fb4de1e902b072fa8ae0e2696937027d91fcda`
- Untracked scope: `exclude`; initial inventory `sha256:a87f7d8a8078041c9e25a2f0e7165234f98810cf73e2d1dda117b6af072ee6e5`

## Cumulative Completed Tasks

- [x] 1.1 Provider-neutral batch/realtime contracts, audio sources, capabilities, usage metadata, scoped lifecycle, and typed errors.
- [x] 1.2 Registry/factory seams for `gpu-local`, `runpod`, and `openai-file`, with fresh scopes, compatibility alias handling, and no fallback.
- [x] 1.3 `AudioSourceResolver` that keeps local paths distinct from storage keys and preserves GPU/RunPod signed-URL inputs.
- [x] 1.4 Dedicated `TRANSCRIPTION_*` configuration, environment template, and Compose propagation with summary/vision `OPENAI_*` isolation.
- [x] 2.1 Preserved GPU-local/RunPod payloads and MedASR routing, added typed batch-only realtime behavior, and made cleanup idempotent through the scoped client.
- [x] 2.2 Added the official SDK `OpenAIFileProvider` for local WAV/MP3 and the documented formats, `gpt-transcribe`/`gpt-4o-mini-transcribe`, language/options, timestamps, usage, duration, absent speakers, typed failures, and no fallback.
- [x] 3.1 Rewired the worker to provider-neutral source/request contracts, preserved RunPod storage-key behavior, preserved local-file behavior, ordered provider/temp cleanup, progress, heartbeat, persistence, and optional-duration accounting.
- [x] 3.2 Rewired the CLI to submit a local `AudioSource`; the realtime WebSocket now rejects batch-only providers with an explicit unsupported-capability error and does not implement realtime.
- [x] 3.3 Kept the existing nullable transcript persistence schema compatible while normalizing missing segment speakers to `SPEAKER_UNKNOWN` and preserving missing duration/word-speaker values.
- [x] 4.1 Added registry-boundary tests for no fallback, cloud-diarization gating, future-only diarization, batch-only realtime, generic compatible-audio rejection, medical OpenAI rejection, and unsupported Ollama audio.
- [x] 4.2 Added mocked official SDK unit coverage for WAV/MP3 submission, model/format/language/timestamp mapping, usage units, missing fields, `SPEAKER_UNKNOWN`, invalid options, typed failures, cleanup, and no fallback.
- [x] 4.3 Added GPU-local/RunPod wire and timeout regression coverage plus a GPU-worker MedASR routing regression without changing the payload contract.
- [x] 4.4 Added a credential-gated external WAV/MP3 conformance test and fixture README; the test records model, response format, normalized output, timing and speaker gaps, duration, metadata, and original usage units without storing private audio.
- [x] 5.1 Updated `docs/configuration.md` and `README.md` with dedicated settings, candidate models, `.env` selection, capability limits, medical separation, realtime exclusion, no silent fallback, and the Ollama/generic-audio boundary.
- [x] 5.2 Verified explicit `gpu-local`/`runpod` rollback targets, no schema migration, no silent fallback, and configuration-removable OpenAI registration/settings; no destructive rollback was performed.

## PR3 Files Changed

| File | Action | What Was Done |
|---|---|---|
| `backend/tests/services/transcription/test_registry.py` | Modified | Added provider-selection boundary, capability, future-model, realtime, medical, generic-audio, Ollama, and explicit rollback-target tests. |
| `backend/tests/services/transcription/test_openai_provider.py` | Created | Added mocked official SDK coverage for file submission, normalization, usage, invalid options, typed failures, cleanup, and no fallback. |
| `backend/tests/services/transcription/test_conformance.py` | Created | Added the opt-in live `gpt-transcribe`/configurable comparison test for operator-supplied WAV and MP3 fixtures. |
| `backend/tests/fixtures/transcription/README.md` | Created | Documents safe external fixture and credential prerequisites; no audio or secret is committed. |
| `backend/tests/unit/test_gpu_client_timeout.py` | Modified | Preserved RunPod medical payload and batch-only realtime regression coverage. |
| `zabt-gpu-worker/tests/test_pipeline_language.py` | Modified | Preserved the MedASR route for medical jobs. |
| `docs/configuration.md` | Modified | Documented provider settings, candidates, limitations, separation, and rollback selection. |
| `README.md` | Modified | Corrected provider/data-boundary claims and added setup guidance for explicit transcription selection. |
| `openspec/changes/modular-transcription-providers/tasks.md` | Modified | Marked only tasks 4.1–5.2 complete; tasks 1.1–3.3 remain complete. |
| `openspec/changes/modular-transcription-providers/apply-progress.md` | Modified | Merged cumulative PR1/PR2 progress with this PR3 evidence and settlement. |

## PR3 Work Unit Evidence

| Evidence | Exact result |
|---|---|
| Focused test command and exact result | With process-only host overrides `MINIO_ENDPOINT=localhost:9000` and `DATABASE_URL=postgresql+asyncpg://app:app@localhost:5433/zabt`, `cd backend && uv run pytest tests/services/transcription -k openai`: **16 passed, 1 skipped, 11 deselected, 2 warnings**. The same corrected environment with `-k "contract or registry"`: **15 passed, 13 deselected, 2 warnings**. GPU/RunPod regressions: **9 passed, 2 warnings**; GPU-worker regression: **5 passed, 1 warning**. |
| Runtime harness command/scenario and exact result | `cd backend && uv run pytest tests/services/transcription/test_conformance.py -q`: **1 skipped, 2 warnings** because the explicit conformance gate, runtime credential, and external WAV/MP3 oracle were unavailable. No live network call, credential, or private audio was used. |
| Static/configuration checks | `cd backend && uv run python -m compileall -q app`: **passed**; `docker compose config --quiet`: **passed**; `git diff --check`: **passed** with existing CRLF normalization warnings. |
| Rollback boundary | Revert the PR3 registry/OpenAI/conformance/fixture tests, GPU/MedASR regression tests, documentation, and task/progress append without removing unrelated PR1/PR2 provider wiring. The explicit rollback test and Compose validation preserve `gpu-local`/`runpod`; no migration or destructive rollback operation was introduced. |

## PR3 Deviations and Issues

- No intentional design deviation. Existing PR1/PR2 `TRANSCRIPTION_*` Compose/config references already covered the required settings, so PR3 changed only the necessary documentation and did not duplicate or alter the wire configuration.
- The live credential-gated conformance path was safely skipped because its operator-supplied credential/audio oracle was absent. This is an expected environment result, not a conformance pass or a provider benchmark claim.
- The broader suite retains the previously recorded unrelated collection blockers (`playwright`, stale `TranscriptionService` import, and `Settings` import failure); no stale modules were changed to make broad collection pass.

## PR3 Workload / PR Boundary

- Strategy: `stacked-to-main`; no `size:exception`.
- Current work unit: `PR3-conformance-docs` only.
- Start: PR2 provider implementations and worker/CLI/persistence wiring.
- Finish: provider boundary tests, mocked OpenAI conformance coverage, safe external fixture test, GPU/RunPod/MedASR regressions, documentation, and rollback evidence.
- Out of scope: realtime implementation, cloud medical parity, generic compatible-audio `base_url`, Ollama audio, UI changes, push, commits, and PR creation.
- Native changed-line accounting: **169**, below the explicit 350-line attempt cap.

## PR3 Native Attempt Settlement

- Actor acquire: **state `proceed`**, exact parent token reused; no second attempt was created.
- The first same-token settlement request was rejected only because new excluded untracked paths changed the inventory from `sha256:a87f7d8a8078041c9e25a2f0e7165234f98810cf73e2d1dda117b6af072ee6e5` to `sha256:76ccbcd4e99d80595e0098457d0bc87262615834af92debba23fcffdaa52f240`. The provider continuation was followed through read-only review status, without reset, rescope, or supersede.
- Final same-token settlement: **state `complete`**, outcome `passed`, using settle request `sdd-apply-pr3-20260914-settle-v2`.
- Evidence revision: `sha256:4875e62aaa2be1c9982e6f909ad5a6757b9c8dc70afb368c331d115d97b5f8a9`.
- Final native changed-line accounting: **169**, below the 350-line attempt cap.
- Final excluded-untracked inventory: `sha256:76ccbcd4e99d80595e0098457d0bc87262615834af92debba23fcffdaa52f240`.

## Status

Tasks 1.1–5.2 are implemented and the PR3 bounded evidence is complete. The slice is **ready for parent SDD verification**; no commit, push, or PR was created.
