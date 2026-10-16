---
status: archived
store: openspec
type: design
change: meeting-processing-cancellation
---

# Design: Meeting Processing Cancellation

> Retrospective record written at archive time from the shipped code
> (`aafa488`, `ae6d99d`, `f831819`, `a024587`). The design was worked out
> inline during ODD; this document describes what was built.

## 1. Summary

Every meeting processing dispatch now has an exact `ProcessingRun` identity:
a run id and a `dispatch_attempt_id`. The meeting stores the current run id;
every stage event stores the attempt that produced it. Cancellation targets one
run through `POST /meetings/{meeting_id}/processing-runs/{run_id}/cancel`.
Worker stages fence their writes on run + attempt, fail closed without
identity, and skip notification and embedding for cancelled runs. Temporary
media paths and visual artifacts are attempt-owned. Manual visual analysis on a
completed meeting creates its own `visual_breakdown` run that never reopens
the meeting. Web and mobile clients cancel the current run and render
`cancelled` and `failed` as terminal.

## 2. Binding Decisions (carried from proposal)

1. Cancellation is exact-run and cooperative; later writes from the cancelled
   run are rejected rather than the task being killed.
2. Stages without identity fail closed (`celery.exceptions.Ignore`).
3. Manual visual analysis owns a separate `ProcessingRun`.
4. Original media is never deleted by cancellation; YouTube audio and visual
   artifacts are attempt-scoped with bounded cleanup.

## 3. Data Model & Migration

### 3.1 Identity columns

- `meeting.current_processing_run_id` (nullable integer): the run the meeting
  currently considers authoritative.
- `meetingprocessingrun.dispatch_attempt_id` (nullable `String(32)`, unique
  index): rotated on every dispatch of the run.
- `meetingprocessingevent.dispatch_attempt_id` (nullable `String(32)`): the
  attempt that recorded the event.

### 3.2 Claim indexes

Partial unique indexes on `(run_id, dispatch_attempt_id, stage)` for `started`
events of stages that must execute at most once per attempt (for example
`stage_transliterate` and `stage_summarize`). A duplicate delivery of the same
attempt cannot claim the stage twice.

### 3.3 Migration

`backend/alembic/versions/r8s9t0u1v2w3_add_meeting_processing_generation.py`
(down revision `q5r6s7t8u9v0`) adds the columns and indexes above. All added
columns are nullable; downgrade drops them without data loss. Deployment
requires `alembic upgrade head`.

## 4. Run Lifecycle (`meeting_processing_audit.py`, `meeting.py`)

- `create_run_in_session(session, meeting, trigger=...)` creates a run with a
  fresh attempt id and points `meeting.current_processing_run_id` at it.
- `get_or_create_pending_run(meeting_id, trigger=..., rotate_attempt=True)`
  reuses a pending run for the same meeting but rotates its attempt id, so a
  redispatch invalidates any task still carrying the old attempt.
- `cancel_run(meeting_id, run_id, owner_id)` marks the run cancelled only when
  it is the meeting's current run, is still active, and belongs to the owner.
  It returns `False` otherwise and the endpoint maps that to 409.
- Manual visual analysis: on a `completed` meeting with no visual run queued or
  processing and no other active run (checked under a row lock), create a run
  with trigger `visual_breakdown`, generate a `run_epoch` (`uuid4().hex`), and
  store it in `visual_breakdown_params`. Visual progress updates the run and
  the visual status fields; the meeting status stays `completed`.

## 5. Cancellation API (`meetings.py`)

```
POST /meetings/{meeting_id}/processing-runs/{run_id}/cancel
```

1. Resolve the meeting; 404 if missing or not owned by the caller.
2. `meeting_processing_audit.cancel_run(...)`; 409 when the run is not current
   or no longer active.
3. Respond `{meeting_id, run_id, status: "cancelled", message}` where the
   message states that committed work may remain, an in-flight provider call
   may finish, and later writes from the run are rejected.

## 6. Worker Fence (`worker.py`)

### 6.1 Identity on every stage

Each stage task receives `run_id` and `dispatch_attempt_id`. Before any
persisted write the stage verifies that the run is the meeting's current run,
the attempt matches, and the run is not cancelled. On mismatch the stage
records a rejection event and stops without marking the meeting failed.

### 6.2 Fail closed

A stage invoked without run or attempt identity raises
`celery.exceptions.Ignore` immediately. It does not fall back to a standalone
run against the meeting. This is what broke the pre-existing silent-video
pipeline tests in CI and was corrected in `a024587` by dispatching those tests
with a current run identity.

### 6.3 Downstream effects

- Notifications are skipped when the run is cancelled.
- The embedding stage checks cancellation before indexing.
- Intelligence extraction and summary persistence go through the same fence.

### 6.4 Attempt-owned temporary media

`_temp_path_for(...)` derives the temporary working path from the attempt, so
cleanup of one attempt's working file cannot remove another attempt's input.
Original uploaded media is never deleted by cancellation. Successfully derived
YouTube audio is preserved for reprocessing; in-progress YouTube audio and
visual artifacts are attempt-scoped.

## 7. Visual Artifact Namespace & Cleanup

- `DirectVisionService._artifact_namespace` builds the namespace from owner,
  meeting, visual epoch, run id, and attempt id. It accepts generated
  32-character lowercase UUID-hex epochs and legacy numeric epochs.
- `worker._cleanup_visual_result_artifacts` deletes only the exact
  owner/meeting/epoch/run/attempt namespace of a rejected run. It validates
  that meeting and run ids are positive integers and that a run id is paired
  with an attempt id; any malformed component aborts cleanup without deleting.

Both behaviors were corrected after two independent static reviews: the first
found that generated UUID-hex epochs were rejected by the direct service, the
second found that cleanup identity was not fail-closed.

## 8. Client Surfaces

### 8.1 Shared vocabulary

`packages/shared/src/enums.ts` carries the processing status values used by
both clients, including the terminal `cancelled` and `failed` states.

### 8.2 Web (`frontend-2/`)

- `app/(dashboard)/meetings/[id]/page.tsx`: cancel action bound to the
  meeting's current run id, calling `lib/api.ts`.
- `app/components/processing-queue-item.tsx`, `app/components/status-badge.tsx`,
  `app/contexts/processing-queue-context.tsx`: render and track cancelled runs.
- `app/lib/stage-utils.ts`: classifies `cancelled` and `failed` as terminal so
  no in-progress indicator is shown.

### 8.3 Mobile (`zabt-mobile/`)

- `lib/processing-cancellation.ts` (new) with
  `__tests__/processing-cancellation.test.ts`: decides when a cancel action is
  available and which run id it targets.
- `components/status-banner.tsx`: cancel action and terminal rendering.
- `components/meeting-card.tsx`, `components/meeting/summary-tab.tsx`,
  `lib/queries.ts`, `lib/stage-utils.ts`: terminal-state rendering and query
  wiring.

## 9. Test Infrastructure

`backend/tests/conftest.py`: the `client` fixture is session-scoped while
keeping the real application lifespan. Function-scoped lifespans had caused 11
integration startup errors in the broader local run.

## 10. Tests

- Unit (`backend/app/tests/unit/test_processing_audit.py`,
  `backend/app/tests/unit/services/test_meeting_coverage.py`,
  `backend/tests/unit/test_visual_breakdown_task.py`,
  `backend/tests/unit/test_vision_client.py`,
  `backend/tests/unit/test_embedding_lifecycle.py`,
  `backend/tests/unit/test_silent_video_pipeline.py`): run identity, attempt
  rotation, fence rejection per stage, fail-closed identity, cancelled-run
  notification and embedding skip, manual visual run, namespace acceptance,
  bounded cleanup.
- Integration (`backend/tests/integration/test_processing_audit_endpoint.py`,
  `backend/tests/integration/test_visual_breakdown_endpoints.py`): cancel
  endpoint authorization and 409 behavior, visual run lifecycle.
- Mobile (`zabt-mobile/__tests__/processing-cancellation.test.ts`,
  `zabt-mobile/__tests__/stage-utils.test.ts`): cancel availability and
  terminal classification. Written but not executed (no CI job, local
  `node_modules` absent).

## 11. Rollout & Rollback

**Rollout:** deploy images from Release `v1.1.0`, run `alembic upgrade head`,
then roll workers and API so every dispatch carries identity.

**Rollback:** revert the four commits in reverse order and downgrade
`r8s9t0u1v2w3`; added columns are nullable and claim indexes carry no data.

## 12. Delivery / Review Budget Note

The four work-unit commits total well above the 400-line review budget
(`aafa488` alone is +6612/-469 across 19 files). The repository owner
integrated the commits directly to main without a pull request. No PR slicing
was performed.
