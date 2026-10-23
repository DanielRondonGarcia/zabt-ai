# Summary window audit progress
Objective: Make each long-meeting summary window and final synthesis visibly auditable while a summary is running, so operators can see where progress stops.
Branch: `feat/summary-window-audit-progress`; delivery strategy: `ask-on-risk`; runner: isolated backend unit tests and frontend TypeScript/Node progress tests.

## Specs
- S1: "mmm, necesito que dejes traza en logs de auditoria para saber hasta donde llega y si cada bloque de resumen lo está procesando y ver el avance en ese resumen". Check: audit records show each summary window's start and terminal outcome (completed or failed), its 1-based index/total, and final synthesis progress; the meeting page displays those rows live while summarization is active. An omitted partial window is visibly failed even if later windows and synthesis continue.

## Tasks
- T8 [S1] Add per-window/final-synthesis audit events, exact run/attempt fencing, and focused privacy/progress tests; route: delegated writer after context backstop; done, commit pending; focused backend suite: 21 passed; Risk: item 4 (concurrent task attempts and audit fencing).
- T9 [S1] Render per-window status/index/total in processing audit using existing polling; add focused frontend formatter test; route: inline after T8; done, commit pending; Node suite: 5 passed, TypeScript check passed; Risk: item 6 (frontend behavior coverage).
- T10 [S1] Run focused checks, full backend suite, independent verification, and record proof gaps; route: delegated verifier plus parent spot-check; done; commit pending.

## Log
- L1: mmm, necesito que dejes traza en logs de auditoria para saber hasta donde llega y si cada bloque de resumen lo está procesando y ver el avance en ese resumen
- L2: Source inspection confirms summary processing uses temporal/token-budgeted context chunks, but current audit records only a coarse `stage_summarize` start and terminal event. The UI already refreshes the audit during active meeting polling. Each per-call event must reuse the current processing run/attempt and contain only bounded progress metadata (phase/index/count/duration), never prompt, transcript, or summary text.
- L3: Engram mirror save failed with `unable to open database file (14)`; preserve this repository task file as the current record and retry only during normal closeout if the store becomes available.
- L4: T8 delegated implementation: `summarize_context` now emits started/terminal progress for each temporal block, single-block path, transcript-only path, and final synthesis. Worker writes exact run/attempt fenced audit events with block index/total/phase/duration and sanitized failure class; no prompt/transcript/summary content. RED observed; GREEN focused backend suites: 21 passed, 1 Pydantic deprecation. New audit writes are best-effort and do not block summary calls; no DB concurrency test was run.
- L5: Parent UI formatter test GREEN: Node 5 passed; frontend TypeScript check exited 0. Independent verifier: S1 met, no blockers; backend full-suite/tests also pass (513 passed in isolated scratch, 1 Pydantic warning). Remaining evidence gaps: real DB concurrency/race was not exercised; synthetic probe/provider/meeting data untouched; native ASSESS remains unassessable due controller/untracked-file declaration issue.
