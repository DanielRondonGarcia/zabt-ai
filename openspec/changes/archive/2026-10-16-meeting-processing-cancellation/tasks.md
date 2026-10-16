# Implementation Tasks: Meeting Processing Cancellation

> Retrospective record. The task list below mirrors the four work-unit
> commits delivered through ODD and is written at archive time from commit and
> verification evidence. All tasks are checked because the work shipped.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Actual changed lines | +7374 / -494 across 35 files (four commits) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | Unit 1 → Unit 2 → Unit 3 → Unit 4 (one PR per work-unit commit) |
| Delivery strategy | ask-on-risk |
| Chain strategy | not executed; integrated directly to main without a PR |

---

## Decision needed before apply: No (retrospective)
## Chained PRs recommended: Yes
## Chain strategy: not executed
## 400-line budget risk: High

---

## Chain Boundary Summary

The delivered work maps to **4 work-unit commits** on main:

1. **Unit 1 (`aafa488`, backend)**: run identity, migration, cancellation API, worker fence, manual visual run, artifact namespace and cleanup, test fixture, backend tests. 19 files, +6612/-469.
2. **Unit 2 (`ae6d99d`, web)**: exact-run cancel action and terminal states on the meeting page, queue, badge, context, API client, stage utilities, shared enums. 7 files, +224/-13.
3. **Unit 3 (`f831819`, mobile)**: exact-run cancel action in the status banner, terminal states in card and summary tab, queries, stage utilities, new cancellation module with tests. 8 files, +487/-6.
4. **Unit 4 (`a024587`, backend tests)**: silent-video pipeline tests given a current run identity after fail-closed stages broke them in CI. 1 file, +51/-6.

---

## Unit 1: Backend Run Identity, Cancellation, Fence, Visual Run (`aafa488`)

**Goal**: Every dispatch carries a `ProcessingRun` identity; cancellation targets the exact run; worker stages fence writes and fail closed; manual visual analysis owns its run; media and artifacts are attempt-scoped.

- [x] 1.1 Add `meeting.current_processing_run_id`, `dispatch_attempt_id` on processing runs and events, and per-stage claim indexes via migration `backend/alembic/versions/r8s9t0u1v2w3_add_meeting_processing_generation.py`.
  - Acceptance: Columns are nullable; the run attempt id has a unique index; claim indexes are partial on `started` events; downgrade drops everything.
  - Dependencies: None.
  - Evidence: Migration present in `aafa488`; model fields in `backend/app/models/base.py` and `backend/app/models/processing_audit.py`.

- [x] 1.2 Create and rotate run identity in `backend/app/services/meeting_processing_audit.py` (`create_run_in_session`, `get_or_create_pending_run` with attempt rotation) and point the meeting at the current run.
  - Acceptance: Each dispatch yields a fresh attempt id; redispatch of a pending run rotates the attempt.
  - Dependencies: 1.1.
  - Evidence: Covered by `backend/app/tests/unit/test_processing_audit.py` in the 136-pass local run.

- [x] 1.3 Add `cancel_run` and the endpoint `POST /meetings/{meeting_id}/processing-runs/{run_id}/cancel` in `backend/app/api/v1/endpoints/meetings.py`.
  - Acceptance: 404 for missing or foreign meeting; 409 when the run is not current or inactive; success response reports `status: "cancelled"` and the cooperative-cancellation message.
  - Dependencies: 1.2.
  - Evidence: `backend/tests/integration/test_processing_audit_endpoint.py` passes in the 136-pass local run.

- [x] 1.4 Fence every worker stage in `backend/app/worker.py` on run + attempt; record rejection instead of writing for stale or cancelled runs; raise `celery.exceptions.Ignore` when identity is missing.
  - Acceptance: A stage from a superseded attempt cannot overwrite the current run; a stage without identity does not run standalone and does not mark the meeting failed.
  - Dependencies: 1.2.
  - Evidence: `backend/app/tests/unit/test_processing_audit.py`, `backend/tests/unit/test_visual_breakdown_task.py`; CI-equivalent `cd backend && pytest tests/unit -q` → 464 passed.

- [x] 1.5 Skip notifications for cancelled runs (`backend/app/services/notifications/__init__.py`) and make the embedding stage respect cancellation; route intelligence writes through the fence (`backend/app/services/meeting_intelligence.py`).
  - Acceptance: No notification or embedding side effect for a cancelled run.
  - Dependencies: 1.4.
  - Evidence: `backend/app/tests/unit/test_embedding_lifecycle.py` and `backend/tests/unit/test_embedding_lifecycle.py` updated in `aafa488`.

- [x] 1.6 Make temporary media paths attempt-owned (`_temp_path_for`); preserve original media and successful YouTube-derived audio; scope YouTube audio and visual artifacts to the attempt.
  - Acceptance: Cleanup of one attempt never removes another attempt's inputs or the original upload.
  - Dependencies: 1.4.
  - Evidence: Covered by worker unit tests in the 464-pass CI-equivalent run.

- [x] 1.7 Give manual visual analysis its own `ProcessingRun` (trigger `visual_breakdown`) created only on a completed meeting with no active run; visual-only progress does not reopen the meeting (`backend/app/services/meeting.py`, `backend/app/services/meeting_processing_audit.py`).
  - Acceptance: Cancelling the visual run leaves meeting status, summary, transcript, and media unchanged.
  - Dependencies: 1.2.
  - Evidence: `backend/app/tests/unit/services/test_meeting_coverage.py` and `backend/tests/integration/test_visual_breakdown_endpoints.py` in the 136-pass local run.

- [x] 1.8 Accept generated 32-char lowercase UUID-hex visual epochs (plus legacy numeric) in `DirectVisionService._artifact_namespace`; make `worker._cleanup_visual_result_artifacts` delete only the exact owner/meeting/epoch/run/attempt namespace and fail closed on malformed identity.
  - Acceptance: UUID-hex epochs build a namespace; cleanup with non-positive ids or an unpaired attempt performs no deletion.
  - Dependencies: 1.7.
  - Evidence: Both gaps were found by independent static reviews and closed before release; `backend/tests/unit/test_vision_client.py` and `backend/tests/unit/test_visual_breakdown_task.py` pass.

- [x] 1.9 Make the `client` fixture in `backend/tests/conftest.py` session-scoped while keeping the real application lifespan.
  - Acceptance: Integration tests no longer fail at startup.
  - Dependencies: None.
  - Evidence: Resolved 11 integration startup errors observed in the earlier broader local run.

---

## Unit 2: Web Exact-Run Cancel (`ae6d99d`)

**Goal**: The web meeting page cancels the current run and renders cancelled and failed as terminal.

- [x] 2.1 Add the cancel action bound to the current run id in `frontend-2/app/(dashboard)/meetings/[id]/page.tsx` and the API call in `frontend-2/app/lib/api.ts`.
  - Acceptance: The request targets `/processing-runs/{run_id}/cancel` with the current run id.
  - Dependencies: 1.3.
  - Evidence: Frontend type-check green in CI on `a024587`; web image build green.

- [x] 2.2 Render cancelled runs in `processing-queue-item.tsx`, `status-badge.tsx`, and `processing-queue-context.tsx`; classify `cancelled` and `failed` as terminal in `frontend-2/app/lib/stage-utils.ts`; extend `packages/shared/src/enums.ts`.
  - Acceptance: No in-progress indicator for cancelled or failed runs.
  - Dependencies: 2.1.
  - Evidence: Type-check green; web unit tests not executed (no CI job, local `node_modules` absent).

---

## Unit 3: Mobile Exact-Run Cancel (`f831819`)

**Goal**: The mobile status banner cancels the current run and the card and summary tab render terminal states.

- [x] 3.1 Add `zabt-mobile/lib/processing-cancellation.ts` with `__tests__/processing-cancellation.test.ts`; add the cancel query in `lib/queries.ts`.
  - Acceptance: Cancel availability and target run id are derived from the current run.
  - Dependencies: 1.3.
  - Evidence: Module and tests present in `f831819`; tests written but not executed (no CI job, local `node_modules` absent).

- [x] 3.2 Add the cancel action and terminal rendering in `components/status-banner.tsx`; render terminal states in `components/meeting-card.tsx` and `components/meeting/summary-tab.tsx`; extend `lib/stage-utils.ts` with `__tests__/stage-utils.test.ts`.
  - Acceptance: Cancelled and failed are terminal; polling stops.
  - Dependencies: 3.1.
  - Evidence: Present in `f831819`; same execution limitation as 3.1.

---

## Unit 4: Silent-Video Pipeline Tests Given Run Identity (`a024587`)

**Goal**: Fix the CI failures caused by fail-closed stages in pre-existing tests.

- [x] 4.1 Dispatch the silent-video pipeline tests in `backend/tests/unit/test_silent_video_pipeline.py` with a current run identity.
  - Acceptance: The 3 CI failures of the fail-closed class pass.
  - Dependencies: 1.4.
  - Evidence: GitHub CI green on `a024587` (ruff, backend unit tests, frontend type-check, GHCR helper tests); Docker build and gitleaks green.

---

## Validation Gates

- [x] Focused backend suites pass: 136 passed / 0 failed across `backend/tests/integration/test_processing_audit_endpoint.py`, `backend/tests/integration/test_visual_breakdown_endpoints.py`, `backend/app/tests/unit/services/test_meeting_coverage.py`, `backend/tests/unit/test_visual_breakdown_task.py`, `backend/tests/unit/test_vision_client.py` against isolated local Postgres/MinIO.
- [x] CI-equivalent backend unit suite passes: `cd backend && pytest tests/unit -q` → 464 passed.
- [x] GitHub CI green on `a024587`; Docker build and gitleaks green.
- [x] Release `v1.1.0` built and promoted all 9 images (api, worker, web, gpu-worker, cpu-worker, minio, mc, vision-worker, bot-worker) from `f831819`.

Not executed (recorded as known gaps, not gates):

- Web and mobile unit tests were not run: no CI job exists and local `node_modules` were absent. Only type-check and the web image build passed.
- Browser/device smoke test of the cancel actions is pending.

---

## Rollback Procedure

- **Unit 4 rollback**: revert `a024587`; the silent-video tests return to the pre-fence shape and fail under fail-closed identity.
- **Unit 3 rollback**: revert `f831819`; mobile returns to meeting-level status rendering.
- **Unit 2 rollback**: revert `ae6d99d`; web returns to meeting-level status rendering.
- **Unit 1 rollback**: revert `aafa488` and downgrade `r8s9t0u1v2w3`; added columns are nullable and claim indexes carry no data.

Revert in reverse order; each unit is removable without touching unrelated work.
