# Dashboard theme control and dark mode

## Objective

Replace the fake sidebar plan and credits block with an accessible light/dark theme control and make the existing dashboard readable in both themes.

## Acceptance criteria

- The sidebar no longer renders `Basic Plan` or `0 of 300 monthly mins used` in its footer.
- The sidebar footer renders a keyboard-accessible theme toggle with a visible `Theme`, `Dark mode`, or `Light mode` label, Sun/Moon icon, `type="button"`, `aria-pressed`, `aria-label`, `title`, and a visible focus style.
- Theme values are exactly `light` and `dark`, persisted under the namespaced `zabt-theme` key, initialized from persisted state or `prefers-color-scheme`, and applied to the document root with `.dark` and `colorScheme` updates.
- Theme initialization avoids server-side browser API access and minimizes first-paint flash or hydration mismatch.
- Shell and sidebar surfaces use semantic theme tokens, while existing meeting, groups, AI Chat, dialog, and card screens remain readable in dark mode through narrowly scoped legacy utility and Tailwind Typography compatibility layers.
- Existing navigation and PostHog behavior remain unchanged.

## Affected files

- `frontend-2/app/components/theme-provider.tsx` — client theme context, persistence, root class/style application, and guarded hook.
- `frontend-2/app/components/theme-toggle.tsx` — accessible sidebar theme control.
- `frontend-2/app/components/sidebar.tsx` — semantic sidebar surfaces and replacement of the fake plan footer.
- `frontend-2/app/components/app-shell.tsx` — semantic shell and mobile header surfaces.
- `frontend-2/app/layout.tsx` — theme provider wiring and pre-hydration theme bootstrap.
- `frontend-2/app/globals.css` — light/dark color-scheme declarations, token backgrounds, scoped legacy dark-mode mappings, and prose-stone/Typography overrides.
- `odd/tasks/theme-dark-mode.md` — acceptance criteria, affected files, and verification evidence.

## Verification evidence

- `cd frontend-2 && npx tsc --noEmit` — passed with no diagnostics.
- `cd frontend-2 && npm run build` — passed; all existing routes compiled and prerendered successfully. Next reported the pre-existing multiple-lockfile workspace-root warning.
- `cd frontend-2 && npm run lint -- "app/components/theme-provider.tsx" "app/components/theme-toggle.tsx" "app/components/sidebar.tsx" "app/components/app-shell.tsx" "app/layout.tsx"` — blocked before ESLint analysis by the known `TypeError: expand is not a function` caused by the root `brace-expansion` override.
- Focused lint shim — passed with no diagnostics using the established temporary preload that redirects only `minimatch@3` to the compatible `brace-expansion` export outside the repository.
- `git diff --check` — passed; Git reported only the existing LF-to-CRLF working-copy warnings.
- Theme static/DOM check — passed; persisted light and preferred dark bootstrap cases updated `.dark` and `colorScheme`, source checks confirmed `zabt-theme` persistence and the toggle contract, and the fake sidebar plan copy was absent.
- Independent dark-mode review — passed after the corrective `.dark .prose` compatibility block; compiled CSS confirms meeting summaries and AI Chat Markdown use readable dark-theme body, heading, code, link, list, quote, caption, and table colors while status accents remain isolated.
- Corrective patch — added `.dark .prose` semantic Typography variables and scoped descendant overrides for prose text, headings, links, code/pre, quotes, lists, captions, and table borders; added reduced-motion transition fallbacks to the mobile drawer and sidebar chevron.
- Corrective `cd frontend-2 && npx tsc --noEmit` — passed with no diagnostics.
- Corrective `cd frontend-2 && npm run build` — passed; all existing routes compiled and prerendered successfully. The same pre-existing multiple-lockfile workspace-root warning was reported.
- Dark prose static CSS check — passed; required semantic `--tw-prose-*` declarations were found in source and compiled CSS, including pre overflow protection.
- Corrective `git diff --check` — passed; only the existing LF-to-CRLF working-copy warnings were reported.

## Delivery

- Committed on `feat/actsis-local-stack`: `7b33244 feat(theme): add persisted dashboard dark mode`.
