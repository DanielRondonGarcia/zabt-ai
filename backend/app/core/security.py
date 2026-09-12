# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Authentication primitives for first-party local credentials.

The module deliberately has no external identity-provider integration. It
creates and validates short-lived API access JWTs and extracts credentials
from either a Bearer header or the web access cookie.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings, validate_auth_jwt_secret


bearer_scheme = HTTPBearer(auto_error=False)
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_JWT_ALGORITHM = "HS256"


def _configured_jwt_secret() -> str:
    """Return the validated signing secret without ever including it in errors."""

    try:
        return validate_auth_jwt_secret(settings.AUTH_JWT_SECRET)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("AUTH_JWT_SECRET is not securely configured") from exc


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def allowed_origins() -> set[str]:
    """Return the exact origins allowed to use web cookie authentication."""

    raw = settings.AUTH_ALLOWED_ORIGINS or settings.BACKEND_CORS_ORIGINS
    return {origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()}


def validate_request_origin(request: Request) -> None:
    """Reject cookie-authenticated state changes from another origin.

    A browser sends ``Origin`` for the API requests made by the web client.
    Referer is accepted as a compatibility fallback for clients that omit
    Origin but still identify their same-origin request. Missing origin data is
    rejected rather than treated as trusted.
    """

    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"

    if not origin or origin.rstrip("/") not in allowed_origins():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid request origin",
        )


def create_access_token(user_id: int) -> tuple[str, int]:
    """Create a short-lived access token and return it with its lifetime."""

    now = datetime.now(timezone.utc)
    expires_in = settings.AUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iss": settings.AUTH_JWT_ISSUER,
        "aud": settings.AUTH_JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(
        payload,
        _configured_jwt_secret(),
        algorithm=_JWT_ALGORITHM,
    )
    return token, expires_in


def verify_access_token(token: str) -> dict[str, Any]:
    """Validate a local access JWT without accepting legacy provider tokens."""

    if not token:
        raise _unauthorized()

    try:
        payload = jwt.decode(
            token,
            _configured_jwt_secret(),
            algorithms=[_JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_JWT_AUDIENCE,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
            },
        )
    except (JWTError, RuntimeError) as exc:
        # Do not return decoder details: they can disclose token structure and
        # are not useful to a client.
        raise _unauthorized() from exc

    if payload.get("type") != "access" or not payload.get("sub"):
        raise _unauthorized()

    try:
        int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise _unauthorized() from exc

    return payload


def get_token_payload(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Extract a Bearer token or the web HttpOnly access cookie."""

    if credentials is not None:
        return verify_access_token(credentials.credentials)

    token = request.cookies.get(settings.AUTH_ACCESS_COOKIE_NAME)
    if not token:
        raise _unauthorized()

    if request.method.upper() in _STATE_CHANGING_METHODS:
        validate_request_origin(request)

    return verify_access_token(token)


def verify_token(token: str) -> dict[str, Any]:
    """Backward-compatible string verifier used by non-HTTP transports."""

    return verify_access_token(token)


def verify_websocket_token(token: str | None) -> dict[str, Any]:
    """Validate a token supplied by a WebSocket client."""

    if not token:
        raise _unauthorized()
    return verify_access_token(token)
