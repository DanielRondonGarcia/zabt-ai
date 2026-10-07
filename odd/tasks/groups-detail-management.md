# Groups detail management

## Objective

Give users a larger, owner-scoped group view at `/groups/{id}` where they can inspect assigned meetings and add, move, or remove meetings without leaving the Groups area.

## Acceptance criteria

- Each group card at `/groups` exposes an explicit accessible `Open group` link to `/groups/{id}` while edit and delete actions remain independent.
- The group detail page safely handles invalid IDs, loading, request failures, and owner-scoped not-found responses.
- The detail header shows the group icon, name, description, created date, assigned count, available count, a back link, and an edit link that returns to `/groups` with edit query state.
- Assigned meetings show title, date, status/sub-status, duration, source metadata, an `Open meeting` link, and a `Remove from group` action.
- Available meetings can be searched, added when unassigned, or moved from another owned group with the current group name shown.
- Assignment actions disable only the meeting being saved, update the local list and counts after success, and leave the list unchanged after failure.
- Save feedback is announced politely, assignment failures include a next step and retry action, empty states link to `/meetings`, and the layout remains usable on small screens without page-wide horizontal overflow.
- The existing owner-scoped API contracts are reused; no backend endpoint was added.

## Affected files

- `frontend-2/app/lib/api.ts` — adds the typed `getGroup` helper; existing typed meeting and assignment helpers remain unchanged.
- `frontend-2/app/(dashboard)/groups/page.tsx` — adds explicit group navigation and supports opening an edit dialog from the detail-page query state.
- `frontend-2/app/(dashboard)/groups/[id]/page.tsx` — new client-side group detail, meeting assignment management, filtering, feedback, and responsive panels.
- `backend/app/services/meeting.py` — includes `Meeting.group_id` in the lightweight owner-scoped meeting projection.
- `backend/app/tests/unit/services/test_meeting_coverage.py` — regression coverage asserts the selected projection columns and returned grouped meeting value.
- `odd/tasks/groups-detail-management.md` — objective, acceptance criteria, affected files, and verification evidence.

## Verification evidence

- `cd frontend-2 && npx tsc --noEmit` — passed.
- `cd frontend-2 && npm run lint -- "app/(dashboard)/groups/page.tsx" "app/(dashboard)/groups/[id]/page.tsx" app/lib/api.ts` — blocked before ESLint analysis by the known `TypeError: expand is not a function` caused by the root `brace-expansion` override.
- Focused lint shim — passed with no errors; it reported only the pre-existing `_rememberMe` unused-variable warning in `app/lib/api.ts`.
- `cd frontend-2 && npm run build` — passed; generated routes include `/groups` and dynamic `/groups/[id]`.
- `cd backend && uv run pytest app/tests/unit/services/test_meeting_coverage.py -q` — passed: 29 tests; 2 existing deprecation warnings.
- `cd backend && uv run python -m compileall -q app` — passed.
- `cd frontend-2 && npx tsc --noEmit` — passed after the backend corrective patch; no frontend files changed.
- `git diff --check` — passed; Git reported only the repository's existing LF-to-CRLF working-copy warnings.
- Runtime harness — not run; Docker was not restarted and no runtime mutation was requested.

## Delivery

- Changes remain uncommitted on `feat/actsis-local-stack` as requested.
