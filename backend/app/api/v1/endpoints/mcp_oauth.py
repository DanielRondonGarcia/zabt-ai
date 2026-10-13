# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""MCP OAuth 2.1 public-client endpoints.

The authorization endpoint creates a short-lived pending request. The public
frontend displays its safe details and binds it to the current Zabt web session
only when the user explicitly approves or denies it.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.api import deps
from app.core.config import settings
from app.models import User
from app.services.mcp_oauth import (
    MCPAuthorizationStateError,
    MCPLinkedGrant,
    MCP_OAUTH_AUTHORIZE_RATE_LIMIT,
    MCP_OAUTH_DCR_RATE_LIMIT,
    MCPOAuthError,
    MCP_READ_SCOPE,
    mcp_oauth_service,
    oauth_endpoint_url,
    oauth_issuer_url,
    mcp_resource_url,
)


router = APIRouter(prefix="/mcp/oauth")
metadata_router = APIRouter()

MCP_OAUTH_DCR_MAX_BODY_BYTES = 16_384
MCP_OAUTH_REQUEST_MAX_BODY_BYTES = 8_192


class DCRRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_name: str = Field(default="MCP client", min_length=1, max_length=100)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    response_types: list[str] = Field(default_factory=lambda: ["code"], max_length=1)
    grant_types: list[str] = Field(
        default_factory=lambda: ["authorization_code"],
        min_length=1,
        max_length=2,
    )
    token_endpoint_auth_method: Literal["none"] = "none"
    application_type: Literal["native", "web"] | None = None
    scope: str = Field(default=MCP_READ_SCOPE, min_length=1, max_length=64)
    code_challenge_methods_supported: list[str] = Field(
        default_factory=lambda: ["S256"],
        min_length=1,
        max_length=1,
    )

    @field_validator("application_type", mode="before")
    @classmethod
    def validate_application_type(cls, value: object) -> object:
        if value is None:
            raise ValueError("application_type must be 'native' or 'web'")
        return value


class AuthorizeApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=32, max_length=128)
    approved: bool


class LinkedGrantResponse(BaseModel):
    grant_id: int
    client_id: str
    client_name: str
    redirect_uris: list[str]
    resource: str
    scope: str
    created_at: Any
    last_used_at: Any
    revoked_at: Any


class AuthorizationRequestDetailsResponse(BaseModel):
    status: Literal["consent_required"]
    authorization_request_id: str
    client_name: str
    redirect_uri: str
    resource: str
    scope: str


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _error_response(
    error: str,
    description: str,
    *,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    authenticate: bool = False,
) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if authenticate:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
        headers=headers,
    )


def _validation_error_response(exc: ValidationError) -> JSONResponse:
    # Pydantic details may contain submitted values; return only a stable
    # protocol error instead of echoing request metadata.
    _ = exc
    return _error_response("invalid_request", "OAuth request metadata is invalid")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _rate_limit(
    request: Request,
    *,
    category: str,
    limit: int,
) -> JSONResponse | None:
    try:
        allowed = mcp_oauth_service.state_store.allow_attempt(
            category,
            _client_ip(request),
            limit=limit,
        )
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not allowed:
        return _error_response(
            "slow_down",
            "Too many OAuth requests",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    return None


async def _read_limited_body(
    request: Request,
    *,
    max_bytes: int,
    too_large_message: str,
) -> tuple[bytes, JSONResponse | None]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            return b"", _error_response("invalid_request", "Invalid request")
        if declared_length < 0:
            return b"", _error_response("invalid_request", "Invalid request")
        if declared_length > max_bytes:
            return b"", _error_response("invalid_request", too_large_message)

    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in request.stream():
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                return b"", _error_response("invalid_request", "Invalid request body")
            total += len(chunk)
            if total > max_bytes:
                return b"", _error_response("invalid_request", too_large_message)
            chunks.append(bytes(chunk))
    except Exception:
        return b"", _error_response("invalid_request", "Invalid request body")
    return b"".join(chunks), None


async def _json_payload(
    request: Request,
    *,
    max_bytes: int,
    too_large_message: str,
) -> tuple[Any | None, JSONResponse | None]:
    body, error = await _read_limited_body(
        request,
        max_bytes=max_bytes,
        too_large_message=too_large_message,
    )
    if error is not None:
        return None, error
    try:
        return json.loads(body), None
    except Exception:
        return None, _error_response("invalid_request", "Invalid JSON request")


def _redirect_contract(
    redirect_uri: str,
    *,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
) -> str:
    params: list[tuple[str, str]] = []
    if code is not None:
        params.append(("code", code))
    if error is not None:
        params.append(("error", error))
    if state is not None:
        params.append(("state", state))
    if not params:
        return redirect_uri
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"


def _linked_grant_response(grant: MCPLinkedGrant) -> LinkedGrantResponse:
    return LinkedGrantResponse(
        grant_id=grant.grant_id,
        client_id=grant.client_id,
        client_name=grant.client_name,
        redirect_uris=list(grant.redirect_uris),
        resource=grant.resource,
        scope=grant.scope,
        created_at=grant.created_at,
        last_used_at=grant.last_used_at,
        revoked_at=grant.revoked_at,
    )


def _consent_url(request_id: str) -> str:
    return f"{settings.APP_URL.rstrip('/')}/mcp/consent?{urlencode({'request_id': request_id})}"


def authorization_server_metadata(response: Response) -> dict[str, Any]:
    _no_store(response)
    return {
        "issuer": oauth_issuer_url(),
        "authorization_endpoint": oauth_endpoint_url("authorize"),
        "token_endpoint": oauth_endpoint_url("token"),
        "registration_endpoint": oauth_endpoint_url("register"),
        "revocation_endpoint": oauth_endpoint_url("revoke"),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [MCP_READ_SCOPE],
    }


def protected_resource_metadata(
    response: Response,
    resource_path: str | None = None,
) -> dict[str, Any]:
    _ = resource_path
    _no_store(response)
    return {
        "resource": mcp_resource_url(),
        "authorization_servers": [oauth_issuer_url()],
        "scopes_supported": [MCP_READ_SCOPE],
        "bearer_methods_supported": ["header"],
    }


# The SDK may request either the path-scoped metadata URL or the standard
# host-level well-known URL depending on whether the app is mounted.
for _metadata_prefix in (router, metadata_router):
    _metadata_prefix.add_api_route(
        "/.well-known/oauth-authorization-server",
        authorization_server_metadata,
        methods=["GET"],
    )
    _metadata_prefix.add_api_route(
        "/.well-known/oauth-authorization-server/{issuer_path:path}",
        authorization_server_metadata,
        methods=["GET"],
    )
    _metadata_prefix.add_api_route(
        "/.well-known/oauth-protected-resource",
        protected_resource_metadata,
        methods=["GET"],
    )
    _metadata_prefix.add_api_route(
        "/.well-known/oauth-protected-resource/{resource_path:path}",
        protected_resource_metadata,
        methods=["GET"],
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_client(
    request: Request,
    response: Response,
) -> JSONResponse:
    _no_store(response)
    limited = _rate_limit(request, category="dcr", limit=MCP_OAUTH_DCR_RATE_LIMIT)
    if limited is not None:
        return limited
    payload, body_error = await _json_payload(
        request,
        max_bytes=MCP_OAUTH_DCR_MAX_BODY_BYTES,
        too_large_message="Registration metadata is too large",
    )
    if body_error is not None:
        return body_error
    try:
        metadata = DCRRequest.model_validate(payload)
    except ValidationError as exc:
        return _validation_error_response(exc)
    try:
        registered = mcp_oauth_service.register_client(
            **metadata.model_dump(exclude={"application_type"})
        )
    except MCPOAuthError as exc:
        return _error_response(exc.code, exc.description)
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if metadata.application_type is not None:
        registered["application_type"] = metadata.application_type
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=registered,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/authorize")
def authorize(
    request: Request,
    response: Response,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    response_type: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    state: str | None = None,
    resource: str | None = None,
    scope: str | None = None,
) -> Response:
    _no_store(response)
    limited = _rate_limit(request, category="authorize", limit=MCP_OAUTH_AUTHORIZE_RATE_LIMIT)
    if limited is not None:
        return limited
    allowed_query = {
        "client_id",
        "redirect_uri",
        "response_type",
        "code_challenge",
        "code_challenge_method",
        "state",
        "resource",
        "scope",
    }
    seen_query: set[str] = set()
    for key, _value in request.query_params.multi_items():
        if key not in allowed_query or key in seen_query:
            return _error_response("invalid_request", "Invalid OAuth parameters")
        seen_query.add(key)
    try:
        transaction = mcp_oauth_service.create_authorization_request(
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            resource=resource,
            scope=scope,
            user_id=None,
        )
    except MCPOAuthError as exc:
        return _error_response(
            exc.code,
            exc.description,
            status_code=(
                status.HTTP_401_UNAUTHORIZED
                if exc.code == "login_required"
                else status.HTTP_400_BAD_REQUEST
            ),
            authenticate=exc.code == "login_required",
        )
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return RedirectResponse(
        url=_consent_url(transaction.request_id),
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get(
    "/authorize/request/{request_id}",
    response_model=AuthorizationRequestDetailsResponse,
)
def authorization_request_details(
    request: Request,
    response: Response,
    request_id: str,
) -> AuthorizationRequestDetailsResponse | JSONResponse:
    """Return safe, non-consuming consent details for the public browser page."""

    _no_store(response)
    limited = _rate_limit(request, category="authorize", limit=MCP_OAUTH_AUTHORIZE_RATE_LIMIT)
    if limited is not None:
        return limited
    try:
        transaction = mcp_oauth_service.state_store.get_request(request_id)
        client_metadata = (
            mcp_oauth_service.client_metadata(transaction.client_id)
            if transaction is not None
            else None
        )
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if transaction is None or client_metadata is None:
        return _error_response(
            "invalid_request",
            "Authorization request is invalid or expired",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return AuthorizationRequestDetailsResponse(
        status="consent_required",
        authorization_request_id=transaction.request_id,
        client_name=client_metadata.get("client_name", "MCP client"),
        redirect_uri=transaction.redirect_uri,
        resource=transaction.resource,
        scope=" ".join(transaction.scopes),
    )


@router.post("/authorize/approve")
async def approve_authorization(
    request: Request,
    response: Response,
    current_user: User = Depends(deps.get_current_web_active_user),
) -> JSONResponse:
    _no_store(response)
    payload, body_error = await _json_payload(
        request,
        max_bytes=MCP_OAUTH_REQUEST_MAX_BODY_BYTES,
        too_large_message="Authorization request is too large",
    )
    if body_error is not None:
        return body_error
    try:
        approval = AuthorizeApprovalRequest.model_validate(payload)
    except ValidationError as exc:
        return _validation_error_response(exc)
    if current_user.id is None:
        return _error_response(
            "login_required",
            "Authenticate with Zabt before approving this MCP client",
            status_code=status.HTTP_401_UNAUTHORIZED,
            authenticate=True,
        )
    try:
        transaction, code = mcp_oauth_service.approve_authorization_request(
            approval.request_id,
            user_id=current_user.id,
            approved=approval.approved,
        )
    except MCPOAuthError as exc:
        return _error_response(exc.code, exc.description)
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not approval.approved:
        redirect_url = _redirect_contract(
            transaction.redirect_uri,
            error="access_denied",
            state=transaction.state,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "denied",
                "redirect_uri": transaction.redirect_uri,
                "redirect_url": redirect_url,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    assert code is not None
    redirect_url = _redirect_contract(
        transaction.redirect_uri,
        code=code,
        state=transaction.state,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "approved",
            "redirect_uri": transaction.redirect_uri,
            "redirect_url": redirect_url,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


async def _form_values(request: Request) -> tuple[dict[str, str], JSONResponse | None]:
    body, body_error = await _read_limited_body(
        request,
        max_bytes=MCP_OAUTH_REQUEST_MAX_BODY_BYTES,
        too_large_message="OAuth request is too large",
    )
    if body_error is not None:
        return {}, body_error

    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    bounded_request = Request(request.scope, receive)
    try:
        form = await bounded_request.form()
    except Exception:
        return {}, _error_response("invalid_request", "Invalid form request")
    values: dict[str, str] = {}
    for key, value in form.multi_items():
        if not isinstance(key, str) or not isinstance(value, str):
            return {}, _error_response("invalid_request", "Invalid form request")
        if key in values:
            return {}, _error_response("invalid_request", "Duplicate OAuth parameter")
        values[key] = value
    return values, None


@router.post("/token")
async def token(request: Request, response: Response) -> JSONResponse:
    _no_store(response)
    values, error = await _form_values(request)
    if error is not None:
        return error
    allowed = {
        "grant_type",
        "code",
        "redirect_uri",
        "client_id",
        "code_verifier",
        "resource",
        "scope",
        "refresh_token",
    }
    if "client_secret" in values:
        return _error_response("invalid_client", "Client secrets are not accepted")
    if set(values) - allowed:
        return _error_response("invalid_request", "Unsupported OAuth parameter")
    grant_type = values.get("grant_type")
    try:
        if grant_type == "authorization_code":
            required = ("code", "redirect_uri", "client_id", "code_verifier", "resource")
            if any(not values.get(field) for field in required):
                return _error_response("invalid_request", "Authorization code fields are required")
            pair = mcp_oauth_service.exchange_authorization_code(
                code=values["code"],
                client_id=values["client_id"],
                redirect_uri=values["redirect_uri"],
                code_verifier=values["code_verifier"],
                resource=values["resource"],
                scope=values.get("scope"),
            )
        elif grant_type == "refresh_token":
            required = ("refresh_token", "client_id", "resource")
            if any(not values.get(field) for field in required):
                return _error_response("invalid_request", "Refresh token fields are required")
            pair = mcp_oauth_service.exchange_refresh_token(
                refresh_token=values["refresh_token"],
                client_id=values["client_id"],
                resource=values["resource"],
                scope=values.get("scope"),
            )
        else:
            return _error_response("unsupported_grant_type", "Unsupported grant_type")
    except MCPOAuthError as exc:
        return _error_response(exc.code, exc.description)
    except MCPAuthorizationStateError:
        return _error_response(
            "temporarily_unavailable",
            "OAuth service is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    content = {
        "access_token": pair.access_token,
        "token_type": pair.token_type,
        "expires_in": pair.expires_in,
        "scope": pair.scope,
    }
    if pair.refresh_token is not None:
        content["refresh_token"] = pair.refresh_token
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=content,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/revoke")
async def revoke(request: Request, response: Response) -> JSONResponse:
    _no_store(response)
    values, error = await _form_values(request)
    if error is not None:
        return error
    allowed = {"token", "token_type_hint", "client_id"}
    if "client_secret" in values:
        return _error_response("invalid_client", "Client secrets are not accepted")
    if set(values) - allowed:
        return _error_response("invalid_request", "Unsupported OAuth parameter")
    if not values.get("token"):
        return _error_response("invalid_request", "token is required")
    mcp_oauth_service.revoke_token(
        token=values["token"],
        client_id=values.get("client_id"),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/apps", response_model=list[LinkedGrantResponse])
def list_linked_apps(
    response: Response,
    current_user: User = Depends(deps.get_current_active_user),
) -> list[LinkedGrantResponse]:
    _no_store(response)
    if current_user.id is None:
        return []
    return [
        _linked_grant_response(grant)
        for grant in mcp_oauth_service.list_linked_grants(current_user.id)
    ]


@router.delete("/apps/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_linked_app(
    grant_id: int,
    response: Response,
    current_user: User = Depends(deps.get_current_active_user),
) -> Response:
    _no_store(response)
    if current_user.id is None or not mcp_oauth_service.revoke_grant(
        grant_id=grant_id,
        user_id=current_user.id,
    ):
        return Response(status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "no-store"})
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
