# Read-only MCP access to groups and meetings

## Scope

This work unit adds the first remote MCP surface for Zabt. It exposes only
owner-scoped reads over Streamable HTTP at `/api/v1/mcp` and does not expose
raw SQL, Qdrant, storage credentials, conversation writes, group writes, or
meeting mutations.

The backend currently uses the official Python MCP SDK `mcp` v2 package. In
that SDK the high-level server formerly called FastMCP is named `MCPServer`.
The server is mounted into the existing FastAPI process and participates in the
existing application lifespan.

## Security model

- A user creates an opaque `zabt_mcp_…` bearer token from Settings.
- The database stores only the SHA-256 digest, a short display prefix, owner,
  label, timestamps, expiry, and revocation state.
- The raw token is returned only from the create response and is held only in
  the in-memory Settings component afterward. The create response is marked
  `Cache-Control: no-store`; the token is not returned by list or status
  endpoints and is not written to browser storage.
- External clients send `Authorization: Bearer <token>` to the MCP endpoint.
- The SDK token verifier resolves the digest, rejects inactive owners,
  revocation, and expiry, then exposes the owner id as the access-token
  subject/claims. Tool arguments never accept a `user_id`.
- Every tool requires `mcp:read`. Tool annotations mark the surface as
  read-only, idempotent, and non-destructive.
- Existing group ownership and retrieval authorization remain authoritative.
  Search calls `retrieval_service.search(group_id, user_id, query, limit,
  kinds)`, which performs the provider and Qdrant isolation internally.
- Meeting context uses an owner-filtered lookup before loading the full meeting
  row or transcript segments. A foreign meeting is rejected without reading
  its heavy context.
- Summary, action items, group descriptions, transcript context, and structured
  output are bounded. Structured output is recursively limited by depth,
  keys/items, and total JSON characters; unsupported values are omitted. Keys
  matching provider, private/signing/encryption keys, token, secret, password,
  credential, authorization, API key, client secret, or related normalized
  patterns are omitted case-insensitively, including qualified variants such as
  `my_private_key`, `x_signing_key_value`, and `my-encryption-key`. Domain keys
  such as `key_questions` and `question_key` remain allowed.
- Every MCP tool response is capped by `MCP_RESPONSE_MAX_CHARS` (12,000 by
  default), including collection tools. Titles, names, descriptions, statuses,
  source kinds, and search text are bounded before the shared response cap.
- Unexpected tool failures return one generic safe MCP error. Internal logs
  contain only the operation, user/group/meeting identifiers, and exception
  type; they do not include exception text, provider bodies, transcript text,
  tokens, or stack traces. Retrieval-unavailable responses retain safe 503
  tool-error semantics.
- The Microsoft/OIDC token is not an MCP token and cannot be used in its
  place.

### Streamable HTTP topology

The SDK `TransportSecuritySettings` is enabled explicitly. Hosts and origins
come from the comma-separated `MCP_ALLOWED_HOSTS` and
`MCP_ALLOWED_ORIGINS` settings. Development uses exact localhost defaults for
web ports 3000 and 3001, plus the test client; production rejects wildcard
entries and derives a fail-closed host from `MCP_PUBLIC_URL` when an explicit
host list is not supplied. Production deployments should set both allowlists
to their reverse-proxy/API and frontend origins.

The transport uses `stateless_http=True`: these tools perform request-scoped
reads and do not need resumable server-to-client state. The MCP Starlette
lifespan is still entered by the parent FastAPI lifespan so the transport
manager is initialized and shut down cleanly.

This version intentionally uses user-managed bearer tokens rather than running
an OAuth authorization server. OAuth 2.1 protected-resource metadata,
authorization-code flow, and PKCE can be added later without changing the
read-only tool contracts or the token table boundary.

## Available tools

| Tool | Inputs | Result |
| --- | --- | --- |
| `list_groups` | none | Owner groups with id, name, description, and meeting count |
| `list_group_meetings` | `group_id`, bounded `limit` (default 100) | Bounded meeting metadata, including `group_id` |
| `search_group_meetings` | `group_id`, `query`, bounded `limit` (default 8), optional `kinds` | Group id and bounded sources with meeting id, kind, chunk index, score, and text |
| `get_meeting_context` | `meeting_id`, bounded `max_chars` (default 6000) | Title, group, status, summary, action items, structured output, and bounded transcript context |

Authorization is checked before group reads, meeting reads, or retrieval
provider calls. Missing, invalid, expired, or revoked bearer tokens receive
HTTP 401. Authorization and retrieval failures are returned as safe MCP tool
errors; provider details and credentials are not included. Host/origin policy
rejects invalid transport requests before MCP processing.

## Token lifecycle

1. Open **Settings → Read-only MCP access**.
2. Enter a label and choose an expiry from 1–365 days.
3. Copy the raw token immediately. Zabt will not show it again after the page
   is reloaded or the in-memory one-time panel is cleared.
4. Configure the external client with the endpoint and bearer header.
5. Review token prefix, expiry, last use, and revocation state in Settings.
6. Revoke a token when it is no longer needed or may have been exposed.

## External client configuration

Use the configuration format supported by the client. Define `ZABT_MCP_TOKEN` in
the external client's environment with the token copied during creation; never
commit a real token:

```json
{
  "mcpServers": {
    "zabt": {
      "type": "streamable-http",
      "url": "https://api.example.com/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${ZABT_MCP_TOKEN}"
      }
    }
  }
}
```

If the client has a field named **Bearer token environment variable**, enter only
`ZABT_MCP_TOKEN`, not the raw token or `Bearer ...`. Choose Streamable HTTP, not
STDIO. Cursor, Claude Desktop, and similar clients may name the same fields
slightly differently, but the required HTTP behavior is the same: POST
Streamable HTTP JSON-RPC requests to `/api/v1/mcp` with the bearer header. After
saving, reconnect or reopen the chat if the client does not discover the tools.
Do not paste the token into a repository, issue, screenshot, log, or support
message.

## Verification

Focused local checks:

```bash
cd backend
uv run pytest tests/unit/test_mcp_tokens.py tests/unit/test_mcp_server.py tests/unit/test_mcp_transport.py -q --confcutdir=tests/unit
uv run python -m compileall -q app
uv run alembic upgrade c8d9e0f1a2b3:head --sql
uv run alembic heads
uv lock --check

cd ../frontend-2
npx tsc --noEmit
npm run build
npm run lint -- app/components/McpTokenManager.tsx app/'(dashboard)'/settings/page.tsx app/lib/api.ts

cd ..
git diff --check
```

The transport smoke test is in-process only. It exercises initialize,
`tools/list`, and `tools/call` with a temporary SQLite-backed token and never
makes a live external MCP call. After rebuilding the local API image, a
temporary owner token was also exercised against the real local
`http://127.0.0.1:8000/api/v1/mcp` endpoint: initialize, tools/list, and
list_groups returned 200 with four tools; search_group_meetings for group 2
and get_meeting_context for meeting 602 also returned 200. The token was
revoked immediately after the smoke tests and its raw value was not printed.

## Rollback boundary

The MCP work unit can be removed without reverting unrelated authentication,
groups, or meeting changes by:

1. Removing `backend/app/mcp_server.py` and the MCP endpoint/service/model
   additions.
2. Removing the MCP import/router and mount from `backend/app/api/api.py` and
   `backend/app/main.py`.
3. Removing `MCP_PUBLIC_URL`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`, and
   `MCP_RESPONSE_MAX_CHARS` from `backend/app/core/config.py`, the MCP
   retrieval logging correction, and `MCPToken` registration from
   `backend/app/models/__init__.py` and `backend/alembic/env.py`.
4. Downgrading the `d9e0f1a2b3c4` migration to remove `mcptoken`.
5. Removing the MCP dependency/lock entries, focused tests, Settings component,
   API helpers, and this task document.

That rollback removes only the MCP endpoint, token management, and Settings
surface. It does not remove local JWT/OIDC authentication or group/retrieval
authorization.

## Delivery

- `a8ec834 feat(mcp): expose owner-scoped read-only group tools`
- `18344dc feat(mcp): add user token management UI`
