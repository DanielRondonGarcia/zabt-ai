# Microsoft Entra OIDC sign-in

## Objective

Add Microsoft Entra ID OpenID Connect sign-in for Zabt while preserving local email/password authentication and the existing delegated Microsoft Graph Calendar/email integration.

## Security decisions

- OIDC application sign-in uses authorization code + PKCE with `openid profile email`; an ID token is validated as an identity assertion and is never used as a Microsoft Graph access token.
- OAuth state is an opaque random value. The server stores its purpose (`oidc_login` or `oidc_link`), nonce, PKCE verifier, safe relative next path, and the explicit-link user id in Redis under a SHA-256 state key with a short TTL.
- Callback state is consumed with Redis `GETDEL`, so expiry, malformed payloads, replay, and CSRF fail closed.
- Discovery is fetched only from the fixed Microsoft authority. Authorization/token URLs are constructed from that authority; metadata endpoints must use HTTPS, the exact `login.microsoftonline.com` host with no userinfo/port/query/fragment, the expected Microsoft paths, and the expected issuer template. JWKS is fetched only from the Microsoft discovery keys path, so the client secret cannot be sent to a metadata-controlled host.
- If a selected JWK includes an `issuer` attribute, it must match the resolved validated tenant issuer or the documented Microsoft multi-tenant issuer template.
- ID-token validation requires an RS256 JWKS signature, expected Microsoft issuer, audience/client id, nonce, subject, tenant, and expiration checks. Concrete tenants are restricted to tenant GUIDs; `common`, `organizations`, and `consumers` require a valid tenant GUID and validate `https://login.microsoftonline.com/<tenant-guid>/v2.0`. Provider response bodies, authorization codes, secrets, access tokens, and raw claims are not logged or persisted.
- External identities store only provider, tenant, subject, email, and timestamps. Anonymous OIDC never links to an existing local email: it returns a static conflict result directing the user to local sign-in and settings. New external-only users are created with `password_hash=None`.
- Existing local accounts can link Microsoft only through the authenticated `/auth/microsoft/link/start` flow. The link callback verifies the initiating active web session, validates the same OIDC token contract, permits a different provider email because the user explicitly authorized the action, and rejects identities already linked to another user.
- OIDC configuration is considered unavailable when client credentials or the redirect URI are missing. Production settings require a non-local HTTPS redirect URI, and production Compose files require the redirect variable without a localhost fallback; the local-development callback is explicit only in `.env.example`.
- OIDC and Graph use separate callback settings and remain separate product capabilities. Graph tokens remain in the existing encrypted integration table.

## Affected files

- `backend/app/models/external_identity.py` — external provider identity model and indexes.
- `backend/app/models/base.py`, `backend/app/models/__init__.py`, `backend/alembic/env.py` — model relationship, re-export, and migration registration.
- `backend/alembic/versions/c8d9e0f1a2b3_add_external_identities.py` — reversible external identity schema with user-delete cascade.
- `backend/app/services/oauth_state.py` — Redis-backed one-time state, nonce, PKCE verifier, and next-path validation.
- `backend/app/services/microsoft_oidc.py` — Entra discovery, authorization URL, token exchange, ID-token validation, and account linking.
- `backend/app/api/v1/endpoints/auth.py` — OIDC login/link start, callback, status, explicit-session verification, and existing-cookie issuance.
- `backend/app/core/config.py`, `.env.example`, `docker-compose.yml`, `docker-compose.cloud.prod.yml`, `docker-compose.full-cloud.yml` — OIDC settings and environment mapping.
- `frontend-2/app/lib/api.ts`, `frontend-2/app/login/page.tsx` — typed OIDC login/link URL/status helpers, Microsoft login link, and safe callback errors.
- `frontend-2/app/(dashboard)/integrations/page.tsx` — separate Entra configuration/explicit-link action and Graph integration sections.
- `backend/tests/unit/` — focused model, state, OIDC, callback, migration, and endpoint coverage.

## Verification

- `cd backend && $env:AUTH_COOKIE_SECURE='false'; uv run pytest --confcutdir=tests/unit tests/unit/test_microsoft_graph.py app/tests/unit/services/test_microsoft_graph_coverage.py app/tests/unit/services/test_integration_coverage.py tests/unit/test_local_auth.py tests/unit/test_local_auth_router.py tests/unit/test_external_identity.py tests/unit/test_oauth_state.py tests/unit/test_microsoft_oidc.py tests/unit/test_microsoft_auth.py tests/unit/test_microsoft_oidc_config.py tests/unit/test_config_defaults.py -q` — passed, 131 tests and two existing dependency warnings. The `AUTH_COOKIE_SECURE` override makes the isolated local-auth contract deterministic without reading or changing `.env`.
- `cd backend && uv run python -m compileall -q app` — passed.
- `cd backend && uv run alembic upgrade b7c8d9e0f1a2:head --sql` — passed and emitted the new `externalidentity` DDL; `uv run alembic heads` reports `c8d9e0f1a2b3 (head)`.
- `cd frontend-2 && npx tsc --noEmit` — passed.
- `cd frontend-2 && npm run build` — passed; Next reported the existing multiple-lockfile workspace-root warning.
- `cd frontend-2 && npm run lint -- "app/lib/api.ts" "app/login/page.tsx" "app/(dashboard)/integrations/page.tsx"` — blocked before ESLint analysis by the existing `TypeError: expand is not a function` minimatch/brace-expansion dependency mismatch. The known temporary Node preload shim redirected only minimatch 3 to the installed compatible `brace-expansion.expand`; focused lint then passed with 0 errors and one existing `_rememberMe` warning in `app/lib/api.ts`.
- `git diff --check` — passed; Git reported only existing LF-to-CRLF working-copy warnings.
- Hostile discovery/token/JWKS/JWK-issuer tests, tenant-policy tests, production redirect tests, anonymous conflict tests, explicit-link success/conflict/inactive tests, authenticated-session checks, and static redirect-leakage assertions passed in the focused suite.
- No live Microsoft calls, Docker restart, credential exposure, commit, or push performed.

## Rollback boundary

Remove the OIDC route/service/model/migration/settings/UI/docs files listed above and downgrade `c8d9e0f1a2b3`; remove the explicit-link route and static result mappings with the OIDC change. Leave `auth.py` local email/password behavior, `microsoft_graph.py`, `integrations.py`, and the existing Graph integration table/tokens unchanged. Do not downgrade below the migration while external identity rows exist without an approved identity cleanup.

## Second work unit: delegated Microsoft Graph OAuth hardening

### Objective

Harden the existing Microsoft Graph Calendar/email delegated OAuth connection with the shared one-time Redis state service and PKCE, while keeping the existing token encryption, calendar synchronization, and email behavior unchanged after a successful connection.

### Security decisions

- Graph connect state uses the opaque `graph_connect` Redis purpose, binds the current user id server-side, stores `/integrations` as the only next path, and is consumed with atomic `GETDEL`.
- Graph authorization uses S256 PKCE. The stored verifier is sent only to the fixed Microsoft token endpoint; callers that omit PKCE retain the existing client-helper behavior for compatibility.
- The Graph callback never parses a user id from query state and never trusts a caller-controlled redirect. Missing, replayed, wrong-purpose, denied, malformed, and provider-error callbacks use only static `microsoft_error` result codes.
- Callback `code`, `state`, and provider-error query values are accepted as nullable raw strings and bounded manually after routing, preventing FastAPI validation errors from echoing authorization-code input. Oversized and control-character values terminate through static redirects without provider calls.
- Client credentials, Graph redirect URI, and `TOKEN_ENCRYPTION_KEY` must be present before connect or token exchange. Tokens and profile data are validated before encrypted integration upsert; no code, token, secret, or provider response body is logged.
- Graph profile/calendar/email failures expose only static operation/status messages; provider response bodies are never included in `MicrosoftGraphError` strings or logs. Token-storage readiness validates the configured key as a Fernet key rather than checking only nonempty text.
- The status response exposes only `graph_configured` and `token_storage_configured` booleans. The UI disables Graph connect until both are ready and explicitly tells operators to set `TOKEN_ENCRYPTION_KEY`.

### Affected files

- `backend/app/services/oauth_state.py` — `graph_connect` purpose and required owner validation.
- `backend/app/services/microsoft_graph.py` — optional PKCE authorization/token parameters and safe error logging.
- `backend/app/api/v1/endpoints/integrations.py` — user-bound connect/callback, readiness checks, validation, and static redirects.
- `backend/app/api/v1/endpoints/auth.py`, `frontend-2/app/lib/api.ts` — non-secret Graph readiness status fields.
- `frontend-2/app/(dashboard)/integrations/page.tsx`, `frontend-2/app/components/integration-card.tsx` — honest Graph readiness messaging and disabled connect action.
- `backend/tests/unit/test_microsoft_graph_auth.py`, `backend/tests/unit/test_microsoft_graph.py`, `backend/app/tests/unit/services/test_microsoft_graph_coverage.py`, `backend/tests/unit/test_oauth_state.py`, `backend/tests/unit/test_microsoft_auth.py`, `backend/tests/test_integration_service.py` — PKCE/state/callback/status/privacy/Fernet-readiness regressions.

### Verification

- `cd backend && $env:AUTH_COOKIE_SECURE='false'; uv run pytest --confcutdir=tests/unit tests/unit/test_microsoft_graph.py app/tests/unit/services/test_microsoft_graph_coverage.py tests/unit/test_microsoft_graph_auth.py tests/unit/test_oauth_state.py tests/unit/test_microsoft_auth.py tests/unit/test_microsoft_oidc.py tests/unit/test_microsoft_oidc_config.py tests/unit/test_local_auth.py tests/unit/test_local_auth_router.py app/tests/unit/services/test_integration_coverage.py tests/test_integration_service.py tests/unit/test_config_defaults.py -q` — passed, 157 tests and two existing dependency warnings.
- `cd backend && uv run python -m compileall -q app` — passed.
- `cd backend && uv run alembic heads` — passed with `c8d9e0f1a2b3 (head)`; `uv run alembic upgrade b7c8d9e0f1a2:head --sql` passed and emitted no new schema changes.
- `cd frontend-2 && npx tsc --noEmit` — passed.
- `cd frontend-2 && npm run build` — passed; Next reported the existing multiple-lockfile workspace-root warning.
- Focused frontend lint shim — passed with 0 errors and the existing `_rememberMe` warning in `app/lib/api.ts`; direct lint remains blocked by the existing minimatch/brace-expansion mismatch.
- `git diff --check` — passed; Git reported only existing LF-to-CRLF working-copy warnings.
- Hostile oversized callback marker tests, provider-body redaction tests, valid/invalid Fernet readiness tests, and static redirect-leakage assertions passed in the focused suite.
- Calendar preservation check `tests/test_calendar_sync.py tests/test_calendar_sync_nplus1.py` — 3 pre-existing failures remain in `test_upsert_uses_batch_load`, `test_upsert_reduces_queries_for_many_events`, and `test_upsert_creates_new_events_when_not_found`; each calls `CalendarSyncService._upsert_events()` with the stale old signature and omits the current keyword-only `session` and `existing_events` arguments. Calendar behavior was not changed to mask this unrelated test drift.
- No live Microsoft calls, Docker restart, credential exposure, commit, or push performed.

### Rollback boundary

Revert only the `graph_connect` state purpose, Graph client PKCE parameters, delegated Graph endpoint hardening, readiness status fields, Graph UI disable/message changes, tests, and this second-work-unit documentation section. Keep the first-unit OIDC login/link flow, external identity migration, local authentication, and Graph token storage/calendar/email runtime behavior intact.

## Configuration-discovery UI correction

### Evidence

- `frontend-2/app/(dashboard)/integrations/page.tsx` now provides a visible **How to configure Microsoft** dialog trigger in the Entra/Graph configuration area, emphasized while OIDC, Graph, or token-storage status is unavailable.
- The dialog explains that deployment operators configure server `.env` or secret-manager values, never browser-side secrets, and lists `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_OIDC_REDIRECT_URI`, `MICROSOFT_REDIRECT_URI`, and `TOKEN_ENCRYPTION_KEY` without exposing their values.
- The dialog displays API-reported OIDC and Graph callback URLs, shows the local-development OIDC fallback derived from `NEXT_PUBLIC_API_URL` only when the API reports no OIDC URL, and provides non-secret URL copy actions plus a safe link to the official Microsoft Entra app-registration portal.
- **Recheck configuration** calls `getMicrosoftOidcStatus` again and reports loading, success, and error states while preserving the existing Graph Connect disabled behavior when readiness is unknown or incomplete.
- OIDC sign-in and delegated Graph connection remain separate capabilities; OIDC scopes are documented as `openid profile email`, while Graph scopes remain server-controlled.

Secrets and encryption keys remain deployment-managed. The browser receives only non-sensitive status fields and callback URLs; it never receives `MICROSOFT_CLIENT_SECRET` or `TOKEN_ENCRYPTION_KEY` values.

### Verification

- `cd frontend-2 && npx tsc --noEmit` — passed.
- `cd frontend-2 && npm run build` — passed; Next reported the existing multiple-lockfile workspace-root warning.
- Direct focused lint `cd frontend-2 && npm run lint -- "app/(dashboard)/integrations/page.tsx"` — blocked before ESLint analysis by the existing `TypeError: expand is not a function` minimatch/brace-expansion dependency mismatch.
- Known focused lint shim — passed with 0 errors using the temporary preload that redirects only the minimatch 3 `brace-expansion` lookup to the compatible export outside the repository.
- `git diff --check` — passed; Git reported only existing LF-to-CRLF working-copy warnings.
- No credentials were exposed, Docker was not restarted, and no commit or push was performed.

### Rollback boundary

Revert only the configuration-discovery dialog, callback URL copy/link actions, status recheck feedback, related semantic styling, and this documentation section. Keep the existing Entra link action, Graph Connect disabled behavior, backend status contract, and all deployment-managed secret handling unchanged.
