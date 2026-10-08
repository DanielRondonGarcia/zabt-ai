# System superuser and global authentication mode

## Objective

Provision one deployment-owned system superuser and let only that account
manage the instance-wide regular-user sign-in mode and public Microsoft OIDC
configuration. Keep Graph delegated OAuth independent.

## Policy

- `User.is_superuser` is the sole authority for managing global authentication
  settings. Legacy `is_admin` remains for compatibility but grants no OIDC or
  authentication-mode privileges.
- Ordinary registration and local login never promote a user. The minimum user
  ID, first registration, and first OIDC configuration save are not privileged.
- Regular-user sign-in mode is `local` or `microsoft_oidc`. The system
  superuser always has a separate local-only web login endpoint at
  `POST /api/v1/auth/superuser/login`, regardless of the selected mode.
- Public OIDC settings remain database-backed and contain no client secret.
  Graph delegated OAuth continues to use its existing deployment-managed
  credentials and token storage.

## First-install bootstrap

Before the first `docker compose up`, provide these values through the
secret-managed `.env` or deployment secret manager:

```dotenv
BOOTSTRAP_SUPERUSER_EMAIL=<operator email>
BOOTSTRAP_SUPERUSER_PASSWORD=<unique secret password>
```

Do not commit real credentials or use usable defaults. The API startup sequence
runs Alembic migrations, provisions the configured account, then starts Uvicorn.
The variables are passed only to the API service, not Celery worker or beat.
Provisioning validates the values and hashes the password when creating a new
account. If the configured email already belongs to a local account, it verifies
the supplied bootstrap password against that account's existing password hash
before promotion; a missing hash or mismatch fails startup with a generic safe
message. Promotion never replaces the existing password hash. Later restarts do
not reset the password or create another superuser. A missing bootstrap
configuration is fatal only while no superuser exists. A configured email that
differs from the existing system superuser fails startup with a safe message.

## Runtime behavior

| Mode | Ordinary local login/registration | Microsoft OIDC exchange | System superuser |
|---|---|---|---|
| `local` | Enabled | Rejected | Dedicated local web login remains available |
| `microsoft_oidc` | Rejected | Enabled for browser and native clients | Dedicated local web login remains available |

Linking an existing account to Microsoft remains available while local mode is
active. Switching to OIDC requires the public Microsoft configuration to be
complete and enabled and every active non-superuser account to have a linked
Microsoft identity, regardless of whether the account has a local password.
Email addresses do not establish identity and the system never auto-links an
account. The API returns a safe `409` with the number of accounts that still
need linking. Passwordless unlinked accounts require identity reconciliation or
deactivation by the system superuser before switching; they cannot use local
sign-in to link Microsoft. Switching back to local mode does not require
OIDC readiness. Disabling OIDC while it is the active sign-in mode is rejected;
switch back to local first. Changing the Microsoft client or tenant also
requires switching back to local first so existing external identities remain
valid.

`GET /api/v1/auth/mode` is public and returns the selected mode plus OIDC
configuration readiness. Superusers use `GET` and `PUT` on
`/api/v1/auth/mode/config` to inspect readiness and change the global mode; the
configuration view also reports the number of active accounts that still need
linking and the number of those accounts without a local password. Microsoft
OIDC configuration `GET` and `PUT` are also restricted
to the system superuser; legacy `is_admin` does not grant read or write access.
The exchange accepts `client: "web"` or `client: "mobile"`; an
omitted client defaults to `web` for compatibility. Browser clients receive
HttpOnly cookies; mobile clients receive bearer and refresh tokens in the
response body. The client value never bypasses the global mode check. Existing
login sessions are not revoked by a mode change. Changing the mode governs new
login and registration attempts; existing sessions remain usable while their
access and refresh credentials remain valid, until normal expiration or another
revocation event.

Before Microsoft login or account linking, clients call
`POST /api/v1/auth/microsoft/oidc/challenge` with `purpose: "login"|"link"` and
`client: "web"|"mobile"`. The API returns `{challenge_id, nonce}` and stores a
600-second one-time transaction in Redis. Login challenges are allowed only in
Microsoft OIDC mode and store no user ID. Link challenges require an active Zabt
session, bind the current user ID, and remain available in local mode for
pre-linking; in OIDC mode they are issued only to users already linked to
Microsoft. Exchange and link requests must include the matching `challenge_id`
and client. The API atomically consumes the transaction, checks purpose, client,
and owner before validating the ID token, then compares the signed token nonce
to the server-issued nonce. Replays and cross-purpose/client/user reuse fail.
Web MSAL Browser `loginPopup` uses the authorization-code flow with PKCE; MSAL
manages the code verifier and token exchange. Mobile AuthSession also uses
authorization code with PKCE. Both clients send the server-issued nonce in the
authorization request. The backend challenge binds that nonce to the one-time
transaction, then validates the signed ID token's nonce against the stored value;
this nonce binding is separate from PKCE.

## Frontend and mobile behavior

- The web and mobile login screens read the public `/auth/mode` response. In
  `local` mode they offer email/password and registration only; in
  `microsoft_oidc` mode they hide local credentials and registration and offer
  Microsoft sign-in only when public OIDC readiness is true. A mode/status
  failure never silently falls back to local login.
- The regular web login always links to `/superuser/login`. That page uses the
  dedicated local superuser endpoint and redirects to Integrations after
  success, regardless of the regular-user mode.
- Local registration pages explain when registration is disabled by OIDC mode.
  API-side mode enforcement remains authoritative.
- Integrations exposes the authentication-mode controls only to the system
  superuser. The Microsoft public-client configuration card is separate from
  delegated Graph readiness and credentials. Ordinary users in local mode can
  still link their Microsoft identity from Integrations before the mode is
  switched.
- Native Microsoft sign-in uses authorization-code flow with PKCE, requests
  `openid profile email`, exchanges the authorization code using its PKCE
  verifier, then sends the Microsoft ID token and server challenge ID to the Zabt
  exchange endpoint. Only Zabt access and refresh tokens are persisted through the
  existing mobile SecureStore path; Microsoft access/refresh tokens and any
  client secret are not stored in the app.

## Microsoft Entra redirect registration

- Register the web client redirect on the Entra **Single-page application**
  platform as the actual frontend origin followed by `/login` (for example,
  `https://zabt.example.com/login`). Local development uses
  `http://localhost:3001/login`.
- Register `zabt://auth` as a redirect URI on the Entra **Mobile and desktop
  applications** platform for the native Expo app. The URI is derived from the
  `zabt` Expo scheme and `auth` path; the client ID and tenant are public
  configuration. Do not add a client secret to the mobile app.
- These OIDC redirects do not replace the separate backend callback and
  deployment credentials used for delegated Microsoft Graph Calendar/email.

## Verification

- `cd backend && uv run pytest --confcutdir=tests/unit tests/unit/test_system_superuser.py tests/unit/test_oauth_state.py tests/unit/test_microsoft_graph_auth.py tests/unit/test_microsoft_oidc.py tests/unit/test_microsoft_auth.py tests/unit/test_local_auth.py tests/unit/test_local_auth_router.py tests/unit/test_microsoft_oidc_config.py tests/unit/test_microsoft_oidc_migration.py tests/unit/test_authentication_mode_migration.py tests/unit/test_config_defaults.py app/tests/unit/services/test_auth_coverage.py -q` — passed, **179 tests**, with one existing Pydantic settings deprecation warning.
- `cd backend && uv run python -m compileall -q app` — passed.
- `cd backend && uv run alembic upgrade e0f1a2b3c4d5:head --sql` — passed; generated the `is_superuser` column/index, singleton `local` mode row, and nullable updater foreign key with `ON DELETE SET NULL`.
- `cd backend && uv run alembic upgrade f1a2b3c4d5e6:head --sql` — passed as an empty range because this checkout's new migration is the `f1a2b3c4d5e6` head. The existing OIDC-configuration migration in this checkout is `e0f1a2b3c4d5`.
- `cd backend && uv run alembic heads` — passed; `f1a2b3c4d5e6 (head)`.
- Targeted Ruff checks for the auth endpoints/services, new mode model,
  bootstrap/migration, and focused tests — passed. A broader run including
  `backend/app/models/base.py` reports its pre-existing F821 for the unchanged
  `ExternalIdentity` forward reference at line 143; this work adds only
  `is_superuser` to that file.
- After server-nonce challenge wiring, `npx tsc --noEmit` passed from both `frontend-2` and `zabt-mobile`.
- Before nonce-challenge wiring, from `frontend-2`, `npm run build` — passed, including the production build and
  TypeScript check. Next.js warned that multiple lockfiles exist and selected
  the repository-root `package-lock.json` as the workspace root.
- Focused frontend lint for the changed login, registration, superuser login,
  Integrations, and API files — blocked by the existing minimatch brace-expansion
  incompatibility: `TypeError: expand is not a function` in
  `@eslint/config-array`/`minimatch`.
- From `zabt-mobile`, `npx expo install expo-auth-session expo-crypto` — passed
  against the anonymous public npm registry. Expo installed SDK-compatible
  `expo-auth-session@55.0.18` and aligned `expo-crypto@55.0.19`; AuthSession's
  required `expo-web-browser@55.0.20` dependency is present. No Microsoft calls
  or credentials were used.
- From `zabt-mobile`, `npm test -- --runInBand` — passed, **6 suites and 39
  tests**. The multipart retry test emitted its expected simulated-network
  warning logs.
- From `zabt-mobile`, `npm run lint` — blocked only by existing
  `react/no-unescaped-entities` errors in `app/record.tsx:161` (two apostrophes);
  no lint findings were reported in the changed mobile files.
- Root `git diff --check` — passed; Git emitted only its existing LF-to-CRLF working-copy warnings.
- Independent auth-mode review correction: active non-superuser accounts are counted as unlinked regardless of local password; linked Microsoft identities remain excluded. Superuser mode diagnostics report `unlinked_passwordless_user_count`, and passwordless blockers require system-superuser identity reconciliation or deactivation rather than local sign-in.
- `cd backend && uv run pytest --confcutdir=tests/unit tests/unit/test_local_auth_router.py tests/unit/test_local_auth.py tests/unit/test_microsoft_auth.py tests/unit/test_microsoft_oidc.py tests/unit/test_microsoft_oidc_config.py -q` — passed, **112 tests**, with one existing Pydantic settings deprecation warning.
- `cd backend && uv run python -m compileall -q app` — passed.
- Targeted `uv run ruff check app/services/authentication_configuration.py app/api/v1/endpoints/auth.py tests/unit/test_local_auth_router.py` from `backend` — passed.
- From `frontend-2`, `npx tsc --noEmit` — passed; `npm run build` — passed. Next.js emitted the existing multiple-lockfile workspace-root warning.
- Root `git diff --check` after the correction — passed; Git emitted only existing LF-to-CRLF working-copy warnings. No Docker commands or Microsoft service calls were run.
- Corrective review verification: the focused auth-storage Jest file passed all
  **4 tests**; mobile `npx tsc --noEmit` and frontend-2 `npx tsc --noEmit` passed;
  frontend-2 `npm run build` passed with the existing multiple-lockfile workspace
  root warning.
- `docker compose --profile local config --quiet` passed; normalized config
  confirms the API waits for `db: service_healthy` and `db` uses `pg_isready`.
  The external database overlay config using `COMPOSE_PROFILES=` with
  `docker-compose.cloud.yml` also passed. No containers were started.
- The local database migration was not applied and containers were not restarted
  because bootstrap-superuser credentials were not supplied in this checkout.
  No Microsoft services were called.
- Runtime harness: **N/A**. No mobile device/browser login or live Microsoft OIDC
  call was performed. Docker startup is out of scope; the operator must provide
  bootstrap credentials in local `.env` or the deployment secret manager.

## Rollback boundary

Revert the authentication-mode/superuser model, service, API, bootstrap module,
OIDC Redis challenge and web/mobile nonce wiring, migration, Compose startup
wiring, tests, `.env.example` entries, and this task document together.
Downgrade the new migration only after an approved retention decision for the
system-superuser role and mode state. Keep the legacy `is_admin` column,
external identities, Microsoft OIDC public configuration, local auth, Graph
integration, and unrelated backend services intact.
