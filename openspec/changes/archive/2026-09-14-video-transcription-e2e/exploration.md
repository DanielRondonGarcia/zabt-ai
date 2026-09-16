---
status: success
executive_summary: >-
  The focused E2E suite is reaching the active local Docker stack at port 3001,
  but its current failures are test-harness drift rather than product playback
  failures. Four upload tests use an obsolete CTA and the five transcript tests
  make a race-prone role fallback; after those selectors are corrected, the
  browser-supported video, audio regression, lifecycle, layout, and media-error
  flows pass in read-only reproductions. The empty-MIME case needs a browser-level
  File/DataTransfer fixture because Playwright assigns video/mp4 to an .mp4
  payload even when mimeType is supplied as an empty string.
artifacts:
  - path: openspec/changes/video-transcription-e2e/exploration.md
    store: openspec
    type: exploration
next_recommended: sdd-research
risks:
  - The focused tests are not yet a valid verification signal because their selectors fail before the intended assertions run.
  - The repository has no openspec/config.yaml and no repository-level pytest or Playwright configuration; the command must set E2E_BASE_URL explicitly.
  - Browser file injection normalizes MIME values differently from the intended empty-MIME contract.
skill_resolution: paths-injected
---

## Exploration: Video Transcription E2E

### Current State

The requested work is verification of two already-separated E2E slices, not a
new product implementation. Product commits `08ca1e0`, `44abea7`, and
`5b007bc` are present, while the E2E commits are split into PR3A (`7345539`)
and PR4 (`e46d286`). The working tree has no changes to the two E2E test files,
fixture, or product files; the only unrelated working-tree entries are the
pre-existing `frontend-2/next-env.d.ts` modification and the old untracked
OpenSpec change folder.

The local Compose stack is active and aligned with the new local URL:

- web: `localhost:3001` mapped to container port 3000;
- API: `localhost:8000`;
- PostgreSQL: `localhost:5433` mapped to container port 5432;
- MinIO: `localhost:9000` (console `9001`).

`docker-compose.yml` uses `localhost:3001` for the local frontend URL, API
CORS/origin defaults, and the web port. The cloud Compose files were not part
of this correction scope. Both focused tests default their `BASE_URL` to
`E2E_BASE_URL`, then `FRONTEND_URL`, then `http://localhost:3001`, so the
verification command should still set `E2E_BASE_URL=http://localhost:3001`
explicitly.

The current focused run was:

```text
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v --tb=short
```

It collected 10 tests: the storage CORS/range contract passed and 9 tests
failed in 176.80 seconds. The failures are deterministic setup/locator
failures:

1. All four PR3A tests stop at `test_meeting_upload.py:87`, where the helper
   waits for `button[name="Upload a meeting"]`. The current empty meeting feed
   renders `button[name="Import a meeting"]` (`frontend-2/app/components/meeting-feed.tsx:379`),
   so no upload request or MIME assertion is reached.
2. All five PR4 tests stop in `_open_transcript` at
   `test_transcript_viewer.py:188`. Immediately after `page.goto`, the helper
   checks `.count()` before the asynchronously loaded page has rendered its
   tabs. It therefore selects a fallback `button[name="Transcript"]` locator.
   The current `TabsTrigger` is a semantic `role="tab"` from Base UI
   (`frontend-2/app/components/ui/tabs.tsx:58-70`), not a button, so the click
   waits for the 30-second Playwright default timeout. This includes the
   unsupported-media parameter, which explains the previous harness timeout;
   it is not evidence that the unsupported media response itself hangs.

Read-only browser reproductions against the same running stack confirm the
deeper behavior once the helper enters the intended UI:

- `short-video.mp4` is 4,534 bytes, serves the expected CORS and range headers,
  and loads in Chromium with `readyState=4` and duration `4` seconds. No fixture
  replacement is indicated.
- The video scenario passes visible media, duration, exact play/pause behavior,
  playback-rate changes, timeline seeking, transcript-word highlighting, and
  the 375px layout reservation when the transcript tab is awaited and the Play
  locator is exact.
- The audio regression, media identity cleanup, expired URL, and unsupported
  media scenarios likewise pass in read-only reproductions after the same tab
  synchronization and exact-name correction. Both failure paths show the
  existing accessible `role="status"` message while leaving transcript words
  usable.
- `set_files({"name": "legacy-video.mp4", "mimeType": "", ...})` does not
  produce an empty browser MIME in the current Python 3.14.5,
  pytest-playwright 0.9.0, Chromium 151 environment. The intercepted requests
  contain `video/mp4` for presign, meeting creation, and PUT, while the test
  expects presign/PUT `audio/mpeg` and raw meeting `content_type: ""`.
  Constructing a `File` with `type: ""` through an in-page `DataTransfer`
  reproduces the intended request sequence without changing product code.

There is no `openspec/config.yaml` in the repository, and no repository-level
pytest or Playwright configuration was found. The installed `pytest-playwright`
plugin supplies the `page` fixture; the focused modules own their base URL and
their local media server. The native OpenSpec location is nevertheless
available, so this artifact is written without creating configuration or
touching the prior exhausted change.

### Affected Areas

- `tests/e2e/test_meeting_upload.py` — update the empty-feed CTA locator to the
  current `Import a meeting` copy and replace the `.mp4` `set_files` empty-MIME
  attempt with a deterministic browser-level empty-type File fixture. Keep the
  existing presign, raw meeting, PUT MIME, and storage-provider assertions.
- `tests/e2e/test_transcript_viewer.py` — await the real `role="tab"` before
  clicking; remove the count-time race/fallback, and use exact accessible names
  for `Play` so it cannot also match `Playback speed 1x`. Keep the existing
  supported-video, audio-regression, seeking/highlighting, cleanup, layout,
  accessibility, and range-contract scenarios.
- `tests/e2e/fixtures/short-video.mp4` — verified as a small browser-supported
  fixture; no content change is required.
- `frontend-2/app/components/meeting-feed.tsx` — current source of the
  `Import a meeting` accessible name; product code is evidence for the test
  selector, not an intended change.
- `frontend-2/app/components/ui/tabs.tsx` — current Base UI tab wiring explains
  why the PR4 helper must wait for `role="tab"` rather than guess a button.
- `frontend-2/app/components/upload-modal.tsx` — existing product behavior
  deliberately uses `item.file.type || "audio/mpeg"` for presigning and PUT,
  while preserving the raw `item.file.type` on meeting creation. The test must
  supply an actually empty `File.type` to verify this contract.
- `frontend-2/app/components/sticky-media-player.tsx` and
  `frontend-2/app/(dashboard)/meetings/[id]/page.tsx` — read-only reproductions
  exercise the current unified media path; no product correction is indicated
  by the observed failures.
- `docker-compose.yml` — local port and origin evidence only. Do not modify the
  production cloud Compose topology as part of either E2E slice.

### Approaches

1. **Correct the E2E harness to the current UI and browser semantics** — make
   the smallest test-only changes described above, then run PR3A and PR4
   independently followed by the combined focused command.
   - Pros: preserves the already-complete product commits, keeps PR3A/PR4
     boundaries intact, tests the intended empty-MIME fallback truthfully, and
     removes the race that inflated the prior runtime.
   - Cons: requires a small in-page file-injection helper instead of the current
     one-line `set_files` payload, and selectors must remain synchronized with
     current accessible UI copy.
   - Effort: Low

2. **Change product UI or MIME normalization to satisfy the current tests** —
   rename the current CTA, broaden product fallback behavior for browser MIME
   values, or change the tab implementation to expose a button.
   - Pros: could make the existing test text pass without updating its
     assumptions.
   - Cons: changes user-facing behavior or the upload contract to mask stale
     test setup, risks treating `application/octet-stream` as a known audio
     type, and violates the request to preserve the completed product slices.
   - Effort: Medium, with unnecessary regression risk

### Recommendation

Choose the test-only correction approach. In PR3A, use the current exact
`Import a meeting` CTA and generate the missing-MIME case with a browser
`File(..., { type: "" })` delivered through `DataTransfer`; this preserves the
existing distinction between the conservative presign/PUT fallback and the raw
meeting `content_type`. In PR4, wait for the semantic Transcript tab and use
exact Play/Pause accessible names. Do not change the fixture, media server,
player, transcript contract, local Docker ports, or cloud Compose files.

Run the verification slices with the active stack and an explicit URL:

```text
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v
```

### Risks

- A future UI copy change could recreate the CTA drift; exact accessible names
  should be intentional and kept close to the current user-facing contract.
- Browser MIME normalization is runtime-dependent, so the empty-type helper
  should assert the injected File type or captured request shape before relying
  on the fallback assertions.
- The test modules have no shared repository-level Playwright configuration;
  running them without `E2E_BASE_URL` or against a stale web image can produce
  misleading failures even though the focused defaults currently target 3001.
- The active Docker stack proves local integration prerequisites, but the
  deterministic media server does not prove every production storage provider's
  CORS, signed-URL expiry, or range configuration beyond the contract modeled by
  this slice.
- `openspec/config.yaml` remains absent. Creating it would exceed this
  exploration's allowed file boundary and is not required to persist the new
  artifact.

### Ready for Proposal

Yes. The evidence is sufficient for a narrowly scoped proposal for test-only
E2E corrections split across PR3A and PR4. The proposal should preserve the
existing product and Docker boundaries, record the selector race and browser
MIME-injection behavior, and require the three focused commands above before
the E2E slices are considered verified.

## Key Learnings

1. The active local Docker stack exposes the web application on port 3001 as configured.
2. The four upload failures come from an obsolete `Upload a meeting` selector.
3. The five transcript failures come from a race-prone tab role fallback.
4. Playwright normalizes an empty MIME `.mp4` payload to `video/mp4` in this environment.
5. Read-only reproductions pass media behavior after the test harness enters the intended UI.
