# Failed meeting reprocess action

## Goal
Add a safe `Reprocess` action to failed meeting details so the owner can rerun the stored media pipeline without uploading the file again.

## Scope
- Add a dedicated owner-authorized backend endpoint for failed meetings with stored media.
- Add a failed-state button with submitting feedback and polling transition in the meeting detail UI.
- Add focused backend tests and preserve existing re-transcription behavior.

## Non-goals
- No automatic retry policy.
- No retry for active or completed meetings through this button.
- No media re-upload or storage migration.
- No changes to transcription provider routing.

## Tasks
- [x] Add `POST /meetings/{id}/reprocess` with owner, failed-state, and stored-file guards.
- [x] Clear stale pipeline outputs and dispatch the existing full pipeline.
- [x] Add the failed-banner Reprocess button and queued/polling state.
- [x] Add focused tests and verify the UI/backend build. (Backend integration tests pass with host-mapped PostgreSQL/MinIO endpoints; frontend TypeScript check passes.)

## Acceptance criteria
- Only the meeting owner can reprocess.
- A meeting must be `failed` and have a `file_path`; otherwise the endpoint returns a clear client error.
- Reprocess queues the same stored media, clears stale failure/output state, and dispatches once.
- The button is visible only for failed meetings, prevents duplicate clicks, and reflects queued progress.
- Existing active/completed and existing language re-transcription flows remain unchanged.

## Delivery
- Keep changes uncommitted until explicitly requested.
