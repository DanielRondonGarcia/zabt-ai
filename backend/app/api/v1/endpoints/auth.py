# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Local email/password authentication endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session

from app.api.deps import get_db
from app.core import security
from app.models import User, UserTier
from app.services import auth as auth_service


router = APIRouter()


ClientKind = Literal["web", "mobile"]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    picture: str | None
    tier: UserTier
    is_active: bool
    minutes_used_this_month: int


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    client: ClientKind

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return auth_service.normalize_email(value)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    client: ClientKind

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return auth_service.normalize_email(value)


class RefreshRequest(BaseModel):
    client: ClientKind
    refresh_token: str | None = Field(default=None, min_length=20, max_length=512)


class LogoutRequest(BaseModel):
    client: ClientKind
    refresh_token: str | None = Field(default=None, min_length=20, max_length=512)


class AuthResponse(BaseModel):
    """Tokens are populated only for mobile; web receives HttpOnly cookies."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserRead


def _auth_response(
    response: Response,
    user: User,
    tokens: auth_service.TokenBundle,
    client: ClientKind,
) -> AuthResponse:
    response.headers["Cache-Control"] = "no-store"
    if client == "web":
        auth_service.set_web_auth_cookies(response, tokens)
        return AuthResponse(expires_in=tokens.expires_in, user=user)
    return AuthResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=user,
    )


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    if payload.client == "web":
        security.validate_request_origin(request)
    try:
        user = auth_service.create_user(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
        tokens = auth_service.issue_tokens(db, user)
    except auth_service.DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered") from exc
    return _auth_response(response, user, tokens, payload.client)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    if payload.client == "web":
        security.validate_request_origin(request)
    try:
        user = auth_service.authenticate_user(
            db,
            email=payload.email,
            password=payload.password,
        )
        tokens = auth_service.issue_tokens(db, user)
    except auth_service.InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user") from exc
    except (auth_service.InvalidCredentialsError, ValueError) as exc:
        raise _invalid_credentials() from exc
    return _auth_response(response, user, tokens, payload.client)


@router.post("/refresh", response_model=AuthResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    raw_token = auth_service.refresh_token_from_request(
        request,
        client=payload.client,
        body_token=payload.refresh_token,
    )
    try:
        user, tokens = auth_service.rotate_refresh_token(db, raw_token or "")
    except auth_service.InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user") from exc
    except auth_service.InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return _auth_response(response, user, tokens, payload.client)


@router.post("/logout")
def logout(
    payload: LogoutRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    raw_token = auth_service.refresh_token_from_request(
        request,
        client=payload.client,
        body_token=payload.refresh_token,
    )
    auth_service.revoke_refresh_token(db, raw_token)
    if payload.client == "web":
        auth_service.clear_web_auth_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
