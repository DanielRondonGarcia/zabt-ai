# Tasks: Video Transcription Playback

## Review Workload Forecast (Guard)

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | API, shared contract, upload MIME | PR 1 | `cd backend && uv run pytest tests/contract/test_meetings.py -q` | N/A: API stubs/request interception | Backend model/endpoint, shared type, upload payload |
| 2 | Native player, store, page lifecycle | PR 2 | `cd frontend-2 && npm run lint && npm run build` | `npm run dev:web` plus meeting transcript route | Player/store/page changes only |
| 3 | Fixture, upload MIME, and storage contract E2E | PR 3A | `pytest tests/e2e/test_meeting_upload.py -v` | Local web/API/storage fixture with signed GET and ranges | Fixture and upload-test changes only |
| 4 | Transcript viewer playback and failure-path E2E | PR 4 | `pytest tests/e2e/test_transcript_viewer.py -v` | Local web/API/storage fixture with signed GET and ranges | Transcript-viewer test changes only |

## Phase 1: API and Shared Contract

- [x] 1.1 Extend `backend/tests/contract/test_meetings.py` for known, missing, empty, parameterized, and unknown MIME; assert JSONB persistence, nullable `media_type`, and preserved `audio_url`.
- [x] 1.2 Add nullable `media_type` to `MeetingRead` in `backend/app/models/base.py`; in `backend/app/api/v1/endpoints/meetings.py`, persist supplied `content_type` in existing JSONB, trim/lowercase only that field, and update `_normalize_media_type`, `MeetingCreateWithKey`, `_build_meeting_response`, `create_meeting`, and `read_meetings` without migrations or filename guessing.
- [x] 1.3 Add `MediaType` and `media_type` to `packages/shared/src/types.ts`; verify `frontend-2/app/lib/api.ts` (read-only) continues its type re-export.

## Phase 2: Upload and Native Playback

- [x] 2.1 Update `frontend-2/app/components/upload-modal.tsx` `startUpload` to send raw `item.file.type` on meeting creation while preserving presigned PUT fallback headers and no extension guessing.
- [x] 2.2 Add reset/media identity to `frontend-2/app/lib/use-transcript-store.ts` (`TranscriptState`/`useTranscriptStore`) so time, duration, playing state, and pending seeks cannot cross media.
- [x] 2.3 Refactor `StickyMediaPlayer` in `frontend-2/app/components/sticky-media-player.tsx` around one keyed `HTMLMediaElement`; render visible video or hidden audio, share controls/sync, clean RAF/listeners, catch `play()`, and announce generic non-blocking errors.
- [x] 2.4 Update `MeetingDetailPage` in `frontend-2/app/(dashboard)/meetings/[id]/page.tsx` to mount only for transcription, reserve measured fixed-player space, and reset on meeting, URL, route, or lifecycle changes.

## Phase 3: Browser and E2E Proof

- [ ] 3.1 Create `tests/e2e/fixtures/short-video.mp4`, a browser-supported fixture served with `video/mp4`, CORS, `Accept-Ranges`, `206`, and `Content-Range` evidence.
- [ ] 3.2 Extend `tests/e2e/test_meeting_upload.py` to assert video MIME reaches `POST /meetings/` while presigned upload and PUT behavior remain unchanged; cover empty-MIME fallback.
- [ ] 3.3 Extend `tests/e2e/test_transcript_viewer.py` for audio regression, visible video, play/rate/timeline/word seeking, highlighting, responsive bounds, navigation cleanup, signed/codec failures, accessible status, and usable transcript.

## Phase 4: Completion and Guardrails

- [ ] 4.1 Run the three focused commands above, then record API, lint/build, browser, storage-header, and E2E evidence; require audio and video synchronization before release.
- [ ] 4.2 Confirm rollback is a code revert with no data rewrite; keep `audio_url`, null-to-audio fallback, transcript schema, and summary/intelligence contracts compatible.

## Review Workload Forecast (Final)

Estimated 650–850 changed lines: seven product files, three test files, a media fixture, and a substantial player/lifecycle refactor. The browser/E2E work is now split into PR 3A (upload/fixture/storage) and PR 4 (transcript viewer playback) to keep each test slice reviewable. Risk is High; chained PRs are recommended. Delivery uses the selected `stacked-to-main` strategy. Threat matrix is N/A, so no threat-specific RED tasks apply. Out of scope: mobile, visual pipeline, re-encoding, transcript-schema changes, broad redesign, and new media libraries.

## PR 1 Apply Evidence

### Work Unit Evidence

| Evidence | Required value |
|---|---|
| Focused test command and exact result | `cd backend && uv run pytest tests/contract/test_meetings.py -q` — failed during `tests/conftest.py` import because the configured MinIO endpoint was unavailable; no test collection ran. |
| Runtime harness command/scenario and exact result | N/A — this slice has API contract coverage and request-level upload propagation only; browser/runtime playback belongs to PR 3. |
| Rollback boundary | Revert the backend model/endpoint/service changes, shared type, upload payload, and contract tests in this PR; no migration or data rewrite is involved. |

## PR 1 Remediation Retry Evidence

### Work Unit Evidence

| Evidence | Required value |
|---|---|
| Focused test command and exact result | `cd backend && uv run pytest tests/contract/test_meetings.py -q` — failed during `tests/conftest.py` import before collection with `botocore.exceptions.EndpointConnectionError` for `http://minio:9000/zabt-ai-bucket`; `docker compose ps minio` showed the MinIO container `Up`, so the backend process still could not resolve its configured service endpoint. |
| Runtime harness command/scenario and exact result | N/A — this slice has API contract coverage and request-level upload propagation only; browser/runtime playback belongs to PR 3. `npm run build:shared` passed and `git diff --check` passed, but the failed backend contract collection prevents a passing work-unit result. |
| Rollback boundary | Revert the backend model/endpoint/service changes, shared type, upload payload, and contract tests in this PR; no migration or data rewrite is involved. |

### Native remediation attempt

- Actor acquire: `proceed`, continuing the parent token with request `video-transcription-playback-pr1-actor-acquire-20260913-002`.
- Native settle: `proceed` with outcome `failed`; evidence revision `sha256:4ab258a20f0e771d41094b4ff680d0a0a2f0977a1dcc54c12846f66ca8609361`.
- Remediated evidence revision: `sha256:42d833473c29b284c4cf1f77626816d2cc19c6b583d297f3c11144e2c28f6bf1`.
- The fresh evidence revision is distinct from the failed revision; no further reset or verification retry is authorized for this attempt.

## PR 1 Host-Local MinIO Verification

### Work Unit Evidence

| Evidence | Required value |
|---|---|
| Focused test command and exact result | `$env:DATABASE_URL='postgresql+asyncpg://app:app@localhost:5433/zabt'; $env:MINIO_ENDPOINT='localhost:9000'; $env:WEASYPRINT_DLL_DIRECTORIES='C:\msys64\mingw64\bin'; Set-Location backend; uv run pytest tests/contract/test_meetings.py -q` — exit 0; 17 passed, 12 warnings in 6.63s. |
| Shared build command and exact result | `npm run build:shared` — exit 0; `@zabt/shared` TypeScript `tsc --build` completed. |
| Diff hygiene command and exact result | `git diff --check` — exit 0; no whitespace errors; Git emitted only the existing LF-to-CRLF warnings for the six tracked PR 1 files. |
| Runtime harness command/scenario and exact result | Host-local backend contract harness passed with process-only PostgreSQL, MinIO, and WeasyPrint settings; browser/runtime playback is out of scope for PR 1. |
| Rollback boundary | Revert only the six tracked PR 1 files and this evidence section; no migration, data rewrite, or later-slice file is involved. |

### Native Attempt

- Actor acquire: `proceed`, request `video-transcription-playback-pr1-actor-acquire-20260913-008`.
- Native settle: `complete`, outcome `passed`, request `video-transcription-playback-pr1-actor-settle-20260913-008`.
- Fresh evidence revision: `sha256:8a76444fd93dc570f46951a235c20b90b4fe279c96c6afdfecf7ce5c569b2155`.
- Exact remediated evidence revision: `sha256:8d31a4a216fe0c92b7e4f0fd601e2789882156b297c576e6d5d5234b03674573`.
- Proven diagnosis: the prior host process used the container-only MinIO hostname `minio`; the process-only `MINIO_ENDPOINT=localhost:9000` override reached the verified host-local service and resolved the contract-test collection failure without source changes.
- Harness disposition: `reused`; existing host database and MinIO services were reused, and browser/E2E playback remains a later slice.
- Cleanup evidence: process-only environment overrides ended with each command; no repository files or service lifecycle were changed by verification.
- Process evidence: all three requested commands ran once in the foreground after actor acquire returned `proceed`; no retry was performed.

### PR 1 Boundary Confirmation

- Exactly six tracked files remain in the PR 1 diff: backend model, endpoint, meeting service, contract test, upload modal, and shared types.
- Changed-line count remains 164 (`159` additions + `5` deletions), below the 400-line slice budget.
- No later-slice files were modified; tasks 2.2–4.2 remain unchecked.

## PR 2 Apply Evidence

### Work Unit Evidence

| Evidence | Required value |
|---|---|
| Focused test command and exact result | `cd frontend-2 && npm run lint && npm run build` — failed at `npm run lint` with exit 2 before the chained build; ESLint 9.39.5 raised `TypeError: expand is not a function` from the installed `minimatch`. The final standalone `npm run build` then passed after the media ref callback fix; TypeScript and Next production generation completed successfully. |
| Runtime harness command/scenario and exact result | `npm run dev -- --hostname 127.0.0.1 --port 3102` from `frontend-2`, then `GET /meetings/999` — HTTP 200; the temporary dev-server process was stopped and no long-lived process remained. |
| Rollback boundary | Revert only `frontend-2/app/lib/use-transcript-store.ts`, `frontend-2/app/components/sticky-media-player.tsx`, `frontend-2/app/(dashboard)/meetings/[id]/page.tsx`, and this PR 2 evidence/checkbox section; PR 1 API, upload, shared-type, and contract files remain intact. |

### Native Attempt

- Actor acquire: `proceed`, continuing the parent token with request `video-transcription-playback-pr2-actor-acquire-20260913-001`.
- Native settle request `video-transcription-playback-pr2-actor-settle-20260913-001` recorded outcome `failed` with evidence revision `sha256:721b8b818103d5ef6facb142d308aed43fbb744674a2dba362ab6fc2dc47e248`; the command returned `state: blocked` with `reason: maintainer_decision` because the native objective requires reset after this failed attempt.
- Native status revision after settlement: `sha256:1eeca3e098ccfcbb19e83f97d848272e1db818466cd454caa5b1fca27da604b8`; cumulative PR 2 changed lines: `313`; no reset or second attempt was run.

### PR 2 Boundary Confirmation

- Exactly three PR 2 source files were added to the existing six tracked PR 1 files; no PR 3 files were modified.
- PR 2 authored changed-line count is `313` (`253` additions + `60` deletions), below the 400-line slice budget.
- The OpenSpec change directory remains untracked as the pre-existing artifact boundary; no source files outside the nine-file PR 1 + PR 2 boundary changed.

## PR 2 Final Verification Attempt

### Bounded Verification Evidence

| Evidence | Required value |
|---|---|
| Candidate player lint | `cd frontend-2 && npx eslint "app/components/sticky-media-player.tsx"` — exit 0; no diagnostics. |
| Changed page lint and unchanged-base diagnostics | `cd frontend-2 && npx eslint "app/(dashboard)/meetings/[id]/page.tsx"` — exit 1; 6 problems (4 errors, 2 warnings). The base-file stdin run produced the same six rule diagnostics: `react-hooks/immutability` for `startPolling` and `stopPolling` access before declaration, `react-hooks/exhaustive-deps` for the unused disable directive and missing `startPolling`, and two `@typescript-eslint/no-explicit-any` errors. Candidate lines are shifted by 27 from base lines 155/167/164/189/163/165/567/568 to 182/194/191/216/190/192/594/595; no PR 2-specific page diagnostic was introduced. |
| Full frontend lint baseline | `cd frontend-2 && npm run lint` — exit 1; 38 problems (31 errors, 7 warnings). Known unchanged diagnostics are in integrations, templates, structured-output-renderer, summary-menu, summary-toolbar, template-selector, upload-modal, processing-queue-context, `api.ts`, and the unchanged polling/typing sections of the meeting page. The player and transcript store produced no diagnostics. |
| Standalone frontend build | `cd frontend-2 && npm run build` — exit 0; Next.js compiled, typechecked, generated 10 static pages, and finalized route optimization. |
| Shared build | `npm run build:shared` — exit 0; `@zabt/shared` TypeScript build completed. |
| Diff hygiene | `git diff --check` — exit 0; only existing LF-to-CRLF conversion warnings for the three PR 2 files, with no whitespace errors. |
| Runtime route harness | Direct Next process harness failed before readiness: `node` could not resolve `node_modules/next/dist/bin/next`, and the cleanup script then rejected assignment to PowerShell's read-only `$PID` variable. `GET /meetings/999` and cleanup/port-clear evidence were not obtained. |
| Rollback boundary | Revert only `frontend-2/app/lib/use-transcript-store.ts`, `frontend-2/app/components/sticky-media-player.tsx`, `frontend-2/app/(dashboard)/meetings/[id]/page.tsx`, and this bounded evidence section; retain the PR 1 commit and leave PR 3 files untouched. |

### Native Attempt

- Actor acquire: `proceed`, request `video-transcription-playback-pr2-actor-acquire-20260913-003`, parent token `sha256:01eb7866474222562b0f75478a3280a2000c5de2879eb668f39d9a0406c5e3a0`.
- Actor settle: one `failed` settlement was attempted with fresh evidence revision `sha256:889b610867d0906049cc907c45aafb11dbee2c92b0cd56b7a9f74bed7066fcc7` and exactly `--remediates-evidence-revision sha256:87b804ea42e92397b1c61d230bca92a48629bc01c3b5a181e6c13efb0dd1df11`; native returned `state: blocked`, `reason: maintainer_decision` because the lifetime attempt objective still requires a maintainer decision.
- No reset, retry, PR 3 start, SDD verify phase, or archive operation was performed.

## PR 3 Apply Evidence (Size Exception)

- Delivery strategy: `exception-ok`; chain strategy: `stacked-to-main`; the maintainer-approved review limit for this slice is 600 changed lines rather than the normal 400-line budget.
- Tasks 3.1–3.3 remain pending because the focused browser command did not pass; no task is marked complete from failed evidence.

### Work Unit Evidence

| Evidence | Required value |
|---|---|
| Focused test command and exact result | `pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v` — collected 10 tests; the storage contract test passed, eight tests failed, and the final unsupported-media case exceeded the 180000ms harness timeout. |
| Runtime harness command/scenario and exact result | `npm run dev:web -- --hostname 127.0.0.1 --port 3001` with the focused pytest command — the Zabt server reached `127.0.0.1:3001`; taskkill process-tree cleanup removed the npm/Next/PostCSS processes and the final matching-process probe returned zero. |
| Rollback boundary | Revert `tests/e2e/test_meeting_upload.py`, `tests/e2e/test_transcript_viewer.py`, `tests/e2e/fixtures/short-video.mp4`, and this PR 3 evidence section; retain PR 1 and PR 2 source and the restored generated `frontend-2/next-env.d.ts`. |

### Native Attempt

- Actor acquire: `proceed`, request `video-transcription-play3-actor-acquire-20260913-002`, token `sha256:53fa49cb4a688c99329ce100c433de7af4da45504a6400957cff16bd56898933`.
- Actor settle: request `video-transcription-play3-actor-settle-20260913-002`, outcome `failed`, evidence revision `sha256:7bac12c30665024e3ec574d493bd881fa41ba3a8b010edf144d78090b81d494c`; native returned `state: blocked`, `reason: maintainer_decision`.
- Fixture evidence: `tests/e2e/fixtures/short-video.mp4`, 4,534 bytes, SHA-256 `607ff1ff56d72e93d7948bd5fe741cce8cc701bf4927a9fff0d1ac90643fea46`.
- No PR 3 commit was created because the required focused browser command failed; tasks 4.1–4.2, final SDD verification, and archive remain untouched.

## PR 3 Split Boundary

- **PR 3A** — fixture and upload/storage E2E: local commit `7345539 test(transcription): cover video upload E2E` contains `tests/e2e/fixtures/short-video.mp4` and `tests/e2e/test_meeting_upload.py`. Verification remains pending because the combined browser run failed before an admitted settlement.
- **PR 4** — transcript viewer/video playback E2E: local commit `e46d286 test(transcription): cover transcript video playback E2E` contains `tests/e2e/test_transcript_viewer.py`. Verification remains pending and depends on PR 3A.
- Both commits were created as bounded local slices after the maintainer approved the size exception; no push or PR was performed. Tasks 3.1–3.3 remain unchecked until their focused suites pass.
