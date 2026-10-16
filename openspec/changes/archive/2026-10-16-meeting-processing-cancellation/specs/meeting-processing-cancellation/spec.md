# Delta for meeting-processing-cancellation

This delta represents the full canonical specification for the `meeting-processing-cancellation` capability because no prior canonical spec existed. Archive-time tooling will copy this content into `openspec/specs/meeting-processing-cancellation/spec.md`.

## ADDED Requirements

### Requirement: Every processing dispatch carries a ProcessingRun identity

Every pipeline dispatch MUST create or rotate a `ProcessingRun` with a run id and a unique `dispatch_attempt_id`, and the meeting MUST record its current run in `current_processing_run_id`. Stage events MUST record the attempt that produced them.

#### Scenario: Reprocess creates a new current run

- GIVEN a meeting with a previous completed run
- WHEN the owner triggers reprocessing
- THEN a new `ProcessingRun` with a fresh `dispatch_attempt_id` is created, the meeting's `current_processing_run_id` points at it, and the worker chain is dispatched with that run id and attempt id

#### Scenario: Stage events are attributed to an attempt

- GIVEN a run with a dispatch attempt id
- WHEN any pipeline stage records a started, completed, failed, or skipped event
- THEN the event stores the same run id and `dispatch_attempt_id` as the dispatch that executed it

### Requirement: Cancellation targets the exact current run

The cancellation endpoint `POST /meetings/{meeting_id}/processing-runs/{run_id}/cancel` MUST cancel only the run identified by `run_id` when it is the meeting's current active run and the caller owns the meeting. Requests for a non-current, inactive, or foreign run MUST be rejected without mutating any run.

#### Scenario: Owner cancels the current active run

- GIVEN the authenticated owner's meeting has an active current run with id R
- WHEN the owner calls the cancel endpoint with `run_id = R`
- THEN the run is marked cancelled, the response reports `status: "cancelled"` and states that committed work may remain and later writes from the run are rejected

#### Scenario: Cancelling a stale or non-current run is rejected

- GIVEN the meeting's current run is R2 and an earlier run R1 exists
- WHEN the owner calls the cancel endpoint with `run_id = R1`
- THEN the endpoint returns 409 Conflict and neither R1 nor R2 changes state

#### Scenario: Non-owner cannot cancel

- GIVEN user B does not own the meeting
- WHEN user B calls the cancel endpoint for any run of that meeting
- THEN the endpoint returns 404 Not Found and no run changes state

### Requirement: Worker writes are fenced on run and attempt identity

Every worker stage MUST fence its persisted writes on the run id and dispatch attempt id it was dispatched with. A stage whose run is no longer the meeting's current run, whose attempt id does not match, or whose run has been cancelled MUST NOT persist transcript, summary, intelligence, embedding, notification, or status changes for that meeting.

#### Scenario: Stale attempt cannot overwrite a newer run

- GIVEN run R1 attempt A1 is still executing when the owner reprocesses and run R2 attempt A2 becomes current
- WHEN a stage from R1/A1 finishes and attempts to write its result
- THEN the write is rejected, the meeting keeps R2's state, and R1's stage records the rejection without marking the meeting failed

#### Scenario: Cancelled run skips notification and embedding

- GIVEN a run that was cancelled while a stage was in flight
- WHEN the chain reaches the notification or embedding stage for that run
- THEN the notification is not sent and the embedding stage does not index content for that run

### Requirement: Stages without run identity fail closed

A worker stage invoked without a run id and dispatch attempt id MUST NOT run standalone against the meeting. It MUST stop by raising `celery.exceptions.Ignore` and MUST NOT mark the meeting failed.

#### Scenario: Stage dispatched without identity is ignored

- GIVEN a stage task is invoked with no run id or no attempt id
- WHEN the task starts
- THEN it raises `Ignore`, performs no meeting writes, and leaves the meeting status unchanged

### Requirement: Original media and successful YouTube-derived audio are preserved

Cancellation and stale-run rejection MUST NOT delete the original uploaded media. Successfully derived YouTube audio MUST be preserved for reprocessing. Temporary working files MUST be attempt-owned so one attempt's cleanup cannot remove another attempt's inputs.

#### Scenario: Cancelled run keeps original media

- GIVEN an uploaded meeting whose current run is cancelled mid-transcription
- WHEN the run's cleanup executes
- THEN the original media object remains in storage and the owner can reprocess the meeting

#### Scenario: Temporary paths are attempt-scoped

- GIVEN two attempts A1 and A2 for the same meeting
- WHEN each attempt resolves its temporary media path through `_temp_path_for`
- THEN the paths differ per attempt and cleanup of A1's path does not remove A2's working file

### Requirement: Manual visual analysis runs under its own ProcessingRun

Manual visual analysis on a completed meeting MUST create a separate `ProcessingRun` with trigger `visual_breakdown`, only when no other run is active. Cancelling that run MUST preserve the completed meeting status, summary, transcript, and media. Visual-only progress MUST NOT reopen the meeting lifecycle.

#### Scenario: Manual visual run does not reopen the meeting

- GIVEN a completed meeting
- WHEN the owner starts manual visual analysis
- THEN a run with trigger `visual_breakdown` is created and the meeting status remains `completed` while the visual run progresses

#### Scenario: Cancelling the visual run preserves completed artifacts

- GIVEN a completed meeting with an active `visual_breakdown` run
- WHEN the owner cancels that run
- THEN the run is cancelled, and the meeting's status, summary text, transcript text, and media are unchanged

#### Scenario: Manual visual analysis is refused while another run is active

- GIVEN a meeting with an active pipeline run
- WHEN manual visual analysis is requested
- THEN no visual run is created and the active run is unaffected

### Requirement: Visual artifacts are attempt-scoped and cleanup is bounded

Visual result artifacts MUST be written under a namespace that includes owner, meeting, visual epoch, run id, and attempt id. `DirectVisionService._artifact_namespace` MUST accept generated 32-character lowercase UUID-hex epochs and legacy numeric epochs. Rejected-run cleanup MUST delete only the exact namespace of the rejected owner/meeting/epoch/run/attempt and MUST fail closed when any identity component is malformed.

#### Scenario: UUID-hex epoch is accepted

- GIVEN a visual run whose epoch is a 32-character lowercase hexadecimal string
- WHEN the direct vision service resolves the artifact namespace
- THEN the namespace is built successfully and artifacts are written under it

#### Scenario: Cleanup deletes only the rejected attempt's namespace

- GIVEN visual artifacts exist for attempts A1 and A2 of the same meeting and epoch
- WHEN `_cleanup_visual_result_artifacts` runs for the rejected attempt A1
- THEN only the A1 namespace is deleted and the A2 artifacts remain

#### Scenario: Malformed identity aborts cleanup

- GIVEN a cleanup request with a non-positive meeting or run id, or a run id without its paired attempt id
- WHEN `_cleanup_visual_result_artifacts` validates the identity
- THEN no deletion is performed

### Requirement: Clients expose exact-run cancellation and terminal states

Web and mobile clients MUST offer a cancel action that targets the meeting's current run id, MUST render `cancelled` and `failed` as terminal states, and MUST stop treating a cancelled run as in-progress. Shared status vocabulary lives in `packages/shared/src/enums.ts`.

#### Scenario: Web meeting page cancels the current run

- GIVEN the web meeting page shows an active processing run
- WHEN the user confirms the cancel action
- THEN the client calls the cancel endpoint with the current run id and the status badge and processing queue show the run as cancelled

#### Scenario: Mobile status banner reflects cancellation

- GIVEN the mobile status banner shows an active run
- WHEN the user cancels the run
- THEN the banner, meeting card, and summary tab render the cancelled state as terminal and stop polling for stage progress

#### Scenario: Failed and cancelled states are not in-progress

- GIVEN a meeting whose last run ended in `failed` or `cancelled`
- WHEN the client derives stage progress
- THEN the stage utilities classify the run as terminal and no in-progress indicator is shown
