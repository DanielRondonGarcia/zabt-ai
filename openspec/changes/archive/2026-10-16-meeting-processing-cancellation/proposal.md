---
status: archived
executive_summary: >-
  Give every meeting processing dispatch an exact ProcessingRun identity (run
  id plus dispatch attempt id) so cancellation can target one run, worker
  stages can fence their writes against stale or cancelled runs, and manual
  visual analysis runs under its own ProcessingRun without disturbing a
  completed meeting. Original media and successful YouTube-derived audio are
  preserved for reprocessing; YouTube audio and visual artifacts are
  attempt-scoped with bounded, fail-closed cleanup. Web and mobile clients
  expose exact-run cancel actions and render cancelled and failed runs as
  terminal states. This change was implemented through Organic Driven
  Development (ODD); this proposal is a retrospective record written at
  archive time from commit and verification evidence.
artifacts:
  - path: openspec/changes/meeting-processing-cancellation/proposal.md
    store: openspec
    type: proposal
next_recommended: none
risks:
  - Fencing every worker stage on run and attempt identity touches the whole Celery chain; any stage left unfenced could still overwrite a newer run.
  - Stages that fail closed without identity change the behavior of any caller that previously invoked a stage standalone; existing tests and tooling that did so must be updated.
  - Manual visual analysis moving to its own ProcessingRun changes how visual-only progress is reported; clients that inferred meeting state from visual status need the new terminal-state vocabulary.
  - Delivery size far exceeds the 400-line review budget; the change was integrated directly to main by the repository owner without a pull request.
skill_resolution:
  mode: none
  injected_skill_paths: []
---

# Proposal: Meeting Processing Cancellation

> Retrospective record. This change was implemented and shipped through Organic
> Driven Development (ODD). The SDD artifacts in this directory were written at
> archive time from commit and verification evidence; no SDD explore, research,
> propose, design, tasks, apply, or verify phase was executed.

## Intent

Meeting processing could not be cancelled precisely. A cancel request acted on
the meeting rather than on a specific run, so it could not distinguish the run
the user saw from a newer one. Worker tasks from an older dispatch could still
finish and overwrite the results of a newer run. Manual visual analysis shared
the meeting lifecycle, so cancelling it could disturb a meeting that had
already completed.

This change introduces an exact `ProcessingRun` identity for every dispatch,
a cancellation API bound to that identity, a write fence in every worker stage,
a separate run lifecycle for manual visual analysis, attempt-scoped temporary
and visual artifacts, and client surfaces that cancel the exact run and render
terminal states.

## User Impact

- Users can cancel the processing run they are looking at, on web and mobile,
  and the cancel action cannot accidentally target a different run.
- A run that was superseded by a reprocess can no longer overwrite the newer
  run's transcript, summary, intelligence, embeddings, or notifications.
- Cancelling manual visual analysis on a completed meeting leaves the
  meeting, its summary, transcript, and media intact.
- Original uploads and successfully derived YouTube audio remain available for
  reprocessing after a cancellation.
- Cancelled and failed runs are shown as terminal states instead of appearing
  stuck in progress.

## Confirmed Product Decisions

Decisions were made inline during ODD by the repository owner and are recorded
here as the binding inputs the implementation followed.

1. **Cancellation is exact-run, not meeting-wide.** The API requires the run
   id and rejects a run that is not the meeting's current active run.
2. **Cancellation is cooperative.** Committed work may remain and an in-flight
   provider call may finish; later writes from the cancelled run are rejected.
3. **Stages fail closed without identity.** A stage invoked without run and
   attempt identity does not run standalone.
4. **Manual visual analysis owns its own run.** It is created only on a
   completed meeting with no other active run, and never reopens the meeting.
5. **Original media is never deleted by cancellation.** YouTube-derived audio
   and visual artifacts are attempt-scoped; cleanup is bounded to the exact
   rejected namespace.

## Goals

- A `ProcessingRun` identity (run id + `dispatch_attempt_id`) on every
  dispatch, persisted on the run, on stage events, and as the meeting's
  `current_processing_run_id`.
- `POST /meetings/{meeting_id}/processing-runs/{run_id}/cancel` that cancels
  only the current active run owned by the caller.
- Worker stages that fence writes on run + attempt, skip notifications and
  embedding for cancelled runs, and raise `celery.exceptions.Ignore` when
  identity is missing.
- Attempt-owned temporary media paths (`_temp_path_for`) and attempt-scoped
  visual artifact namespaces with bounded, fail-closed cleanup.
- Manual visual analysis under a `ProcessingRun` with trigger
  `visual_breakdown` whose cancellation preserves the completed meeting.
- Web and mobile cancel actions bound to the current run id, with `cancelled`
  and `failed` rendered as terminal states.

## Non-Goals

- Hard-killing in-flight provider calls or Celery tasks; cancellation is
  cooperative and relies on the write fence.
- Changing transcription, summarization, intelligence, or visual analysis
  output for runs that complete normally.
- Automatic retry or recovery policies after cancellation.
- Web or mobile unit-test execution in CI (not configured; see Open Issues).

## Capabilities

### New Capabilities
- `meeting-processing-cancellation`: exact-run identity and cancellation,
  stale-run write fencing, fail-closed stage identity, media and artifact
  preservation, separate manual visual run lifecycle, and client terminal
  states.

### Modified Capabilities
- None. `optional-visual-processing` keeps its published behavior; the manual
  visual run gains its own `ProcessingRun` identity without changing the
  chain-safe fallback semantics that spec describes.

## Scope

### In Scope
- Alembic migration `r8s9t0u1v2w3_add_meeting_processing_generation.py`:
  `meeting.current_processing_run_id`, `dispatch_attempt_id` on processing
  runs and events, and per-stage claim indexes.
- Cancellation endpoint and run-rotation logic in
  `backend/app/api/v1/endpoints/meetings.py`,
  `backend/app/services/meeting.py`, and
  `backend/app/services/meeting_processing_audit.py`.
- Run/attempt fencing, fail-closed identity, attempt-owned temp paths,
  cancelled-run notification and embedding skips, and bounded visual artifact
  cleanup in `backend/app/worker.py`.
- UUID-hex visual epoch acceptance in
  `backend/app/services/visual_breakdown/direct_service.py`.
- Session-scoped `client` fixture in `backend/tests/conftest.py` that keeps the
  real application lifespan.
- Web: meeting page cancel action, processing queue item, status badge,
  processing queue context, API client, stage utilities, and shared enums.
- Mobile: status banner, meeting card, summary tab, queries, stage utilities,
  and a new `lib/processing-cancellation.ts` module with tests.

### Out of Scope
- Browser or device smoke testing of the cancel actions (pending).
- A CI job for web or mobile unit tests.
- Pull-request slicing of the delivered commits.

## Approach

1. **Identity.** Extend the audit model so every dispatch rotates a
   `dispatch_attempt_id` and the meeting records `current_processing_run_id`.
2. **Cancellation API.** Resolve the meeting by owner, then ask the audit
   service to cancel the exact run; reject non-current or inactive runs with
   409.
3. **Write fence.** Every worker stage receives run id and attempt id, checks
   them before persisting, and records a rejection instead of writing when the
   run is stale or cancelled. Missing identity raises `Ignore`.
4. **Artifact ownership.** Temporary media paths and visual artifact
   namespaces include the attempt; cleanup for rejected runs validates every
   identity component and deletes only that namespace.
5. **Manual visual run.** Create a `visual_breakdown`-triggered run only on a
   completed meeting with no active run; visual progress updates the run, not
   the meeting status.
6. **Clients.** Expose a cancel action bound to the current run id and extend
   the shared status vocabulary and stage utilities so `cancelled` and
   `failed` are terminal.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/alembic/versions/r8s9t0u1v2w3_add_meeting_processing_generation.py` | New | Current run pointer on meeting, `dispatch_attempt_id` on runs and events, claim indexes. |
| `backend/app/models/base.py`, `backend/app/models/processing_audit.py` | Modified | `current_processing_run_id`, `dispatch_attempt_id` fields and read schemas. |
| `backend/app/api/v1/endpoints/meetings.py` | Modified | Exact-run cancel endpoint; dispatch paths carry run identity. |
| `backend/app/services/meeting.py` | Modified | Run rotation and visual-only progress that does not reopen the meeting. |
| `backend/app/services/meeting_processing_audit.py` | Modified | Run creation, attempt rotation, `cancel_run`, manual visual run creation. |
| `backend/app/services/meeting_intelligence.py`, `backend/app/services/notifications/__init__.py` | Modified | Fence-aware writes and cancelled-run notification skip. |
| `backend/app/services/visual_breakdown/direct_service.py` | Modified | Accept UUID-hex and legacy numeric visual epochs in `_artifact_namespace`. |
| `backend/app/worker.py` | Modified | Run/attempt fencing, fail-closed identity, `_temp_path_for`, `_cleanup_visual_result_artifacts`. |
| `backend/tests/conftest.py` | Modified | Session-scoped `client` fixture with real app lifespan. |
| `backend/app/tests/`, `backend/tests/` | Modified | Unit and integration coverage for identity, fencing, cancellation, visual run, cleanup. |
| `frontend-2/` | Modified | Cancel action on meeting page, queue item, status badge, queue context, API client, stage utilities. |
| `packages/shared/src/enums.ts` | Modified | Shared processing status vocabulary. |
| `zabt-mobile/` | Modified | Cancel action in status banner, meeting card, summary tab, queries, stage utilities, new cancellation module and tests. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| A worker stage is left unfenced and can still overwrite a newer run | Medium | Every stage receives and checks run + attempt; unit tests cover rejection per stage. |
| Callers that invoked stages standalone break under fail-closed identity | Medium | Observed in CI (silent-video pipeline tests); fixed in `a024587` by giving those tests a current run identity. |
| Visual artifact cleanup deletes more than the rejected attempt | Low | Cleanup validates positive-int ids and a paired attempt, and deletes only the exact namespace; static review found and closed this gap. |
| Generated UUID-hex epochs rejected by the direct vision service | Low | Static review found the rejection; `_artifact_namespace` now accepts 32-char lowercase hex plus legacy numeric. |
| Clients still treat cancelled runs as in-progress | Medium | Shared enums and stage utilities updated on web and mobile; mobile cancellation module has unit tests. |
| Change size exceeds the 400-line review budget | High | Delivered as four work-unit commits; integrated directly to main by the repository owner without a PR. |

## Rollback Plan

- Revert the four work-unit commits (`aafa488`, `ae6d99d`, `f831819`,
  `a024587`) in reverse order.
- Downgrade the migration `r8s9t0u1v2w3`; the added columns are nullable and
  the claim indexes carry no data, so no meeting content is lost.
- Clients fall back to the previous meeting-level status rendering once the
  shared enums revert.

## Dependencies

- Existing processing audit trail (`odd/tasks/meeting-processing-audit.md`)
  that introduced `ProcessingRun` and stage events.
- Existing infrastructure unchanged: PostgreSQL, Redis/Celery, MinIO/S3,
  Alembic.
- Deployment requires `alembic upgrade head`.

## Success Criteria (Acceptance Direction)

- [x] Every dispatch creates a run with a unique `dispatch_attempt_id` and the
      meeting records it as current.
- [x] The cancel endpoint cancels only the current active run; stale, inactive,
      and foreign runs are rejected.
- [x] Worker stages reject writes for stale or cancelled runs and raise
      `Ignore` without identity.
- [x] Original media and successful YouTube-derived audio survive
      cancellation; temporary paths are attempt-owned.
- [x] Manual visual analysis runs under its own `visual_breakdown` run and its
      cancellation preserves the completed meeting.
- [x] Visual artifacts are attempt-scoped; cleanup is bounded and fails closed
      on malformed identity.
- [x] Web and mobile expose exact-run cancel actions and render `cancelled`
      and `failed` as terminal.

## Open Issues

1. Web and mobile unit tests were not executed; no CI job exists and local
   `node_modules` were absent. Only type-check and the web image build passed.
2. Browser and device smoke testing of the cancel actions is pending.
3. The delivery exceeded the 400-line review budget and was integrated
   directly to main without a pull request; no PR slicing was performed.
