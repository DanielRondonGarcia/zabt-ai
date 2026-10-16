---
status: explored
executive_summary: >-
  Exploration happened inline during Organic Driven Development; no SDD
  explore phase ran. This artifact records, at archive time, the problem that
  was explored and the seams that were reused. Cancellation acted on the
  meeting rather than on a specific run, stale worker tasks could overwrite a
  newer run, and manual visual analysis shared the meeting lifecycle. The
  existing ProcessingRun audit trail (odd/tasks/meeting-processing-audit.md)
  was the natural place to add exact run identity, and the existing Celery
  stage chain was the place to add the write fence.
artifacts:
  - path: openspec/changes/meeting-processing-cancellation/exploration.md
    store: openspec
    type: exploration
next_recommended: none
risks:
  - Every worker stage had to be touched; a missed stage would leave a path that can still overwrite a newer run.
  - Fail-closed identity changes the behavior of any standalone stage invocation, including existing tests.
  - Manual visual analysis moving to its own run changes how clients interpret visual progress on a completed meeting.
skill_resolution:
  mode: none
  injected_skill_paths: []
---

## Exploration: Meeting Processing Cancellation

> Retrospective record. Exploration was done inline during ODD; this file
> summarizes what was found so the archive is complete.

### Current State (before the change)

- Processing runs and stage events already existed from the processing audit
  work (`odd/tasks/meeting-processing-audit.md`), but a run had no attempt
  identity and the meeting did not record which run was current.
- Cancellation could not name a run; any cancel acted on the meeting as a
  whole.
- Worker stages wrote results keyed only by meeting id, so a task from an
  older dispatch could finish after a reprocess and overwrite the newer run.
- Manual visual analysis reused the meeting lifecycle, so cancelling it could
  move a completed meeting out of `completed`.
- Visual artifacts and temporary media were not scoped to an attempt, so
  cleanup for one dispatch could remove another dispatch's files.

### Affected Areas

- `backend/app/models/processing_audit.py`, `backend/app/models/base.py`,
  and a new Alembic migration for identity columns.
- `backend/app/services/meeting_processing_audit.py` and
  `backend/app/services/meeting.py` for run rotation, cancellation, and the
  manual visual run.
- `backend/app/api/v1/endpoints/meetings.py` for the cancel endpoint.
- `backend/app/worker.py` for the fence, fail-closed identity, attempt-owned
  temp paths, and bounded cleanup.
- `backend/app/services/visual_breakdown/direct_service.py` for the artifact
  namespace.
- `frontend-2/`, `zabt-mobile/`, and `packages/shared/` for cancel actions and
  terminal states.

### Approach Taken

Extend the existing audit model with run + attempt identity, fence every
stage on it, give manual visual analysis its own run, scope artifacts to the
attempt, and expose exact-run cancellation to both clients. See `design.md`.

### Ready for Proposal

Not applicable; the change shipped through ODD and these artifacts are a
retrospective record.
