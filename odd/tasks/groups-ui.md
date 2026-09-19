# Owner groups management and meeting assignment UI

## Goal
Complete the owner-scoped groups feature in the dashboard so users can create/manage groups, assign or unassign meetings, and then use those groups in `/ai-chat` and retrieval indexing.

## Scope
- Add authenticated frontend API helpers for group CRUD and meeting group assignment.
- Add a Groups management page with empty, loading, error, create, edit, and delete states.
- Add Groups navigation to the dashboard sidebar.
- Add a group selector to the meeting detail header with an explicit unassigned option.
- Keep backend contracts unchanged; preserve existing uncommitted work and do not run delivery commands.

## Tasks

- [x] Define group UI contracts and task evidence.
- [x] Implement groups management page and navigation.
- [x] Add meeting group assignment UI.
- [ ] Run frontend validation and record evidence.

## Acceptance criteria

- An authenticated user can create, rename, describe, and delete an owned group from `/groups`.
- The empty state explains how groups power retrieval and AI Chat.
- An authenticated user can assign any owned group or explicitly remove the group from a meeting detail page.
- Saving assignment updates the meeting state and exposes loading/error feedback without losing the current selection.
- Group operations remain owner-scoped through the existing API; no browser-side provider calls are introduced.
- Existing design-system rules remain intact: warm stone neutrals, rose accent only, no shadows, accessible labels and keyboard-safe dialogs.

## Verification evidence

- `npm run build:shared` — passed.
- `cd frontend-2 && npx eslint "app/(dashboard)/groups/page.tsx" "app/components/meeting-group-selector.tsx" app/lib/api.ts app/components/sidebar.tsx` — passed with one pre-existing warning in `api.ts` (`_rememberMe` unused).
- `cd frontend-2 && npm run build` — passed; generated routes include `/groups` and `/meetings/[id]`.
- `cd frontend-2 && npm run lint` — blocked by pre-existing repository-wide lint errors in integrations, templates, meeting-detail legacy code, upload-modal, and structured-output components. The new Groups page and selector introduce no lint errors.
- `git diff --check` — passed; only existing line-ending warnings were reported.
- `docker compose up -d --build web` — rebuilt and restarted the web container so the deployed localhost:3001 image includes the new `/groups` route and sidebar link.
- Container verification: `/app/frontend-2/app/components/sidebar.tsx` contains the `/groups` link, the groups route exists, and `curl http://localhost:3001/groups` succeeds.

## Follow-up blockers

- [ ] Resolve the pre-existing frontend-wide ESLint errors before claiming the entire frontend lint suite is green. This is outside the groups UI surfaces.

## Delivery

- Keep changes uncommitted until the user explicitly requests delivery.
