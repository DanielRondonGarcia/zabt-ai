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
