# Meeting summary pipeline optimization
Objective: Reduce long-meeting summary latency, preserve successful outputs after downstream failures, and make each model operation separately auditable and resumable; no production meeting data changes.
Branch: `perf/summary-pipeline-stages`; forecast: ~600 authored lines; delivery strategy: `ask-on-risk`, chain strategy: `feature-branch-chain`; backend runner: `uv run pytest tests/unit -q` from `backend`; frontend runner: `npx --no-install tsc --noEmit --incremental false` from `frontend-2`.

## Specs
- S1: "claro, necesito que optmimices esos steps, entonces si allí se estan haciendo mas cosas que solo el resumen, partelo en otro step para entender mejor". Check: 128-minute synthetic meetings produce materially fewer sequential partial-summary calls with 10-minute temporal windows while preserving all evidence and the existing per-chunk input-token bound.
- S2: "partelo en otro step para entender mejor". Check: title inference, highlight extraction, and structured-output extraction each have their own processing-audit stage and independently visible start, duration, and terminal result.
- S3: "mira, ya ha fallado 5 veces el summary, revisa ese meeting 15 que está mal, pq eso no debería de demorar nada con ese modelo dado glm.6.3-flash". Check: a successful summary remains unchanged if a later operation fails; resume skips completed model operations and dispatches only missing/failed work, without repeating summary or completed extraction.

## Tasks
- T1 [S1] in_progress — reduce long-summary chunk fan-out under the existing evidence-token budget; inline; commit pending; Risk: item 5 (Compose service defaults).
- T2 [S3] pending — preserve successful summaries and keep downstream errors out of summary content; inline; commit pending; Risk: item 1 (persisted meeting summary behavior).
- T3 [S2] pending — separate title, highlights, and structured-output model operations into sequential audited Celery stages; inline; commit pending; Risk: item 4 (task dispatch and attempt claims).
- T4 [S2-S3] pending — resume from prior completed stage events and add stage-specific tests/UI labels; inline; commit pending; Risk: item 4 (resume and duplicate-delivery behavior).
- T5 [S1-S3] pending — run applicable backend/frontend checks, review the diff, and record final evidence; inline; commit pending.

## Log
- L1: claro, necesito que optmimices esos steps, entonces si allí se estan haciendo mas cosas que solo el resumen, partelo en otro step para entender mejor
- L2: mira, ya ha fallado 5 veces el summary, revisa ese meeting 15 que está mal, pq eso no debería de demorar nada con ese modelo dado glm.6.3-flash
- L3: User selected "Separar cada operación (recomendado)": reduce summary windows to 10 minutes; audit and resume title, highlights, and structured output separately; preserve the summary if a later stage fails.
- L4: The screenshot's five runs are processing-run count, not five summary failures. The latest run's summary succeeded in 43m22s, then `Extract intelligence` failed with `Request timed out.` The matching worker task returned meeting_id 15 after 2602 seconds.
- L5: Repository exploration found `stage_summarize` also calls title inference and `stage_extract_intelligence` performs two sequential whole-transcript calls. Summary chunks currently use 120-second windows; worker provider calls have a 120-second timeout and no SDK retries. The summary provider is owner-selected, while intelligence uses global `OPENAI_MODEL`; runtime model configuration remains unverified.
- L6: The working tree contained unrelated pre-existing dirty files before this feature branch. They remain untouched and must not be staged with this work.
- L7: User selected "Sí, cambia solo esa línea": update only `SUMMARY_CHUNK_SECONDS` from `120` to `600` in `.env.example`; do not read or edit `.env` or secrets.
- L8: The safety guard still refused the exact `.env.example` line edit after the user authorized it. Do not try another tool to bypass the guard; leave that sample untouched and report the mismatch. Application, Compose, and configuration-document defaults remain in scope.
- L9: RED: with the original `SUMMARY_CHUNK_SECONDS=120` field default, `EMBEDDING_PROVIDER=ollama OPENAI_API_KEY=local-test-only uv run pytest --noconftest tests/unit/test_multimodal_context.py -q -k 128_minute_summary` failed at the expected default assertion. GREEN after setting the default to 600: the same focused test passed and the full file passed (7 passed); the token budget remained 6000 and all 64 evidence items were assigned.
- L10: `git diff --check` over the T1 paths exited 0. Updating `backend/app/core/config.py`, all five Docker Compose fallbacks, and `docs/configuration.md` to 600; `.env.example` remains unchanged because the safety guard refused the specific edit even after explicit user consent. The `.env` file was not modified.
- L11: User selected "Cadena desde esta rama de feature (recomendada)" for eventual review slicing. This does not authorize pushing or creating PRs.
