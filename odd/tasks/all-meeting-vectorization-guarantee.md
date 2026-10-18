# All-Meeting Vectorization Guarantee
Objective: Persist semantic vectors for every processed meeting source before completion, while keeping AI/MCP retrieval gated by group assignment.
Branch: `feat/all-meeting-vector-guarantee` | Delivery: `ask-on-risk` (forecast ~400 authored lines) | Checks: focused `uv run pytest`; frontend `npm run lint` and `npm run build`.

## Specs

### S1 — Vectorize every meeting source before completion
User requirement (verbatim): “eso es un bug, siempre si o si todo debe de tener vectores” and selected scope: “Todas las reuniones”.
Acceptance: meetings from audio, video, and YouTube processing receive at least one persisted Qdrant point before the processing run may complete. A successful embedding stage with zero points is not acceptable. Provider/Qdrant errors must fail the stage and keep the run from `completed`.
Checks: tests prove ungrouped meetings are indexed, successful runs require persisted points, and zero-content/provider/Qdrant failures do not mark the run completed.

### S2 — Group membership gates AI/MCP retrieval, not storage
User clarification (verbatim): “es que debe de ser por cada video que se sube, pero solo estará disponible para IA y MCP cuando se agregue a un grupo, porque los grupos son los puntos de entrada”; later scope selection: “Todas las reuniones”.
Acceptance: vectors may be stored before group assignment, but AI chat and MCP can retrieve meeting content only through an authorized group. Preserve the mandatory group filter and existing `group_service.get_accessible` authorization. Assignment/reassignment must update the vector payload to the current group without allowing stale tasks to expose content under an old group.
Checks: retrieval tests prove ungrouped vectors are unreachable; owner/group access remains enforced; assignment and stale-reassignment tests prove only the current group can retrieve points.

### S3 — Use visual descriptions when a video has no transcript
User selection (verbatim): “Usar descripciones visuales”.
Acceptance: add available visual-segment descriptions (including their time range) to the textual meeting chunks; do not introduce raw-frame or multimodal frame embeddings. If a meeting has no transcript or visual/text content to embed, the index stage must fail rather than manufacture a placeholder vector.
Checks: chunking tests cover visual descriptions and timestamps; worker tests cover transcript-free video with descriptions and the no-indexable-content failure case.

### S4 — Embedding is a required processing gate
User selection (verbatim): “Bloquear completado”.
Acceptance: do not report the processing run as completed until all required meeting chunks have been embedded and persisted. Preserve bounded retries and record the final embedding failure in the run/stage audit. A prior transcription/summary result may remain stored, but must not be presented as fully processed or AI/MCP-searchable before successful indexing.
Checks: deterministic failure tests assert the run is not completed and stage/run failure is recorded; success tests assert vector upsert precedes run completion.

### S5 — Add a meeting-level reindex action
User request (verbatim): “si, reindexalo, pero estaría genial botón de reindexación”.
Acceptance: add a reindex button to the meeting detail UI for users authorized to edit that meeting. Reuse the existing `POST /meetings/{id}/reindex` route and its authorization; show queued/loading/error feedback. The action must work for ungrouped meetings too, without making them retrievable until group assignment.
Checks: frontend lint/build and focused component/handler tests where the current test setup permits; existing API authorization tests remain passing.

### S6 — Restore the affected production meeting after the fix is deployed
User authorization (verbatim): “si, reindexalo”.
Acceptance: after the fixed app version is deployed with fresh path-specific deployment consent, reindex only meeting 7 on ACT-TWC-11 and verify at least one persisted Qdrant point for it, group scoping, and successful processing/index status. Do not reindex other meetings. Do not deploy `v1.4.2` or any new version without the required separate deployment authorization.
Checks: read-only before/after counts are scoped to meeting 7; verify the target collection and group filter; no other meeting/vector data is read or changed.

### Assumptions
- The existing meeting detail page is the intended home for the button, and its existing meeting-editor authorization is the required role boundary.
- Visual descriptions are taken from persisted visual-segment captions; no raw image/frame content is sent to the embedding provider.
- Current real-time WebSocket transcription rejects configured batch-only providers; this change does not add a new realtime transcription capability. All audio/video uploads and YouTube sources handled by the existing processing pipeline are in scope.
- Engram mirror is pending: the save tool returned `unable to open database file (14)`; preserve and retry the full mirror when available.

## Tasks
- T1 — S1-S3 — Inline — Add visual-text chunks and group-optional vector payload while preserving retrieval gates — in progress — commit: TBD.
- T2 — S1,S4 — Inline — Make every meeting's embedding stage required and repair standalone/reassignment reindex sequencing — pending — commit: TBD.
- T3 — S5 — Inline — Add the editor-authorized meeting-detail reindex button with feedback — pending — commit: TBD.
- T4 — S1-S5 — Parent + `gentle-ai-verify` — Run applicable focused/full checks, assess the high-risk candidate, and independently verify vector safety, group isolation, failure handling, and reindex concurrency — pending — commit/candidate: TBD.
- T5 — S6 — Parent — Deploy the fix and reindex only meeting 7 — blocked — waiting for fresh path-specific authorization to deploy a new app version; the target-only reindex itself is authorized — production change: TBD.
- T6 — All — Parent — Persist the full feature document in Engram — blocked — waiting for the Engram database file to become available after `unable to open database file (14)` — memory mirror: pending.

## Log
- L1 (2026-10-18): “eso es un bug, siempre si o si todo debe de tener vectores”
- L2 (2026-10-18): “es que debe de ser por cada video que se sube, pero solo estará disponible para IA y MCP cuando se agregue a un grupo, porque los grupos son los puntos de entrada”
- L3 (2026-10-18): “si, reindexalo, pero estaría genial botón de reindexación”
- L4 (2026-10-18): User selected “Usar descripciones visuales” for transcript-free videos and “Bloquear completado” when embedding fails.
- L5 (2026-10-18): User clarified the guarantee applies to “Todas las reuniones”; embeddings must exist before group assignment, and AI/MCP retrieval remains group-gated.
- L6 (2026-10-18): ACT-TWC-11 read-only verification found the sole active Qdrant collection empty (0 points), meeting 7 assigned to group 1, and worker `reindex_meeting` received then ignored. Its current source deletes before invoking `stage_embedding.run()` without the required audit headers. No production write, reindex, provider call, or deployment has occurred.
- L7 (2026-10-18): Branch `feat/all-meeting-vector-guarantee` created from `origin/main` at `56dc770`. Existing untracked `hallazgos.md` remains untouched and excluded.
- L8 (2026-10-18): Full Engram mirror save failed with `unable to open database file (14)`; local specification remains authoritative and T6 is blocked pending memory service availability.
- L9 (2026-10-18): Reconciled T1 against the current branch and feature document: implementation was not yet present, so T1 remains in progress. Added test-first coverage; RED is observed for visual-description chunking (unsupported `visual_segments` argument) and worker indexing of an ungrouped visual-only meeting (no points upserted). Ungrouped Qdrant payload/filter coverage passes. Backend test import required a test-only storage stub update and fake credentials; broader embedding contract tests also encountered unrelated missing `ACTSIS_CA_BUNDLE` path errors, so only focused new tests provide usable RED/GREEN evidence.
- L10 (2026-10-18): Implemented T1 source changes in `backend/app/services/embeddings/chunk.py`, `backend/app/services/vector_store.py`, and `backend/app/worker.py`: chunk timestamped visual captions, index them for transcript-free videos, and store ungrouped points without a `group_id` payload while leaving retrieval's mandatory group filter unchanged. The focused chunk/vector tests pass 3/3, full embedding contract passes 16/16, and targeted worker lifecycle tests pass 3/3. The full lifecycle file had 7 passing and 4 setup failures because its storage stub lacked symbols imported by the meeting endpoint.
- L11 (2026-10-18): Reconciled the active plan after test runs: T1 remains in progress, with no task marked done. Added the missing storage symbols to the test-only stub; the full lifecycle rerun and diff review are still pending. T2-T4 remain pending and T5-T6 remain blocked for their previously recorded authorization/service-availability reasons.
- L12 (2026-10-18): After correcting the test-only storage stub, full embedding contract tests passed 16/16 and full embedding lifecycle tests passed 11/11 using fake credentials and the local certifi CA bundle. T1 remains in progress pending source-diff review and focused lint/diff checks.
- L13 (2026-10-18): During T1 review, identified that the low-level vector search method accepted `group_id=None` at runtime despite its required annotation, which could omit the authorization filter. Observed RED, added an explicit group-scope guard, and the new guard plus ungrouped-payload/filter tests pass 2/2.
- L14 (2026-10-18): Full post-fix embedding contract tests passed 17/17; full worker lifecycle tests passed 11/11; Ruff and `git diff --check` passed; final source diff reviewed. T1's implementation and checks are complete, but its required feature-branch work-unit commit has not yet been created, so T1 stays in progress until that commit is recorded. T2/T3/T4 remain pending and deployment/mirror T5/T6 remain blocked.
