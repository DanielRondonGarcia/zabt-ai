# Meeting processing audit trace

## Goal
Provide an owner-scoped audit trail for meeting processing, including what pipeline stages executed, their task IDs/timestamps, bounded operational messages, and the final outcome.

## Scope
- Persist one processing run and bounded stage events for retries, uploads, and other pipeline dispatches.
- Instrument the existing Celery chain without exposing secrets, transcript content, or raw provider payloads.
- Add an owner-authorized API and meeting-detail panel so users can inspect execution history and errors.

## Non-goals
- No raw stdout, Docker logs, stack traces, provider payloads, credentials, presigned URLs, or transcript/summary content in the user-facing trace.
- No external observability migration or change to Sentry, Logfire, or PostHog.
- No automatic retry policy or worker restart.
- No commit, push, PR, or redeploy.

## Tasks
- [x] Define a durable processing-run/event schema, trigger/status vocabulary, bounded message policy, and response contract.
- [x] Add persistence and instrument the existing pipeline stages, failure callback, and reprocess dispatch.
- [x] Add an owner-scoped audit endpoint, frontend API types, and meeting-detail trace panel with polling.
- [x] Add focused tests and verify backend/frontend behavior.

## Acceptance criteria
- A reprocess action creates a trace with trigger, start time, root task ID when available, and final outcome.
- Each pipeline stage records start/completion/failure or skipped status, task ID when available, timestamps, and a bounded safe message.
- Failures are visible without exposing secrets, raw tracebacks, or transcript/summary content.
- Only the meeting owner can read the trace; missing/unauthorized meetings do not leak data.
- The UI refreshes the trace while processing and clearly shows stage status, duration, and errors.
- Existing processing behavior and the Reprocess action continue to work.

## Verification evidence
- `cd backend && DATABASE_URL=postgresql://app:app@localhost:5433/zabt MINIO_ENDPOINT=localhost:9000 uv run pytest app/tests/unit/test_embedding_lifecycle.py tests/integration/test_re_transcribe_endpoint.py tests/integration/test_processing_audit_endpoint.py app/tests/unit/test_processing_audit.py` — 25 passed, 2 warnings.
- `cd frontend-2 && npx tsc --noEmit --incremental false` — passed with no output.
- `cd backend && PYTHONIOENCODING=utf-8 DATABASE_URL=postgresql://app:app@localhost:5433/zabt MINIO_ENDPOINT=localhost:9000 uv run alembic upgrade head --sql` — passed; offline SQL reached revision `9a1b2c3d4e5f` without mutating the database.

## Delivery
- Keep changes uncommitted until explicitly requested.
