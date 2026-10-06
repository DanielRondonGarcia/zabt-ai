# AI chat conversations

## Objective

Turn the group-scoped AI chat into a persistent, readable conversation experience: remembered turns per chat, a bounded scrollable thread, rich Markdown answers with Mermaid diagrams, and a compact sources presentation that does not repeat the same meeting over and over.

## Problem and rationale

The dashboard chat currently keeps turns only in React state, so every question is answered without prior context and the history disappears on reload. The thread grows the page instead of scrolling inside its own panel. Answers are rendered as plain text, so Markdown lists, tables, code, and Mermaid diagrams returned by the model are unreadable. Every answer lists up to eight raw snippets, most of them from the same meeting, which makes the sources block noisy and redundant.

## Authorized scope

- Persist conversations and messages per owner and group with an Alembic migration; expose list/get/delete endpoints and accept an optional `conversation_id` on `POST /api/v1/ai-chat/`.
- Feed a bounded window of prior turns to the model as conversational memory while keeping retrieval owner/group-authorized and evidence-grounded.
- Keep `evidence_status`, the deterministic no-evidence fallback, and existing 403/404/503 behavior.
- Frontend: conversation list with new-chat action, thread panel with its own scroll area and sticky composer, Markdown rendering with GFM and client-side Mermaid, and grouped/deduplicated sources with inline citation badges.
- Do not persist provider credentials or raw prompts; store only user/assistant content, cited sources, and evidence status.

## Checklist

- [x] T1: Backend conversation persistence, memory window, endpoints, migration, and focused tests.
- [ ] T2: Frontend thread layout with bounded scroll, conversation list, Markdown + Mermaid rendering, and compact sources UX.
- [ ] T3: Rebuild local services, run migration, and smoke-test a multi-turn conversation.

## Acceptance criteria

- A follow-up question in the same conversation can reference the previous answer without the user repeating context.
- Conversations survive reload and are listed per group; deleting one removes its messages.
- The chat thread scrolls inside its panel; the page itself does not grow with the thread.
- Markdown lists, tables, and code render; a ```mermaid fence renders as a diagram with a readable fallback when the diagram is invalid.
- Sources are grouped by meeting with counts and expandable snippets; inline `[meeting:<id> kind:<kind> chunk:<index>]` citations render as compact badges linking to the meeting.
- Focused backend tests and frontend lint/typecheck pass.

## Verification evidence

### T1 (2026-10-06, branch `feat/actsis-local-stack`, uncommitted)

- Focused tests (run from `backend`): `uv run pytest app/tests/unit/services/test_ai_chat_service.py app/tests/unit/api/v1/test_ai_chat.py -q --deselect app/tests/unit/api/v1/test_ai_chat.py::test_api_v1_router_registers_ai_chat_route` → `35 passed, 1 deselected`. The deselected test imports the aggregate router, which opens a connection to `minio:9000` at import time and fails in this environment before and after the change (pre-existing, unrelated).
- Byte-compile: `uv run python -m compileall -q app` → exit 0.
- Migration offline SQL: `uv run alembic upgrade 9a1b2c3d4e5f:head --sql` renders `aichatconversation` + `aichatmessage` with `ON DELETE CASCADE` FKs, `JSONB` sources, and indexes; `uv run alembic heads` → single head `b7c8d9e0f1a2`. No live database touched.
- `git diff --check` → clean.
- Runtime harness: N/A for T1 (no running stack; T3 covers the multi-turn smoke test after migration).
- Rollback boundary: delete `backend/app/models/ai_chat.py`, `backend/app/services/ai_chat_conversations.py`, `backend/alembic/versions/b7c8d9e0f1a2_add_ai_chat_conversations.py`; revert `backend/app/models/__init__.py`, `backend/app/services/ai_chat.py`, `backend/app/api/v1/endpoints/ai_chat.py`, and the two test modules. No other feature is affected.

## Progress

- T1 done: `AIChatConversation`/`AIChatMessage` models and Alembic revision `b7c8d9e0f1a2`; `AIChatConversationService` (list/get/create/append_turn/delete/recent_turns, owner-scoped 404/403); `AIChatService.chat` accepts `conversation_id`/`history`, validates ownership (404/403) and group match (409) before retrieval, splices a bounded memory window (6 messages / 4000 chars, oldest-first) between the system prompt and the current question, persists both turns on the available/not_required/insufficient paths, persists nothing on 503, and returns `conversation_id`. System prompt now asks for Markdown and `mermaid` fences while keeping the grounding/language rules. Endpoints: `POST /ai-chat/` (optional `conversation_id`), `GET /ai-chat/conversations?group_id=`, `GET /ai-chat/conversations/{id}`, `DELETE /ai-chat/conversations/{id}` (204).
- Review workload: ~1,010 authored lines added across 8 files (tests are ~480 of them). Over the 400-line budget after one honest slicing pass; recommended chain: (1) models + migration + repository service with its tests, (2) chat memory + endpoints with their tests, each flagged `size:exception` if kept as single PRs.
- T2 and T3 pending. Frontend note for T2: `AIChatResponse` now carries `conversation_id`; the dashboard page must send it back on follow-ups and can hydrate from `GET /ai-chat/conversations/{id}`.
