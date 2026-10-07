# Microsoft Entra public SPA OIDC

## Objective

Make Microsoft Entra OIDC a global Zabt instance capability configured by an
administrator from the frontend, without requiring an OIDC client secret in
`.env`, while preserving local email/password authentication, delegated
Microsoft Graph OAuth, and the read-only MCP work.

## Confirmed product and security decisions

- OIDC configuration is global, not per-user. The database stores only the
  public Microsoft application client ID, tenant GUID or `common`/
  `organizations`/`consumers`, registered SPA redirect URI, enabled state,
  timestamps, and the updating user. No client secret is accepted, stored, or
  returned by the OIDC configuration API.
- The migration adds indexed `user.is_admin` with a false default and grants
  administrator status to the earliest existing user (`MIN(user.id)`) as the
  bootstrap administrator. Later configuration writes require `is_admin`.
- If the migration ran before any usable operator account was present, an
  authenticated active user receives a one-time setup claim while no global
  OIDC configuration row exists. The first save acquires the PostgreSQL
  transaction advisory lock (or deterministic SQLite/test fallback), rechecks
  row existence, promotes that winning user to `is_admin`, and commits the
  promotion plus public OIDC configuration atomically. Once the row exists,
  non-admin users receive read-only status and `403` on writes; a concurrent
  loser cannot overwrite the winning configuration.
- The browser uses `@azure/msal-browser` with Microsoft Entra public-client
  authorization code + PKCE and the `openid profile email` scopes. It creates
  the MSAL application from the public runtime configuration, calls
  `loginPopup`, and sends only `result.idToken` to Zabt.
- The backend validates the signed ID token against Microsoft discovery/JWKS,
  RS256, issuer, audience, tenant policy, subject, expiry, issued-at, nonce,
  and the applicable standard claims before resolving the external identity.
  Token values are bounded and never included in error responses or logs.
- Discovery metadata and JWKS are cached per fixed Microsoft URI with bounded
  five-minute TTLs. Unknown signing-key IDs get one coalesced refresh per
  cooldown window, preventing repeated structurally valid invalid tokens from
  amplifying outbound provider requests.
- Provider/network/status/invalid-payload failures are negative-cached for a
  bounded 30-second cooldown. Failure entries retain only a safe category and
  timestamp; successful refreshes and `clear_microsoft_oidc_caches` remove them.
  Unknown-kid refreshes cannot bypass an active failure cooldown.
- Anonymous OIDC login never auto-links an existing local email. An existing
  local email receives a static conflict response. A new external-only user
  has `password_hash=None`; an inactive user is rejected.
- Explicit linking is a separate authenticated `POST
  /api/v1/auth/microsoft/oidc/link` action. It validates the browser ID token,
  rejects identities owned by another Zabt user, and does not issue or replace
  Zabt cookies.
- The former server-side OIDC start/callback routes are safe compatibility
  `410 Gone` responses and are not used by the UI. OIDC never reads the Graph
  client secret.
- Delegated Microsoft Graph remains a separate server-side flow at
  `/api/v1/integrations/microsoft/connect`. It continues to require the
  deployment-managed `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`,
  `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`, and valid
  `TOKEN_ENCRYPTION_KEY`. Graph readiness is reported separately from OIDC
  readiness.
- First-user admin assignment uses a PostgreSQL transaction-scoped advisory
  lock; SQLite and lightweight test sessions use a process lock fallback. The
  existing migration still marks the earliest persisted user by minimum ID.

## API contract

- Public `GET /api/v1/auth/microsoft/status` returns only public OIDC values,
  `configured`, `enabled`, `openid profile email` scopes, and separate Graph
  and token-storage readiness booleans. It works before login.
- Authenticated `GET /api/v1/auth/microsoft/config` returns the same public
  values plus `can_manage`, `is_admin`, timestamps, and updater metadata.
- Administrator-only `PUT /api/v1/auth/microsoft/config` validates the client
  ID as a GUID, validates the tenant policy, allows HTTP redirect URIs only in
  non-production environments, requires HTTPS in production, requires the
  redirect URI to be the current SPA origin plus `/login`, and upserts the
  singleton configuration. Public status/config responses are `no-store`.
- `POST /api/v1/auth/microsoft/oidc/exchange` validates `{id_token}` and
  issues the existing web HttpOnly cookies. `POST
  /api/v1/auth/microsoft/oidc/link` validates `{id_token}` and links without
  issuing cookies.

## Affected files

- `backend/app/models/base.py` — indexed `User.is_admin` field and existing
  relationship compatibility.
- `backend/app/models/microsoft_oidc_configuration.py`,
  `backend/app/models/__init__.py`, `backend/alembic/env.py` — singleton public
  OIDC model registration and re-export.
- `backend/alembic/versions/e0f1a2b3c4d5_add_global_microsoft_oidc_configuration.py`
  — admin bootstrap and reversible global configuration schema after MCP head
  `d9e0f1a2b3c4`.
- `backend/app/services/microsoft_oidc_configuration.py` — validation and
  singleton persistence without secret fields.
- `backend/app/services/auth.py` — first-registration bootstrap for a brand-new
  empty database, with later registrations remaining non-admin, and atomic
  first-user locking.
- `backend/app/services/microsoft_oidc.py` — public ID-token discovery/JWKS
  verifier, positive/negative metadata/JWKS caches, and external identity
  resolution.
- `backend/app/core/security.py` — returns the validated browser origin for
  same-origin public SPA redirect validation.
- `backend/app/api/v1/endpoints/auth.py` — public status, authenticated admin
  configuration, exchange, explicit link, and retired compatibility routes.
- `frontend-2/app/lib/api.ts`, `frontend-2/app/lib/microsoft-oidc.ts`,
  `frontend-2/app/login/page.tsx` — typed API contract, runtime MSAL PKCE
  helper, current-origin `/login` redirect default, preinitialization, and
  conditional Microsoft sign-in.
- `frontend-2/app/(dashboard)/integrations/page.tsx` — admin form,
  same-origin SPA redirect guidance, non-admin read-only view, explicit link
  action, and separate Graph warning.
- `frontend-2/package.json`, `frontend-2/package-lock.json` — MSAL browser
  dependency.
- `.env.example`, Docker Compose files — public OIDC is database/frontend
  managed; Graph secrets remain deployment managed and optional for boot.

## Verification

- `cd backend && $env:AUTH_COOKIE_SECURE='false'; uv run pytest --confcutdir=tests/unit tests/unit/test_microsoft_graph.py app/tests/unit/services/test_microsoft_graph_coverage.py app/tests/unit/services/test_integration_coverage.py app/tests/unit/services/test_auth_coverage.py tests/unit/test_local_auth.py tests/unit/test_local_auth_router.py tests/unit/test_external_identity.py tests/unit/test_microsoft_oidc.py tests/unit/test_microsoft_auth.py tests/unit/test_microsoft_oidc_config.py tests/unit/test_microsoft_oidc_migration.py tests/unit/test_config_defaults.py -q` — passed, 169 tests and one existing Pydantic deprecation warning, including positive/negative provider caching, same-origin redirect, no-store, advisory-lock, and one-time setup-claim regressions.
- `cd backend && uv run python -m compileall -q app` — passed.
- `cd backend && uv run alembic upgrade d9e0f1a2b3c4:head --sql` — passed; emitted the `is_admin` bootstrap update and `microsoftoidcconfiguration` DDL.
- `cd backend && uv run alembic heads` — passed with `e0f1a2b3c4d5 (head)`.
- `npm install --ignore-scripts` from the monorepo root — passed and updated the workspace lockfile; the nested `frontend-2/package-lock.json` was kept aligned with the new MSAL package entry.
- `cd frontend-2 && npx tsc --noEmit` — passed.
- `cd frontend-2 && npm run build` — passed; Next reported the existing multiple-lockfile workspace-root warning.
- Focused frontend lint — direct ESLint remains blocked by the existing `TypeError: expand is not a function` minimatch/brace-expansion mismatch; the known in-process compatibility shim passed with 0 errors and the existing `_rememberMe` warning in `app/lib/api.ts`.
- `cd backend && uv lock --check` — passed (`Resolved 182 packages in 1ms`). The repository root has no `pyproject.toml`, so the root invocation is not applicable.
- `cd backend && uv run ruff check ...` — passed for the changed OIDC/auth files.
- `git diff --check` — passed; Git reported only existing LF-to-CRLF working-copy warnings.
- No live Microsoft calls, Docker restart, credential exposure, commit, or push performed.

## Rollback boundary

Downgrade `e0f1a2b3c4d5` only after an approved cleanup or retention decision
for `microsoftoidcconfiguration` rows and the `is_admin` bootstrap field.
Revert the public OIDC API/service/frontend/helper/dependency/docs changes as
one work unit. Keep `externalidentity`, local email/password cookies,
`microsoft_graph.py`, delegated Graph state/PKCE/token handling, integration
storage, MCP endpoints, and MCP migrations intact. If rollback is required,
the former confidential OIDC routes remain unavailable rather than silently
reintroducing a client-secret requirement.
