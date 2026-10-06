# Cloud Actsis diarization

## Objective

Route Zabt cloud transcription and speaker diarization through the verified Actsis endpoint without requiring the local GPU, while preserving the existing local/RunPod provider behavior.

## Problem and rationale

Actsis exposes `whisper-diarize`, which accepts `response_format=diarized_json` and `chunking_strategy=auto`. The initial cloud routing implementation configured that model, but independent verification found endpoint-specific credential fallback, strict diarization request/response, API word-speaker preservation, and HTTPS enforcement gaps. The standalone full-cloud Compose preflight still requires explicit image and managed-service settings.

## Authorized scope

- Update the OpenAI-compatible transcription provider to support the verified Actsis diarization model only when cloud diarization is explicitly enabled.
- Keep official OpenAI and custom Actsis credentials isolated and validate their endpoint-specific fallback rules.
- Require `diarized_json` for speaker-required or `whisper-diarize` requests and reject diarized responses without segment speaker labels.
- Require a custom HTTPS endpoint for enabled cloud diarization while preserving permitted HTTP behavior for non-diarized local/custom test endpoints.
- Carry `chunking_strategy=auto` through the production transcription request.
- Preserve the no-GPU full-cloud Compose profile, its local environment template, and focused documentation/tests so Actsis is configured explicitly.
- Preserve optional word speaker labels through the backend API and shared/frontend TypeScript contract.
- Do not add or start a local GPU/pyannote worker.
- Do not print, commit, rotate, or copy provider credentials.
- Do not invent managed database, Redis, object-storage, Qdrant, image, or public-origin values that are not present in the environment.

## Checklist

- [x] T1: Final corrective pass complete — isolate endpoint-specific credentials, enforce the strict diarization request/response contract for every segment, and require HTTPS for enabled cloud diarization.
- [x] T2: Add a configuration/request path for `chunking_strategy` and pass `auto` to Actsis.
- [x] T3: Verification complete — the HTTPS Actsis full-cloud template and no-GPU topology remain aligned with the new endpoint guard.
- [x] T4: Final corrective regression coverage complete — verify both-key and Actsis-only credential routing, strict mixed/empty/malformed response rejection, timestamp speaker preservation, HTTPS enforcement, API word-speaker round trips, and speaker-aware chunk-boundary text/word deduplication and overlap retention.
- [x] T5: Local checks complete; full-cloud startup and remote smoke test remain blocked — do not start Docker or repeat remote provider calls in this pass.
- [x] T6: Validated Actsis response-shape adjustment complete — parse nested segment words with segment-speaker fallback, retain top-level word compatibility, deduplicate mixed representations, and cover Ricardo's seven-word shape.
- [x] T7: Authorized local video-input fix complete — normalize custom cloud `whisper-diarize` video containers to temporary mono 16 kHz MP3 before the existing direct-upload/chunk decision, clean the temporary workspace, preserve original audio inputs, and map preparation/chunking progress to the frontend `transcribing` stage.
- [x] T8: Shared/mobile progress contract correction complete — include `preparing_audio` and `transcribing_chunk` in the shared sub-status definitions, map both to mobile `transcribing`, and cover both statuses with mobile regressions.
- [x] T9: Normalization cleanup regression coverage complete — verify temporary-workspace cleanup after normalization failure and provider failure after normalized upload without broadening backend scope.
- [x] T10: Apply the authorized long-request client fix — add a validated configurable OpenAI request timeout, propagate it to both SDK and httpx clients, and configure the Actsis full-cloud profile for 7200 seconds with zero provider retries.
- [x] T11: Verify timeout propagation, timeout validation, profile defaults, compilation, and diff hygiene without Docker, remote provider calls, or meeting reprocessing.
- [x] T12: Fence long-request stale recovery — align full-cloud and local Actsis Compose grace with the 7200-second timeout plus margin, preserve the generic 900-second setting default, add liveness regression coverage, and refresh configuration docs/templates.
- [x] T13: Correct the local Compose policy boundary — restore generic base fallbacks, add the shareable no-GPU Actsis overlay, assert the overlay/base split statically, and cover the claim/dispatcher grace boundary.

## Acceptance criteria

- The generic provider remains unchanged for existing models and deployments.
- `whisper-diarize` is rejected before provider I/O unless cloud diarization is enabled for the selected custom endpoint.
- Speaker-required and `whisper-diarize` requests require `response_format=diarized_json` before SDK I/O.
- An enabled diarization response with no segments or with any missing, blank, or `SPEAKER_UNKNOWN` segment speaker label raises a typed provider response error.
- Strict diarization validation rejects every raw segment with missing/blank/unknown speaker labels or invalid/missing timing before malformed entries can be discarded.
- A timestamp pass must not replace labeled primary diarized segments with unlabeled secondary segments; it records a bounded timestamp capability gap when preservation is required.
- The Actsis request contains `model=whisper-diarize`, `response_format=diarized_json`, and `chunking_strategy=auto`.
- A diarized response preserves segment and word speaker labels through the existing transcript model/persistence path.
- Nested `segments[*].words` records inherit their containing segment speaker when unlabeled; top-level `response.words` remains supported and duplicate word/timing records prefer a labeled version.
- Custom HTTPS `whisper-diarize` requests normalize `.mp4`, `.mpeg`, `.mp4v`, `.webm`, `.mov`, `.avi`, and `.mkv` sources to a temporary mono 16 kHz MP3 before the provider request; existing audio files are sent unchanged.
- Temporary normalized media is removed after direct or chunked transcription, including provider and preparation failures.
- `preparing_audio` and `transcribing_chunk` processing sub-statuses render as `Transcribing…` instead of `Uploaded`.
- Enabled cloud diarization requires a custom `https://` endpoint while non-diarized HTTP custom test endpoints remain permitted.
- Full-cloud Compose contains no GPU worker, `HF_TOKEN`, or local pyannote dependency.
- `ACTSIS_API_KEY` remains isolated from `OPENAI_API_KEY` fallback behavior.
- The OpenAI-compatible request timeout is validated, defaults to the existing generic 600-second behavior, and is passed explicitly to both the OpenAI SDK and underlying httpx client.
- The Actsis full-cloud profile defaults to a 7200-second request timeout and zero provider retries, while generic provider defaults remain unchanged.
- Full-cloud and local Actsis Compose runtime overrides set `MEETING_RECOVERY_GRACE_SECONDS=9000` for the 7200-second request profile; the generic `Settings` default remains 900 seconds.
- Base Compose keeps generic timeout/retry/grace fallbacks at `600`/`2`/`900`, while the dedicated no-GPU local Actsis overlay explicitly applies `7200`/`0`/`9000` to API, worker, and beat.
- The local Actsis overlay sources `ACTSIS_API_KEY` from the untracked `.env`, preserves API `:8000` and web `:3001`, disables the GPU profile, and uses a 500000000-byte direct-upload threshold for the current local test.
- Recovery claims remain ineligible at request timeout `7200` and become dispatchable only after grace `9000`; generic `900`-second recovery behavior remains covered.
- Generic configuration docs/templates describe endpoint-specific credential fallback, custom HTTPS `whisper-diarize`, the 600/7200-second timeout profiles, zero full-cloud retries, and the stale-recovery relationship.
- Compose validation and focused tests pass; any unavailable external dependency is recorded as a blocked runtime check rather than replaced with a fake success.

## Verification evidence

- Actsis live verification: `whisper-diarize` is listed and returned HTTP 200 with a `speaker` field when called over HTTPS with `diarized_json` and `chunking_strategy=auto`.
- Actsis response-shape validation: the HTTPS endpoint returned HTTP 200 with one segment and seven words nested under `segments[0].words`, no top-level `words` key, and `SPEAKER_00`; the alternate HTTP port 4000 refused TCP and was not used.
- HTTP endpoint redirects with 301; the configured base URL must use HTTPS directly.
- Current full-cloud preflight is blocked by missing deployment settings and image references; no containers have been started for this feature yet.

## Progress

The final bounded corrective pass was reopened after independent verification found two additional
speaker-loss paths beyond the prior credential, request/response, HTTPS, and API word-speaker fixes:
the primary response guard accepted mixed labeled/unlabeled segments, and the optional `whisper-1`
timestamp pass could replace labeled Actsis segments with unlabeled segments.

T1 corrective work now treats speakers as required when `speaker_required=True`, the selected model is
`whisper-diarize`, or the response format is `diarized_json`. Empty responses and every segment with a
missing, blank, or `SPEAKER_UNKNOWN` label raise the existing typed `ProviderResponseError`. The
non-diarized coarse fallback remains unchanged. The timestamp pass now preserves primary diarized
segments when its secondary response contains unlabeled segments, records `timestamps:<mode>` as a
capability gap, and marks the bounded pass as skipped. Normal non-diarized timestamp replacement
behavior remains covered.

T3/T4 corrective verification remains aligned: the full-cloud template still uses
`https://ai.actsis.internal/v1`, `whisper-diarize`, `diarized_json`, required speakers, and
`TRANSCRIPTION_CHUNKING_STRATEGY=auto`; its service set remains `api`, `worker`, `beat`, and `web`,
with no GPU/pyannote/HF-token topology. Regression coverage now includes mixed/empty strict diarized
responses and preservation of primary speaker labels across an unlabeled timestamp pass, alongside
the existing credential, HTTPS, and API word-speaker tests. The final chunk merge correction now keeps
boundary-overlapping segments when adjacent segment speakers differ, and compares segment speakers and
word speaker labels during duplicate detection; a focused regression proves identical adjacent chunk
text retains both segment and word speaker labels. The final independent-verifier corrective
regressions now also retain identical adjacent speaker text in `TranscriptionResult.text`, use
containing segment speakers when word labels are absent, and reject a mixed valid/malformed raw
diarized response before the malformed entry is skipped.

T5 local verification passed for this final corrective pass: the exact focused pytest command passed
with 101 tests and 1 existing Pydantic deprecation warning; `uv run python -m compileall -q app`
exited successfully with no output; and `git diff --check` exited successfully (Git only reported
existing LF-to-CRLF normalization warnings). No Docker services were started and no remote provider
call was repeated; managed runtime values/images and a real multi-speaker smoke test remain
unavailable.

T6 response-shape work is complete: `_word_timestamps` now reads both legacy top-level words and
nested segment words, applies the containing segment speaker when a nested word is unlabeled, and
deduplicates matching top-level/nested word-timing records while preferring a labeled record without
collapsing distinct nested segment records. Focused regressions model Ricardo's seven nested words
without a top-level `words` field and mixed top-level/nested duplicates; the existing API word-speaker
round-trip coverage remains green. The exact focused
pytest command passed with 89 tests and one existing Pydantic deprecation warning,
`uv run python -m compileall -q app` exited successfully with no output, and `git diff --check`
exited successfully with only existing LF-to-CRLF warnings. No timestamp pass, threshold, Docker
service, remote provider call, or meeting retry was added; meeting 595 was not reprocessed.

T7 application correction is complete: the custom HTTPS `whisper-diarize` path now accepts the
supported video/container extensions, emits `preparing_audio`, extracts a temporary mono 16 kHz MP3
with the bounded FFmpeg timeout, routes that path through the existing direct-upload/chunk decision,
and removes its workspace in `finally`. Original MP3/WAV and other audio inputs remain untouched;
nested Actsis words and strict segment-speaker validation were not weakened. The frontend maps
`preparing_audio` and `transcribing_chunk` to the existing `transcribing` stage.

T8 shared/mobile contract correction is complete: the authoritative shared `MeetingSubStatus` type
and runtime array now include `preparing_audio` and `transcribing_chunk`, and the mobile mapper sends
both statuses to the existing `transcribing` stage. The web mapper remains unchanged and continues to
map both statuses to `transcribing`.

T9 cleanup regression coverage is complete within the existing provider unit seam: normalization
failure and provider failure after normalized upload both assert that the temporary workspace is
removed while the original video remains unchanged. No broader backend cleanup seam was needed.

This pass's exact focused pytest command passed with 92 tests and one existing Pydantic deprecation
warning; `uv run python -m compileall -q app` exited successfully with no output;
`npm run lint -- app/lib/stage-utils.ts` exited successfully; and `git diff --check` exited
successfully with only existing LF-to-CRLF normalization warnings. No frontend test runner is
configured in `frontend-2`, so focused frontend verification used the available ESLint command.
No Docker services were started, no remote provider call was made, and meetings 598 and 597 were not
automatically reprocessed in this pass.

This bounded shared/mobile correction's exact focused pytest command passed with 94 tests and one
existing Pydantic deprecation warning; `uv run python -m compileall -q app` exited successfully with
no output; `npm run lint -- app/lib/stage-utils.ts` exited successfully; the mobile Jest stage-utils
command passed with 14 tests; and `git diff --check` exited successfully with only existing
LF-to-CRLF normalization warnings. No Docker services were started, no remote provider call was
made, no meetings were retried or reprocessed, and no runtime deployment is claimed.

T10/T11 long-request client work is complete: `TRANSCRIPTION_OPENAI_REQUEST_TIMEOUT_SECONDS` is a
validated setting with a 600-second generic default, is passed explicitly to both the OpenAI SDK and
the underlying httpx client, and is exposed through the base Compose services without changing their
generic defaults. The standalone Actsis full-cloud profile and env example default it to 7200 seconds
and set `TRANSCRIPTION_OPENAI_MAX_RETRIES=0`, preventing duplicate long full-file requests. The
provider now records bounded per-attempt elapsed time, attempt number, operation, error type, and
optional status code without logging credentials, request contents, or transcript text.

The requested focused pytest command passed with 97 tests and one existing Pydantic deprecation
warning; `uv run pytest tests/unit/test_config_defaults.py -q --noconftest` passed with 7 tests and
the same warning; `uv run python -m compileall -q app` exited successfully with no output; and
`git diff --check` exited successfully with only existing LF-to-CRLF normalization warnings. No
Docker services were started, no remote provider call was made, and meeting 599 was not retried or
reprocessed.

T12 stale-recovery correction is complete: no reliable live Celery/root-task fence exists in the
current recovery path—the audit root-task id is persisted for observability, but recovery does not
inspect live task state and the provider heartbeat brackets the blocking request. The safe bounded
fix therefore keeps the generic 900-second setting default and sets the full-cloud and local Actsis
Compose runtime grace to 9000 seconds, above the 7200-second request timeout. The regression proves
a request-aged 7200-second processing row is not stale inside the full-cloud grace and becomes stale
after the grace window. Generic configuration docs and `.env.example` now document official versus
custom endpoint key fallback, HTTPS `whisper-diarize`, generic/full-cloud timeout and retry values,
and the grace relationship; the full-cloud env example records the 9000-second override. The exact
required verification command passed with 106 tests and one existing Pydantic deprecation warning;
the recovery regression passed separately, compilation passed, and `git diff --check` passed with
only existing LF-to-CRLF normalization warnings. No Docker services, remote calls, meeting retries,
or meeting reprocessing were performed.

T13 local Compose correction is complete: `docker-compose.yml` now keeps the generic `600`-second
request timeout, `2` retries, and `900`-second recovery grace for API, worker, and beat. The new
`docker-compose.local-actsis.yml` overlay explicitly applies the HTTPS Actsis
`whisper-diarize`/`diarized_json` contract, required speakers, `chunking_strategy=auto`, the
500000000-byte direct-upload threshold, `7200`/`0`/`9000` long-request policy, and the existing API
`:8000`/web `:3001` ports. It sources `ACTSIS_API_KEY` from the untracked `.env` and moves the base
GPU worker to an opt-in `gpu` profile. Static Compose tests assert the base/overlay split for all
three backend processes, and the recovery regression now proves the actual claim/dispatcher seam
does not claim or dispatch at 7200 seconds but does dispatch after the 9000-second grace; generic
900-second recovery behavior remains covered.

The exact required focused pytest command passed with 119 tests and one existing Pydantic
deprecation warning; `uv run python -m compileall -q app` exited successfully with no output; and
`git diff --check` exited successfully with only existing LF-to-CRLF normalization warnings. No
Docker services were started, no remote provider call was made, no meetings were retried or
reprocessed, and no delivery action was performed.

## Next step

The response-shape, video-input, shared/mobile progress, long-request client, and local Compose
policy adjustments are complete for the authorized repository boundary and apply to future or new
runs; meeting 599 was not automatically reprocessed. A real local/full-cloud startup and
multi-speaker smoke test still require the actual deployment settings, managed-service endpoints,
image references, and credentials outside this repository; runtime startup, deployment, and meeting
reprocessing are not claimed complete.
