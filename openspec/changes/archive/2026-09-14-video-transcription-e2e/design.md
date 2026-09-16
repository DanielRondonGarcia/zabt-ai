# Design: Video Transcription E2E

## Technical Approach

Correct the existing test harness only. PR3A will use the current empty-feed `Import a meeting` action, add an in-page `File`/`DataTransfer` helper that preserves `File.type == ""`, and retain the presign, raw meeting, PUT, CORS, and range contracts. PR4 will replace the asynchronous tab fallback with an awaited semantic tab, use exact `Play`/`Pause` locators, and preserve media, transcript, cleanup, layout, accessibility, and error assertions. The active Compose stack is a verification prerequisite, not product code under test.

## Architecture Decisions

| Decision | Choice | Tradeoff and rationale |
|---|---|---|
| Contract ownership | Modify only the two E2E modules. | Avoids masking drift by changing `meeting-feed.tsx`, `upload-modal.tsx`, tabs, or the player; the observed product contracts already pass read-only reproduction. |
| Empty MIME | Build the browser `File` with `type: ""` through `DataTransfer`, then dispatch `change`. | More code than `set_files`, but Playwright normalizes an `.mp4` payload to `video/mp4`; this is the only truthful fixture for the raw-empty/fallback-transport distinction. |
| Synchronization | Wait on exact semantic UI and bounded media readiness, never `.count()`-based fallback. | Accessible-name drift fails loudly, while waits remove the current 30-second race and keep assertions meaningful. |
| Ownership | pytest-playwright owns browser lifecycle; `media_server` owns its HTTP server thread; the outer verification runner owns Docker. | Tests cannot safely stop a shared Compose stack, but every module-owned server must be stopped, joined, and closed on every path. |

## Data Flow / Test Harness

```text
Docker web :3001 ──→ Playwright page
       │                    ├─ mocked API routes → upload request assertions
       │                    └─ local media URL → ThreadingHTTPServer → media element
       └─ api :8000 / MinIO :9000 are stack prerequisites, not test mutations
```

`_mock_authenticated_dashboard` waits for the exact empty-state CTA before returning. `_open_upload_modal` clicks that CTA and waits for the dialog. `_inject_empty_mime_file(page, name)` base64-transfers fixture bytes into a browser `File`, asserts the resulting `files[0].type` is empty, and dispatches the input event. Its expected request contract is presign `audio/mpeg`, meeting `content_type: ""`, and PUT `audio/mpeg`; the normal video case remains `video/mp4`.

`_open_transcript` waits for `get_by_role("tab", name="Transcript", exact=True)` and transcript text. Media tests then wait for metadata (`readyState >= 1`, finite positive duration), scope controls to the media player, and use exact `Play`, `Pause`, and playback-speed names. Error tests await `role="status"`; lifecycle tests await the new audio/tab state before inspecting the recorded old-media `pause()` call. The existing 375px bounding-box and hidden reservation assertions remain the layout contract.

## File Changes

| File | Action | Description |
|---|---|---|
| `tests/e2e/test_meeting_upload.py` | Modify | CTA wait, browser empty-MIME helper, upload/storage assertions. |
| `tests/e2e/test_transcript_viewer.py` | Modify | Semantic waits, exact controls, media readiness, teardown hardening. |
| `tests/e2e/fixtures/short-video.mp4` | Verify only | Keep the 4,534-byte browser-supported fixture unchanged. |
| `docker-compose.yml` | Verify only | Use existing local ports; do not alter cloud or production topology. |

## Interfaces / Cleanup Contract

- `BASE_URL` remains environment-first; every focused command explicitly sets `E2E_BASE_URL=http://localhost:3001`.
- `media_server` keeps function scope for isolation and unconditionally executes `shutdown()`, `thread.join(timeout=2)`, `server_close()`, then asserts the thread is dead. It is an in-process thread, not an untracked subprocess.
- The outer runner starts/reuses the local stack with `docker compose up -d`, verifies `docker compose ps` and browser readiness, captures evidence, and stops only an isolated stack it owns; tests never run `down` against a shared stack.

## Testing, Evidence, and Sequencing

Run from the repository root, in order:

```text
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py -v --tb=short
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_transcript_viewer.py -v --tb=short
$env:E2E_BASE_URL='http://localhost:3001'; pytest tests/e2e/test_meeting_upload.py tests/e2e/test_transcript_viewer.py -v --tb=short
```

PR3A is the base slice: four upload tests, allow-listed to `test_meeting_upload.py`. PR4 stacks on PR3A: six transcript tests, allow-listed to `test_transcript_viewer.py`. Combined authored change is capped at the approved 600-line `exception-ok` boundary and then targets `main`. Record each command, `docker compose ps`, `git diff --check`, changed-file allow-list, diff-stat, fixture hash/no-diff, and absence of owned server threads. Roll back PR4 first, then PR3A; no product, cloud, package, mobile, fixture, or transcript-schema file may change.

## Threat Matrix

Included because verification crosses Docker/test-process boundaries; no application routing or VCS automation is added.

| Boundary | Applicability | Safe/failure behavior; planned RED boundary |
|---|---|---|
| Documentation-like paths | N/A — no executable-file classification. | No test required. |
| Git repository selection | N/A — commands are fixed and run from the declared root; no path selector is parsed. | Wrong cwd is an operator preflight failure, not a harness route. |
| Commit state | N/A — tests never stage, commit, or inspect index state. | No test required. |
| Push state | N/A — delivery is stacked-to-main metadata only. | No test required. |
| PR commands | N/A — no PR command is composed or executed by this change. | No test required. |

## Migration / Rollout

No migration or feature flag. Verify PR3A, then PR4, then the combined command; retain command output and teardown evidence.

## Open Questions

None.
