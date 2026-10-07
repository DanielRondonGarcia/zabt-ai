# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Authenticated management endpoints for the read-only MCP integration."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api import deps
from app.core.config import settings
from app.models import User
from app.services.mcp_tokens import (
    MCPTokenValidationError,
    mcp_token_service,
)
from app.mcp_server import MCP_TOOL_NAMES


router = APIRouter()


class MCPTokenCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(ge=1, le=365)


class MCPTokenMetadata(BaseModel):
    id: int
    label: str
    token_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None


class MCPTokenCreated(MCPTokenMetadata):
    """The only response type that includes the raw token."""

    token: str


class MCPStatus(BaseModel):
    enabled: bool
    endpoint: str
    auth_mode: Literal["bearer_token"]
    tools: list[str]


def _metadata(token) -> MCPTokenMetadata:
    return MCPTokenMetadata.model_validate(token, from_attributes=True)


@router.post(
    "/tokens",
    response_model=MCPTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_mcp_token(
    payload: MCPTokenCreateRequest,
    response: Response,
    current_user: User = Depends(deps.get_current_active_user),
) -> MCPTokenCreated:
    """Create a token; the raw bearer value is returned only in this response."""

    response.headers["Cache-Control"] = "no-store"
    try:
        token, raw_token = mcp_token_service.create(
            user_id=current_user.id,
            label=payload.label,
            expires_in_days=payload.expires_in_days,
        )
    except MCPTokenValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MCPTokenCreated(**_metadata(token).model_dump(), token=raw_token)


@router.get("/tokens", response_model=list[MCPTokenMetadata])
def list_mcp_tokens(
    current_user: User = Depends(deps.get_current_active_user),
) -> list[MCPTokenMetadata]:
    """List owner metadata without exposing token hashes or raw tokens."""

    return [_metadata(token) for token in mcp_token_service.list(current_user.id)]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_mcp_token(
    token_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> Response:
    """Revoke an owner token without revealing whether another user owns it."""

    if not mcp_token_service.revoke(token_id=token_id, user_id=current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/status", response_model=MCPStatus)
def get_mcp_status(
    current_user: User = Depends(deps.get_current_active_user),
) -> MCPStatus:
    """Return the safe MCP capability summary for the current user."""

    # The dependency intentionally authenticates the request even though the
    # returned status is non-sensitive, keeping settings reads consistent.
    _ = current_user
    return MCPStatus(
        enabled=True,
        endpoint=f"{settings.API_V1_STR}/mcp",
        auth_mode="bearer_token",
        tools=list(MCP_TOOL_NAMES),
    )
