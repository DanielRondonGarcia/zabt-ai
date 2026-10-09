# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Read-only MCP server for accessible groups and meeting context.

The installed official MCP SDK is v2, where the former FastMCP class is named
``MCPServer``. It still provides the same high-level tool registration and
Streamable HTTP application surface.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Annotated
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.applications import Starlette

from app.core.config import settings
from app.core.logging import get_logger
from app.services.group import group_service
from app.services.mcp_tokens import MCPTokenService, mcp_token_service
from app.services.meeting import meeting_service
from app.services.retrieval import retrieval_service


logger = get_logger(__name__)

MCP_READ_SCOPE = "mcp:read"
MCP_STATELESS_HTTP = True
MCP_TOOL_NAMES = (
    "list_groups",
    "list_group_meetings",
    "search_group_meetings",
    "get_meeting_context",
)
_MAX_SEARCH_TEXT_CHARS = 4000
_STRUCTURED_OUTPUT_MAX_DEPTH = 4
_STRUCTURED_OUTPUT_MAX_KEYS = 24
_STRUCTURED_OUTPUT_MAX_ITEMS = 64
_STRUCTURED_OUTPUT_MAX_CHARS = 4000
_MCP_RESPONSE_MAX_CHARS = settings.MCP_RESPONSE_MAX_CHARS
_MCP_RESPONSE_SAFETY_MARGIN = 512
_MCP_METADATA_TEXT_MAX_CHARS = 200
_MCP_DESCRIPTION_MAX_CHARS = 500
_MCP_SOURCE_TEXT_MAX_CHARS = 1200
_STRUCTURED_OMITTED = object()
_SENSITIVE_KEY_SEGMENTS = frozenset(
    {
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "provider",
        "secret",
        "token",
    }
)
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "clientsecret",
        "credential",
        "credentials",
        "encryption_key",
        "encryptionkey",
        "password",
        "private_key",
        "privatekey",
        "provider",
        "refresh_token",
        "refreshtoken",
        "secret",
        "signing_key",
        "signingkey",
        "token",
    }
)
_SENSITIVE_KEY_COMPOUNDS = frozenset(
    {
        "access_token",
        "api_key",
        "client_secret",
        "encryption_key",
        "private_key",
        "refresh_token",
        "signing_key",
    }
)
_SENSITIVE_KEY_COMPACT_COMPOUNDS = (
    "accesstoken",
    "apikey",
    "clientsecret",
    "encryptionkey",
    "privatekey",
    "refreshtoken",
    "signingkey",
)
_LOCAL_MCP_HOSTS = (
    "localhost",
    "localhost:8000",
    "127.0.0.1",
    "127.0.0.1:8000",
    "[::1]",
    "[::1]:8000",
    "testserver",
)
_LOCAL_MCP_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://testserver",
)
_MCP_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class MCPGroup(BaseModel):
    id: int
    name: str
    description: str | None
    meeting_count: int


class MCPGroupReference(BaseModel):
    id: int
    name: str
    description: str | None


class MCPMeetingMetadata(BaseModel):
    id: int
    title: str
    description: str | None
    duration_seconds: int | None
    group_id: int
    created_at: datetime
    status: str
    sub_status: str | None
    summary: str | None


class MCPSearchSource(BaseModel):
    meeting_id: int | None
    kind: str | None
    chunk_index: int | None
    score: float | None
    text: str


class MCPSearchResponse(BaseModel):
    group_id: int
    results: list[MCPSearchSource]


class MCPMeetingContext(BaseModel):
    id: int
    title: str
    group: MCPGroupReference | None
    status: str
    summary: str | None
    action_items: str | None
    structured_output: Any | None
    transcript_context: str


class _StructuredOutputBudget:
    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.characters = 0
        self.items = 0

    def claim_characters(self, count: int) -> bool:
        if count < 0 or self.characters + count > self.max_chars:
            return False
        self.characters += count
        return True

    def claim_item(self) -> bool:
        if self.items >= _STRUCTURED_OUTPUT_MAX_ITEMS:
            return False
        self.items += 1
        return True


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return True
    camel_case_normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", camel_case_normalized.casefold()).strip("_")
    if not normalized:
        return True
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    segments = normalized.split("_")
    if _SENSITIVE_KEY_SEGMENTS.intersection(segments):
        return True
    if any(
        "_".join(segments[index : index + 2]) in _SENSITIVE_KEY_COMPOUNDS
        for index in range(len(segments) - 1)
    ):
        return True
    compact_normalized = normalized.replace("_", "")
    return any(
        compound in compact_normalized
        for compound in _SENSITIVE_KEY_COMPACT_COMPOUNDS
    )


def _serialize_structured_value(
    value: Any,
    *,
    depth: int,
    budget: _StructuredOutputBudget,
) -> Any:
    """Recursively keep only bounded JSON primitives and safe keys."""

    if depth > _STRUCTURED_OUTPUT_MAX_DEPTH:
        return {"_omitted": "maximum depth"}

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        remaining = budget.max_chars - budget.characters
        if remaining <= 0:
            return _STRUCTURED_OMITTED
        if len(value) > remaining:
            return _STRUCTURED_OMITTED
        bounded = value
        if not budget.claim_characters(len(bounded)):
            return _STRUCTURED_OMITTED
        return bounded

    if isinstance(value, Mapping):
        safe_object: dict[str, Any] = {}
        for key, nested in value.items():
            if len(safe_object) >= _STRUCTURED_OUTPUT_MAX_KEYS:
                break
            if _is_sensitive_key(key) or not budget.claim_item():
                continue
            safe_nested = _serialize_structured_value(
                nested,
                depth=depth + 1,
                budget=budget,
            )
            if safe_nested is not _STRUCTURED_OMITTED:
                safe_object[key] = safe_nested
        return safe_object

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        safe_items: list[Any] = []
        for nested in value:
            if len(safe_items) >= _STRUCTURED_OUTPUT_MAX_KEYS or not budget.claim_item():
                break
            safe_nested = _serialize_structured_value(
                nested,
                depth=depth + 1,
                budget=budget,
            )
            if safe_nested is not _STRUCTURED_OMITTED:
                safe_items.append(safe_nested)
        return safe_items

    # Do not call str(value): arbitrary object representations can contain
    # provider payloads, file paths, or credential material.
    return _STRUCTURED_OMITTED


def _safe_structured_output(value: Any, max_chars: int) -> Any | None:
    if value is None:
        return None

    budget = _StructuredOutputBudget(
        min(max_chars, _STRUCTURED_OUTPUT_MAX_CHARS)
    )
    serialized = _serialize_structured_value(value, depth=0, budget=budget)
    if serialized is _STRUCTURED_OMITTED:
        return {"_omitted": "unsupported structured output"}

    try:
        encoded = json.dumps(
            serialized,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return {"_omitted": "unsupported structured output"}

    if len(encoded) > budget.max_chars:
        return {"_omitted": "structured output exceeded safety budget"}
    return serialized


def _csv_entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _public_mcp_host() -> str | None:
    try:
        parsed = urlsplit(settings.MCP_PUBLIC_URL)
        hostname = parsed.hostname
        if not hostname:
            return None
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        return f"{hostname}:{parsed.port}" if parsed.port else hostname
    except ValueError:
        return None


def _mcp_transport_security() -> TransportSecuritySettings:
    """Return an explicit host/origin policy for the mounted MCP transport."""

    configured_hosts = _csv_entries(settings.MCP_ALLOWED_HOSTS)
    configured_origins = _csv_entries(settings.MCP_ALLOWED_ORIGINS)

    if configured_hosts:
        allowed_hosts = configured_hosts
    elif settings.AUTH_ENVIRONMENT == "production":
        allowed_hosts = [_public_mcp_host() or "localhost"]
    else:
        allowed_hosts = list(_LOCAL_MCP_HOSTS)

    if configured_origins:
        allowed_origins = configured_origins
    elif settings.AUTH_ENVIRONMENT == "production":
        allowed_origins = _csv_entries(settings.AUTH_ALLOWED_ORIGINS) or [settings.APP_URL]
    else:
        allowed_origins = list(_LOCAL_MCP_ORIGINS)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


class MCPTokenVerifier:
    """Resolve user-managed opaque tokens for the SDK bearer middleware."""

    def __init__(self, token_service: MCPTokenService):
        self.token_service = token_service

    async def verify_token(self, token: str) -> AccessToken | None:
        record = self.token_service.resolve_bearer_token(token)
        if record is None or record.id is None:
            return None

        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        owner_id = int(record.user_id)
        return AccessToken(
            token=token,
            client_id=f"mcp-token-{record.id}",
            scopes=[MCP_READ_SCOPE],
            expires_at=int(expires_at.timestamp()),
            subject=str(owner_id),
            claims={
                "user_id": owner_id,
                "token_id": record.id,
                "scope": [MCP_READ_SCOPE],
            },
        )


def _mcp_auth_settings() -> AuthSettings:
    """Build SDK resource-server settings without enabling an OAuth issuer."""

    public_url = settings.MCP_PUBLIC_URL.rstrip("/")
    return AuthSettings(
        # The SDK requires an issuer URL for protected-resource metadata even
        # when a custom verifier is used. OAuth authorization-server metadata
        # is intentionally not implemented in this first bearer-token version.
        issuer_url=public_url,
        # Leave protected-resource metadata disabled until the OAuth server is
        # implemented; clients that already provide a bearer header do not
        # need discovery.
        resource_server_url=None,
        required_scopes=[MCP_READ_SCOPE],
        validate_token_resource=False,
    )


mcp_server = MCPServer(
    name="zabt-readonly",
    title="Zabt read-only meeting context",
    description="Read-only access to accessible groups and indexed meeting context.",
    instructions="Use only the provided read-only tools. Never infer or request another user_id.",
    version=settings.VERSION,
    auth=_mcp_auth_settings(),
    token_verifier=MCPTokenVerifier(mcp_token_service),
)


def _owner_id_from_access_token() -> int:
    access_token = get_access_token()
    if access_token is None:
        raise ToolError("Authentication context unavailable")

    subject = access_token.subject
    if subject is None:
        raise ToolError("Authentication context unavailable")
    try:
        return int(subject)
    except (TypeError, ValueError) as exc:
        raise ToolError("Authentication context unavailable") from exc


def _safe_domain_error(exc: HTTPException) -> ToolError:
    if exc.status_code == 503:
        return ToolError("Retrieval is temporarily unavailable. Try again later.")
    if exc.status_code in {403, 404}:
        return ToolError("The requested resource is not available to this token.")
    return ToolError("The read request could not be completed.")


def _unexpected_tool_error(
    operation: str,
    exc: Exception,
    *,
    user_id: int,
    group_id: int | None = None,
    meeting_id: int | None = None,
) -> ToolError:
    """Log only safe identifiers/type and return a generic client error."""

    logger.warning(
        "MCP tool failure operation=%s user_id=%s group_id=%s meeting_id=%s exception_type=%s",
        operation,
        user_id,
        group_id,
        meeting_id,
        type(exc).__name__,
    )
    return ToolError("The read request could not be completed.")


def _bounded_text(value: str | None, max_chars: int) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    marker = "\n[truncated]"
    if max_chars <= len(marker):
        return value[:max_chars]
    return value[: max(0, max_chars - len(marker))] + marker


def _model_json_size(value: Any) -> int:
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        elif isinstance(value, list):
            value = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _MCP_RESPONSE_MAX_CHARS + 1


def _fit_model_collection(items: list[BaseModel]) -> list[BaseModel]:
    """Keep collection tools below the shared MCP response budget."""

    collection_budget = max(
        0,
        _MCP_RESPONSE_MAX_CHARS - _MCP_RESPONSE_SAFETY_MARGIN,
    )
    fitted: list[BaseModel] = []
    for item in items:
        candidate = [*fitted, item]
        if _model_json_size(candidate) > collection_budget:
            break
        fitted.append(item)
    return fitted


def _fit_meeting_context_to_budget(
    context: MCPMeetingContext,
) -> MCPMeetingContext:
    """Trim lower-priority text while preserving a hard response-size bound."""

    response_budget = max(
        0,
        _MCP_RESPONSE_MAX_CHARS - _MCP_RESPONSE_SAFETY_MARGIN,
    )
    if _model_json_size(context) <= response_budget:
        return context

    candidate = context
    text_fields = ["transcript_context", "summary", "action_items", "title"]
    for _ in range(32):
        if _model_json_size(candidate) <= response_budget:
            return candidate

        field_name = max(
            text_fields,
            key=lambda name: len(getattr(candidate, name) or ""),
        )
        current = getattr(candidate, field_name) or ""
        if not current:
            text_fields.remove(field_name)
            if not text_fields:
                break
            continue
        candidate = candidate.model_copy(
            update={
                field_name: _bounded_text(current, max(0, len(current) // 2)) or ""
            }
        )

    if candidate.structured_output is not None:
        candidate = candidate.model_copy(
            update={"structured_output": {"_omitted": "response budget"}}
        )

    if _model_json_size(candidate) <= response_budget:
        return candidate

    group = candidate.group
    if group is not None:
        group = group.model_copy(update={"name": "", "description": None})
    return candidate.model_copy(
        update={
            "title": "",
            "summary": None,
            "action_items": None,
            "transcript_context": "",
            "structured_output": None,
            "group": group,
        }
    )


def _row_value(row: Any, name: str) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping[name]
    return getattr(row, name)


@mcp_server.tool(
    name="list_groups",
    description="List the authenticated user's groups and meeting counts.",
    annotations=_MCP_READ_ANNOTATIONS,
)
async def list_groups() -> list[MCPGroup]:
    owner_id = _owner_id_from_access_token()
    try:
        groups = group_service.list_for_user_with_meeting_counts(owner_id)
        return _fit_model_collection(
            [
                MCPGroup(
                    id=group.id,
                    name=_bounded_text(
                        group.name,
                        _MCP_METADATA_TEXT_MAX_CHARS,
                    )
                    or "",
                    description=_bounded_text(
                        group.description,
                        _MCP_DESCRIPTION_MAX_CHARS,
                    ),
                    meeting_count=meeting_count,
                )
                for group, meeting_count in groups
            ]
        )
    except ToolError:
        raise
    except Exception as exc:
        raise _unexpected_tool_error(
            "list_groups",
            exc,
            user_id=owner_id,
        ) from None


@mcp_server.tool(
    name="list_group_meetings",
    description="List bounded meeting metadata for one accessible group.",
    annotations=_MCP_READ_ANNOTATIONS,
)
async def list_group_meetings(
    group_id: int,
    limit: Annotated[int, Field(ge=1, le=100)] = 100,
) -> list[MCPMeetingMetadata]:
    owner_id = _owner_id_from_access_token()
    try:
        group_service.get_accessible(group_id, owner_id)
        rows = meeting_service.get_group_meetings(group_id, owner_id, limit)
        return _fit_model_collection(
            [
                MCPMeetingMetadata(
                    id=_row_value(row, "id"),
                    title=_bounded_text(
                        _row_value(row, "title"),
                        _MCP_METADATA_TEXT_MAX_CHARS,
                    )
                    or "",
                    description=_bounded_text(
                        _row_value(row, "description"),
                        _MCP_DESCRIPTION_MAX_CHARS,
                    ),
                    duration_seconds=_row_value(row, "duration_seconds"),
                    group_id=_row_value(row, "group_id"),
                    created_at=_row_value(row, "created_at"),
                    status=_bounded_text(
                        _row_value(row, "status"),
                        _MCP_METADATA_TEXT_MAX_CHARS,
                    )
                    or "",
                    sub_status=_bounded_text(
                        _row_value(row, "sub_status"),
                        _MCP_METADATA_TEXT_MAX_CHARS,
                    ),
                    summary=_bounded_text(_row_value(row, "summary_text"), 300),
                )
                for row in rows
            ]
        )
    except ToolError:
        raise
    except HTTPException as exc:
        raise _safe_domain_error(exc) from exc
    except Exception as exc:
        raise _unexpected_tool_error(
            "list_group_meetings",
            exc,
            user_id=owner_id,
            group_id=group_id,
        ) from None


@mcp_server.tool(
    name="search_group_meetings",
    description="Search indexed meeting chunks inside one accessible group.",
    annotations=_MCP_READ_ANNOTATIONS,
)
async def search_group_meetings(
    group_id: int,
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    limit: Annotated[int, Field(ge=1, le=20)] = 8,
    kinds: list[str] | None = None,
) -> MCPSearchResponse:
    owner_id = _owner_id_from_access_token()
    if not query.strip():
        raise ToolError("query cannot be empty")

    try:
        # Keep the ownership check at the tool boundary as well as inside the
        # retrieval service, so no provider call can precede authorization.
        group_service.get_accessible(group_id, owner_id)
        results = retrieval_service.search(
            group_id=group_id,
            user_id=owner_id,
            query=query,
            limit=limit,
            kinds=kinds,
        )
        return MCPSearchResponse(
            group_id=group_id,
            results=_fit_model_collection(
                [
                    MCPSearchSource(
                        meeting_id=item.get("meeting_id"),
                        kind=_bounded_text(
                            item.get("kind"),
                            _MCP_METADATA_TEXT_MAX_CHARS,
                        ),
                        chunk_index=item.get("chunk_index"),
                        score=item.get("score"),
                        text=_bounded_text(
                            str(item.get("text") or ""),
                            _MCP_SOURCE_TEXT_MAX_CHARS,
                        )
                        or "",
                    )
                    for item in results
                ]
            ),
        )
    except ToolError:
        raise
    except HTTPException as exc:
        raise _safe_domain_error(exc) from exc
    except Exception as exc:
        raise _unexpected_tool_error(
            "search_group_meetings",
            exc,
            user_id=owner_id,
            group_id=group_id,
        ) from None


@mcp_server.tool(
    name="get_meeting_context",
    description="Read an accessible meeting summary and bounded transcript context.",
    annotations=_MCP_READ_ANNOTATIONS,
)
async def get_meeting_context(
    meeting_id: int,
    max_chars: Annotated[int, Field(ge=100, le=12000)] = 6000,
) -> MCPMeetingContext:
    owner_id = _owner_id_from_access_token()
    try:
        # The service authorizes the owner or the meeting's accessible group.
        meeting = meeting_service.get_meeting_for_access(meeting_id, owner_id)
        if meeting is None:
            raise ToolError("The requested meeting is not available to this token.")

        group = None
        if meeting.group_id is not None:
            group_record = group_service.get_accessible(meeting.group_id, owner_id)
            group = MCPGroupReference(
                id=group_record.id,
                name=_bounded_text(
                    group_record.name,
                    _MCP_METADATA_TEXT_MAX_CHARS,
                )
                or "",
                description=_bounded_text(
                    group_record.description,
                    _MCP_DESCRIPTION_MAX_CHARS,
                ),
            )

        transcript = meeting.transcript_text
        if transcript is None:
            transcript = "\n".join(
                segment.text for segment in (meeting.segments or []) if segment.text
            )

        return _fit_meeting_context_to_budget(
            MCPMeetingContext(
                id=meeting.id,
                title=_bounded_text(
                    meeting.title,
                    _MCP_METADATA_TEXT_MAX_CHARS,
                )
                or "",
                group=group,
                status=_bounded_text(
                    meeting.status,
                    _MCP_METADATA_TEXT_MAX_CHARS,
                )
                or "",
                summary=_bounded_text(meeting.summary_text, max_chars),
                action_items=_bounded_text(meeting.action_items_text, max_chars),
                structured_output=_safe_structured_output(
                    meeting.structured_output,
                    max_chars,
                ),
                transcript_context=_bounded_text(transcript, max_chars) or "",
            )
        )
    except ToolError:
        raise
    except HTTPException as exc:
        raise _safe_domain_error(exc) from exc
    except Exception as exc:
        raise _unexpected_tool_error(
            "get_meeting_context",
            exc,
            user_id=owner_id,
            meeting_id=meeting_id,
        ) from None


def create_mcp_http_app() -> Starlette:
    """Create the SDK Streamable HTTP app mounted by the main FastAPI app."""

    return mcp_server.streamable_http_app(
        # The parent app mounts this Starlette app at /api/v1. Keeping the
        # child route explicit avoids a trailing-slash redirect, so the public
        # Streamable HTTP URL is exactly /api/v1/mcp.
        streamable_http_path="/mcp",
        json_response=True,
        # The tools are request-scoped reads and do not require resumable
        # server-to-client state. Stateless mode avoids retaining sessions in
        # the API process and is the safer topology for this mounted surface.
        stateless_http=MCP_STATELESS_HTTP,
        transport_security=_mcp_transport_security(),
        host="0.0.0.0",
    )


mcp_http_app = create_mcp_http_app()
