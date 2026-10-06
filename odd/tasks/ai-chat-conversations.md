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
- [x] T2: Frontend thread layout with bounded scroll, conversation list, Markdown + Mermaid rendering, and compact sources UX.
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

### T2 (2026-10-06, branch `feat/actsis-local-stack`, uncommitted)

- Dependency: `npm install mermaid` (run from `frontend-2`) added `mermaid@^12.1.0` to `frontend-2/package.json` and 114 packages to the root `package-lock.json` (the only lockfile the Dockerfile uses); the only version change is `dompurify 3.4.11 → 3.4.16` because mermaid requires `^3.4.12`. `npm ci --dry-run` against a copy of the updated manifests → OK. `frontend-2/package-lock.json` (legacy duplicate) was not touched.
- Typecheck (from `frontend-2`): `npx tsc --noEmit` → exit 0.
- Lint: `npm run lint` crashes on this branch before and after the change (`TypeError: expand is not a function` — root `overrides.brace-expansion: 5.0.6` is forced onto `minimatch@3.1.5`, which `@eslint/config-array` requires). Verified with a temporary `node -r` preload that redirects only `minimatch@3`'s `brace-expansion` to a compatible copy outside the repo: `eslint "app/(dashboard)/ai-chat/page.tsx" app/components/chat-markdown.tsx app/components/mermaid-diagram.tsx app/components/ai-chat-sources.tsx app/lib/ai-chat-citations.ts app/lib/api.ts` → 0 errors, 1 pre-existing warning (`_rememberMe` unused in `api.ts`). The `react-hooks` v7 compiler rules (`set-state-in-effect`) pass.
- Build: `npm run build` → `✓ Compiled successfully`, `/ai-chat` prerendered as static, exit 0.
- Helper checks (Node 24 type stripping, outside the repo): `buildCitationIndex` dedupes repeated sources, numbers citations in API order, appends unknown tokens, and ignores tokens inside ```` ``` ```` fences; `linkifyCitations` leaves fenced blocks untouched and emits `[\[n\]](#cite/<meeting>/<kind>/<chunk>)`; `react-markdown@10` keeps the `#cite/...` href through its default `urlTransform`, renders `[1]` as the link text, and exposes `language-mermaid` on the hast `code` child so the `pre` override can route it to `MermaidDiagram`.
- `git diff --check` (repo root) → clean.
- Runtime harness: N/A for T2 (no running stack in this environment; T3 covers the multi-turn smoke test). Mermaid rendering requires a browser and is covered by T3.
- Rollback boundary: delete `frontend-2/app/components/chat-markdown.tsx`, `frontend-2/app/components/mermaid-diagram.tsx`, `frontend-2/app/components/ai-chat-sources.tsx`, `frontend-2/app/lib/ai-chat-citations.ts`; revert `frontend-2/app/(dashboard)/ai-chat/page.tsx`, `frontend-2/app/lib/api.ts`, `frontend-2/package.json`, and `package-lock.json`. No other page imports the new modules.

### T2 corrective patch (2026-10-06, after independent review, uncommitted)

- Fixed: `parseCitationHref` now guards `decodeURIComponent` (try/catch → `null`), requires exactly three segments, plain-digit ids (meeting id > 0, chunk index ≥ 0 because the API is zero-based), and a kind matching the token charset. Node check (type stripping, outside the repo): `#cite/1/%E0/1`, `#cite//x/`, `#cite/-1/x/1e3`, `#cite/0/summary/0`, `#cite/12/summary/0/extra`, `#cite/12/%20/0` → `null`; `#cite/12/summary/0` → `{12, "summary", 0}`; nothing throws.
- Fixed: citation rewriting now runs on prose segments only — fenced blocks, indented code (4 spaces / tab), and inline backtick spans (same-length closing run) are protected; `buildCitationIndex` uses the same segmenter. Node check: tokens in inline/double-backtick/indented/tabbed/fenced code stay verbatim and are not indexed; prose tokens are linked (`[1,4,8]` indexed, protected ids `2,3,5,6,7` untouched).
- Fixed (`mermaid-diagram.tsx`): `suppressErrorRendering: true` in `initialize`; a unique id per render attempt (module counter + timestamp) instead of per component; the SVG container is `role="img"` labelled by an `sr-only` description derived from the first source line, plus a "Show diagram source" `details`; spinner has `motion-reduce:animate-none`.
- Fixed (`page.tsx`): `pendingScrollRef = "auto"` is set right before the hydrated `setMessages`; `?q=` is captured once (lazy `useState`) and `router.replace("/ai-chat")` consumes it before submitting so reloads/back navigation do not create another conversation; re-selecting the active conversation retries when its previous load failed; the live region announces "Zabt answered." (not the raw answer); sources render only for `evidence_status === "available"` (so `null` behaves like `not_required`); the reindex feedback keeps `role` and drops the contradicting `aria-live`; on 409/404/403 from `POST /ai-chat/` the thread is kept until the retry without `conversation_id` answers, and a second failure shows the normal error; all page spinners have `motion-reduce:animate-none`.
- Fixed (`chat-markdown.tsx`): model `h1`–`h5` are demoted two levels (`h1→h3`, `h2→h4`, `h3→h5`, `h4/h5→h6`) to stay under the page hierarchy.
- Fixed (`ai-chat-sources.tsx`): the snippet number badge uses `sr-only` "Source " text instead of `aria-label` on a plain `span`.
- Verification: `npx tsc --noEmit` → exit 0; shimmed eslint on the six touched files → 0 errors, 1 pre-existing warning; `npm run build` → `✓ Compiled successfully`, `/ai-chat` static, exit 0; `git diff --check` → clean.
- Observed, not authored here: root `package.json` `overrides.dompurify` was changed `3.4.11 → 3.4.16` in the working tree between sessions (matches the recommended follow-up; lockfile already resolved 3.4.16).

## Progress

- T1 done: `AIChatConversation`/`AIChatMessage` models and Alembic revision `b7c8d9e0f1a2`; `AIChatConversationService` (list/get/create/append_turn/delete/recent_turns, owner-scoped 404/403); `AIChatService.chat` accepts `conversation_id`/`history`, validates ownership (404/403) and group match (409) before retrieval, splices a bounded memory window (6 messages / 4000 chars, oldest-first) between the system prompt and the current question, persists both turns on the available/not_required/insufficient paths, persists nothing on 503, and returns `conversation_id`. System prompt now asks for Markdown and `mermaid` fences while keeping the grounding/language rules. Endpoints: `POST /ai-chat/` (optional `conversation_id`), `GET /ai-chat/conversations?group_id=`, `GET /ai-chat/conversations/{id}`, `DELETE /ai-chat/conversations/{id}` (204).
- Review workload: ~1,010 authored lines added across 8 files (tests are ~480 of them). Over the 400-line budget after one honest slicing pass; recommended chain: (1) models + migration + repository service with its tests, (2) chat memory + endpoints with their tests, each flagged `size:exception` if kept as single PRs.
- T2 done: `api.ts` gained `conversation_id` on `AIChatResponse`, `conversationId?` on `askAiChat`, the `AIChatMessage`/`AIChatConversationSummary`/`AIChatConversationDetail` types, `listAiChatConversations`/`getAiChatConversation`/`deleteAiChatConversation`, and `getApiErrorStatus`. The page is now a viewport-bounded `h-full` flex column (the dashboard `AppShell` already clamps `<main>` to `h-screen`, so no header-offset `calc` is needed) with a `280px` left column (native group `<select>` + conversation list with "New chat" and per-conversation delete behind a shadcn `AlertDialog`) and a chat panel whose message list is `flex-1 min-h-0 overflow-y-auto overscroll-contain`, auto-scrolls to the newest message (instant under `prefers-reduced-motion`), and keeps the composer pinned. Selecting a group loads its conversations, selecting one hydrates the thread from the detail endpoint, the first answer in a new chat adopts the returned `conversation_id` and refreshes the list, follow-ups send `conversation_id`, and a 409/404/403 on a stored conversation starts a fresh chat with a short notice and still answers the question. `?q=` still submits once after groups load. Answers render through `ChatMarkdown` (`react-markdown` + `remark-gfm`, `prose prose-stone prose-sm`, horizontally scrollable tables, styled code blocks) with ```` ```mermaid ```` fences routed to `MermaidDiagram` (lazy `import("mermaid")`, initialized once with `startOnLoad: false, securityLevel: "strict"`, raw-source fallback with an error line). Inline `[meeting:<id> kind:<kind> chunk:<index>]` tokens become numbered `[n]` badge links to `/meetings/<id>`; `AiChatSources` shows one chip per meeting with kind counts and a "Show snippets" `details` disclosure grouped by meeting. The `insufficient` reindex action is kept; sources are hidden for `not_required`. A visually hidden `aria-live="polite"` region announces new answers and async feedback.
- Review workload (T2): ~1,520 authored lines (adds + deletions, lockfile excluded) across 7 files. Over the 400-line budget after one honest slicing pass; recommended chain: (1) `api.ts` + `ai-chat-citations.ts` (~215), (2) `chat-markdown.tsx` + `mermaid-diagram.tsx` + `ai-chat-sources.tsx` + the mermaid dependency (~385), (3) the `page.tsx` rewrite (~920, `size:exception` — the thread layout, conversation list, and memory UX are one behavior and do not split cleanly).
- T3 pending. Known follow-ups surfaced by T2: `npm run lint` is broken repo-wide by the `brace-expansion` override (fix the override or drop `minimatch@3`); the root `overrides.dompurify: 3.4.11` pin is now contradicted by the lockfile (`3.4.16`) and should be bumped or removed; the active conversation is not yet reflected in the URL (`?conversation=` would make threads deep-linkable).
