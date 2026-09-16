# Tasks: Correct and Verify Video Transcription E2E Slices

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 450–600 authored test lines; approved 600-line maximum |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR3A upload harness → PR4 transcript harness |
| Delivery strategy | exception-ok |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Correct four upload tests and contracts | PR3A first → main | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short` | Active Docker stack, Chromium, module media server | Revert `tests/e2e/test_meeting_upload.py` only |
| 2 | Correct six transcript tests and cleanup | PR4 after PR3A → main | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v --tb=short` | Active Docker stack, Chromium, module media server | Revert `tests/e2e/test_transcript_viewer.py` only |

## Phase 1: PR3A Upload Harness

- [x] 1.1 In `tests/e2e/test_meeting_upload.py`, await the exact `Import a meeting` CTA and remove the obsolete/fallback locator; keep the empty-feed and dialog preconditions.
- [x] 1.2 Add the browser `File`/`DataTransfer` empty-MIME helper in `tests/e2e/test_meeting_upload.py`; assert `File.type == ""` before dispatching `change`, without editing `tests/e2e/fixtures/short-video.mp4` (read-only).
- [x] 1.3 Preserve and verify presign/PUT `audio/mpeg`, raw meeting `content_type: ""`, normal video `video/mp4`, storage CORS, and range assertions.
- [x] 1.4 Ensure `tests/e2e/test_meeting_upload.py` closes browser/page resources and module-owned media-server resources on success and failure; never stop shared Docker.

## Phase 2: PR4 Transcript Harness

- [x] 2.1 In `tests/e2e/test_transcript_viewer.py`, await `get_by_role("tab", name="Transcript", exact=True)` and transcript content; remove the count-based fallback.
- [x] 2.2 Use exact, player-scoped `Play`/`Pause` locators and bounded media readiness; retain supported-video playback/rate and audio-regression coverage.
- [x] 2.3 Verify seeking, transcript highlighting, audio identity/lifecycle, 375px layout reservation, `role="status"` media errors, transcript usability, and accessibility.
- [x] 2.4 Harden `tests/e2e/test_transcript_viewer.py` teardown to close browser/page resources and stop/join the owned media server on every path.

## Phase 3: Focused Verification and Evidence

- [x] 3.1 From the repository root, verify active Docker prerequisites with `docker compose up -d` and `docker compose ps` (web :3001, API :8000, PostgreSQL :5433, MinIO :9000); confirm Chromium and web readiness.
- [x] 3.2 Run, in order, `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short`, `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v --tb=short`, then `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v --tb=short`; require 4/4, 6/6, and 10/10 passing.
- [x] 3.3 Record command output, `docker compose ps`, `git diff --check`, diff-stat, changed-file allow-list, fixture hash/no-diff, and absence of orphaned server threads.

## Phase 4: Acceptance and Rollback Notes

- [x] 4.1 Verify the implementation diff contains only `tests/e2e/test_meeting_upload.py` and `tests/e2e/test_transcript_viewer.py`; keep product/package/cloud/mobile/transcript-schema paths and `docker-compose.yml` (read-only) unchanged.
- [x] 4.2 Enforce the combined authored diff at or below 600 lines; merge PR3A before PR4 toward main, and roll back PR4 first, then PR3A.

All design threat-matrix rows are explicitly N/A; no RED-test tasks are applicable. Completion requires the three focused commands, protected-file evidence, and cleanup evidence.

## PR3A Apply Evidence

| Evidence | Result |
|----------|--------|
| Focused test command and exact result | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short` — 4 passed in 2.32s. |
| Runtime harness command/scenario and exact result | Same focused Playwright command against the active local Docker web at `http://localhost:3001`; upload modal, cancellation, normal video MIME, and empty-MIME fallback scenarios passed. |
| Rollback boundary | Revert `tests/e2e/test_meeting_upload.py` only; leave the fixture, PR4 module, product code, and Docker stack unchanged. |
| Cleanup evidence | The module autouse fixture closed every Playwright page on all four passing paths; this module owns no media server, and no Docker shutdown was invoked. |

## Combined Verification Evidence

### Completed PR4 and verification tasks

The committed PR4 correction at `0dae9b6` was verified together with the committed PR3A correction at `591e69c`. No product code, test code, fixture, package, Docker, cloud, mobile, or transcript-schema file was modified during this verification.

| Evidence | Exact result |
|----------|--------------|
| PR4 focused test command | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v --tb=short` — exit 0; 6 collected, 6 passed in 5.33s. |
| Combined focused test command | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v --tb=short` — exit 0; 10 collected, 10 passed in 7.07s. |
| PR3A focused test command in this verification | `$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short` — exit 0; 4 collected, 4 passed in 2.37s. |
| Runtime harness | All three commands used Chromium against the active local Docker web at `http://localhost:3001`; upload, transcript, media, accessibility, layout, MIME, CORS, range, seeking, highlighting, and lifecycle assertions passed. |
| `git diff --check` | Exit 0; no output. |
| Docker prerequisites | `docker compose ps` — exit 0. `web` is `0.0.0.0:3001->3000/tcp`, `api` is `0.0.0.0:8000->8000/tcp`, `db` is `0.0.0.0:5433->5432/tcp`, and `minio` is `0.0.0.0:9000-9001->9000-9001/tcp`; all services were `Up`. The active stack was reused; `docker compose up -d` was not invoked to avoid mutating the user-owned running stack. |
| Fixture integrity | `tests/e2e/fixtures/short-video.mp4` SHA-256 is `607ff1ff56d72e93d7948bd5fe741cce8cc701bf4927a9fff0d1ac90643fea46`; expected hash matched; correction-range and worktree fixture diff checks both exited 0. |
| Committed changed-file allow-list | `git diff e46d286..HEAD --name-only` returned exactly `tests/e2e/test_meeting_upload.py` and `tests/e2e/test_transcript_viewer.py`; allow-list exact. |
| Combined authored size | `git diff --numstat e46d286..HEAD` total is 106 additions plus 45 deletions = 151 changed lines, within the approved 600-line exception boundary. |
| Cleanup/process evidence | Each suite completed successfully; after the combined run, zero Python/pytest processes remained that could own the in-process `media_server` thread. No orphaned module-owned media-server process/thread evidence was found, and shared Docker was not stopped. |
| Rollback boundary | Revert PR4 (`0dae9b6`) first, then PR3A (`591e69c`); the verification metadata is limited to this task artifact. |

### Verification inventory

The post-run working-tree inventory remained ` M frontend-2/next-env.d.ts`, `?? openspec/changes/video-transcription-e2e/`, and `?? openspec/changes/video-transcription-playback/`, matching the acquired untracked-inventory boundary. Tasks 4.1–4.2 remain pending for final SDD verification and archive.

## Acceptance Evidence

- **4.1 scope and protected paths:** Accepted from the recorded exact allow-list: only `tests/e2e/test_meeting_upload.py` and `tests/e2e/test_transcript_viewer.py` changed in the correction range; product, package, cloud, mobile, fixture, transcript-schema, and Docker configuration paths remained unchanged.
- **4.2 size, sequence, and rollback:** Accepted from the recorded 106 additions plus 45 deletions (151 authored changed lines), within the approved 600-line `exception-ok` limit. PR3A `591e69c` precedes PR4 `0dae9b6` toward `main`; rollback is PR4 first, then PR3A.
- **Current git-state cross-check:** `HEAD` resolves to `main`, and the recorded reflog sequence ends with `591e69c` followed by `0dae9b6`, consistent with the stacked boundary. The prior verification evidence and worktree inventory were preserved unchanged above.
- **Historical status note:** The preceding inventory sentence is retained verbatim from before final acceptance; its pending-task wording predates the 4.1 and 4.2 checkbox updates above.
