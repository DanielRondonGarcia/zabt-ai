# Proposal: Correct and Verify Video Transcription E2E Slices

## Intent

Make the already-separated PR3A and PR4 browser-test slices a trustworthy verification signal. Current failures are harness drift: an obsolete empty-feed CTA, a race-prone transcript-tab fallback, and browser MIME normalization that prevents the intended empty-MIME contract from being exercised. Product behavior has passed read-only reproductions and must remain unchanged.

## Scope

### In Scope
- **PR3A:** target the current `Import a meeting` CTA; inject an actually empty-MIME browser `File` through `DataTransfer`; retain presign, raw meeting, PUT MIME, storage CORS, and range assertions.
- **PR4:** await the semantic `role="tab"` Transcript tab; remove the fallback race; use exact Play/Pause accessible-name matching; preserve video, audio regression, seek/highlight, cleanup, layout, accessibility, and media-error scenarios.
- Verify with bounded focused commands and deterministic teardown of Playwright resources and module-owned media-server processes.

### Out of Scope
- Product, package, fixture, transcript-schema, visual-pipeline, or mobile changes.
- Cloud Compose or production configuration; preserve local Docker URL `http://localhost:3001`.
- Final tasks 4.1–4.2; no broader E2E-suite expansion.

## Capabilities

### New Capabilities
None — this is test-harness correction and verification only.

### Modified Capabilities
None — no product-level requirements change.

## Approach

Keep the existing PR boundaries and fixture content. Limit PR3A edits to `tests/e2e/test_meeting_upload.py` and its test support; limit PR4 edits to `tests/e2e/test_transcript_viewer.py` and its test support, with PR4 stacked after PR3A. Set `E2E_BASE_URL` explicitly for every run and use the active local Docker stack. Each test module must close browser/page resources and stop and join its media-server child process in teardown, including failure paths.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `tests/e2e/test_meeting_upload.py` | Modified | CTA, empty-MIME fixture, upload/storage assertions. |
| `tests/e2e/test_transcript_viewer.py` | Modified | Tab synchronization and exact playback locators. |
| `tests/e2e/fixtures/short-video.mp4` | Verified only | Content remains unchanged. |

## Test and Cleanup Strategy

Run, in order:
```text
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v --tb=short
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v --tb=short
```
The focused modules, not cloud services or an unbounded suite, own process lifecycle and cleanup.

## Size-Exception Boundary

The approved `exception-ok` allowance is **600 changed lines maximum** for this combined E2E change. `stacked-to-main` boundaries are PR3A first, then PR4 stacked on PR3A; the allowance covers only those two test slices and support edits, never product, dependency, cloud-Compose, or fixture changes.

## Risks and Rollback

Browser MIME behavior may vary by runtime; assert the injected `File.type` and captured request shape. Future accessible-copy changes may recreate selector drift. Roll back by reverting PR4, then PR3A; product code, Docker configuration, and fixture remain untouched.

## Dependencies and Success Criteria

Requires the active local Docker stack, installed pytest-playwright/Chromium, and the existing fixture. Success is 10/10 focused tests passing in all three commands, including upload MIME/CORS/range contracts and every preserved playback, seeking, highlighting, layout, cleanup, and media-error scenario, with no orphaned processes.
