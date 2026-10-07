# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Local email/password authentication endpoints."""

from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session

from app.api.deps import get_current_active_user, get_db
from app.core import security
from app.core.config import settings
from app.models import User, UserTier
from app.services import auth as auth_service
from app.services.microsoft_oidc import (
    OIDC_SCOPES,
    MicrosoftOidcAccountConflictError,
    MicrosoftOidcError,
    MicrosoftOidcExternalIdentityConflictError,
    MicrosoftOidcIdentity,
    MicrosoftOidcClient,
    MicrosoftOidcValidationError,
    identity_from_claims,
    is_microsoft_oidc_configured,
    link_external_identity,
    resolve_or_create_user,
)
from app.services.oauth_state import (
    OAuthStateError,
    OAuthStateService,
    build_pkce_challenge,
    validate_next_path,
)


router = APIRouter()
oauth_state_service = OAuthStateService()


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


class MicrosoftOidcStatus(BaseModel):
    """Non-sensitive Microsoft sign-in configuration for the web UI."""

    configured: bool
    tenant: str
    oidc_redirect_uri: str
    graph_redirect_uri: str
    oidc_scopes: list[str]


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


def _microsoft_client() -> MicrosoftOidcClient:
    return MicrosoftOidcClient.from_settings()


def _login_error_redirect(error_code: str) -> RedirectResponse:
    login_url = f"{settings.APP_URL.rstrip('/')}/login?{urlencode({'error': error_code})}"
    response = RedirectResponse(url=login_url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    return response


def _success_redirect(next_path: str) -> RedirectResponse:
    redirect_url = f"{settings.APP_URL.rstrip('/')}{next_path}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    return response


def _microsoft_result_redirect(next_path: str, result_code: str) -> RedirectResponse:
    parsed = urlsplit(next_path)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("microsoft", result_code))
    result_path = urlunsplit(("", "", parsed.path, urlencode(query), ""))
    return _success_redirect(result_path)


def _get_authenticated_link_user(
    request: Request,
    db: Session,
    user_id: int,
) -> User | None:
    """Require the same active web session that initiated an explicit link."""

    raw_token = request.cookies.get(settings.AUTH_ACCESS_COOKIE_NAME)
    if not raw_token:
        return None
    try:
        payload = security.verify_access_token(raw_token)
        authenticated_id = int(payload["sub"])
    except Exception:
        return None
    if authenticated_id != user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


@router.get("/microsoft/status", response_model=MicrosoftOidcStatus)
def microsoft_oidc_status() -> MicrosoftOidcStatus:
    """Return non-secret OIDC and Graph configuration for the configuration UI."""

    return MicrosoftOidcStatus(
        configured=is_microsoft_oidc_configured(),
        tenant=settings.MICROSOFT_TENANT_ID,
        oidc_redirect_uri=settings.MICROSOFT_OIDC_REDIRECT_URI,
        graph_redirect_uri=settings.MICROSOFT_REDIRECT_URI,
        oidc_scopes=list(OIDC_SCOPES),
    )


async def _start_microsoft_transaction(
    *,
    purpose: str,
    next_path: str,
    user_id: int | None = None,
) -> RedirectResponse:
    if not is_microsoft_oidc_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is not configured",
        )

    try:
        transaction_kwargs = {"purpose": purpose, "next_path": next_path}
        if user_id is not None:
            transaction_kwargs["user_id"] = user_id
        transaction = oauth_state_service.create_transaction(**transaction_kwargs)
        client = _microsoft_client()
        authorization_url = await client.build_authorization_url(
            state=transaction.state,
            nonce=transaction.nonce,
            code_challenge=build_pkce_challenge(transaction.code_verifier),
        )
    except (OAuthStateError, MicrosoftOidcError, ValueError) as exc:
        # The state is short-lived; consume it if discovery failed before the
        # browser received the authorization URL.
        if "transaction" in locals():
            try:
                oauth_state_service.consume(transaction.state)
            except OAuthStateError:
                pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is temporarily unavailable",
        ) from exc
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)


@router.get("/microsoft/start")
async def microsoft_oidc_start(
    next_path: str = Query(default="/", alias="next", max_length=2048),
) -> RedirectResponse:
    """Start the anonymous Entra OIDC authorization-code + PKCE flow."""

    try:
        safe_next = validate_next_path(next_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid next path",
        ) from exc
    return await _start_microsoft_transaction(
        purpose="oidc_login",
        next_path=safe_next,
    )


@router.get("/microsoft/link/start")
async def microsoft_oidc_link_start(
    current_user: User = Depends(get_current_active_user),
    next_path: str = Query(default="/integrations", alias="next", max_length=2048),
) -> RedirectResponse:
    """Start an explicit Microsoft identity link for the active local user."""

    try:
        safe_next = validate_next_path(next_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid next path",
        ) from exc
    return await _start_microsoft_transaction(
        purpose="oidc_link",
        next_path=safe_next,
        user_id=current_user.id,
    )


@router.get("/microsoft/callback")
async def microsoft_oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=128),
) -> RedirectResponse:
    """Consume the one-time transaction and finish Entra sign-in."""

    if not state:
        return _login_error_redirect("microsoft_sign_in_failed")

    try:
        transaction = oauth_state_service.consume(state)
    except OAuthStateError:
        return _login_error_redirect("microsoft_sign_in_failed")
    if transaction is None or transaction.purpose not in {"oidc_login", "oidc_link"}:
        return _login_error_redirect("microsoft_sign_in_failed")

    if error is not None:
        if error == "access_denied":
            if transaction.purpose == "oidc_link":
                return _microsoft_result_redirect(transaction.next_path, "cancelled")
            return _login_error_redirect("microsoft_cancelled")
        if transaction.purpose == "oidc_link":
            return _microsoft_result_redirect(transaction.next_path, "link_failed")
        return _login_error_redirect("microsoft_sign_in_failed")
    if not code or not is_microsoft_oidc_configured():
        if transaction.purpose == "oidc_link":
            return _microsoft_result_redirect(transaction.next_path, "link_failed")
        return _login_error_redirect("microsoft_sign_in_failed")

    try:
        client = _microsoft_client()
        token_data = await client.exchange_code(
            code=code,
            code_verifier=transaction.code_verifier,
        )
        claims = await client.validate_id_token(
            token_data["id_token"],
            nonce=transaction.nonce,
        )
        identity: MicrosoftOidcIdentity = identity_from_claims(claims)
        if transaction.purpose == "oidc_link":
            if transaction.user_id is None:
                raise MicrosoftOidcValidationError("Microsoft link owner is missing")
            user = _get_authenticated_link_user(request, db, transaction.user_id)
            if user is None:
                return _microsoft_result_redirect(transaction.next_path, "link_failed")
            link_external_identity(db, identity, user)
            db.commit()
            return _microsoft_result_redirect(transaction.next_path, "linked")
        user = resolve_or_create_user(db, identity)
        tokens = auth_service.issue_tokens(db, user)
    except MicrosoftOidcAccountConflictError:
        db.rollback()
        return _login_error_redirect("microsoft_local_account_exists")
    except MicrosoftOidcExternalIdentityConflictError:
        db.rollback()
        return _microsoft_result_redirect(transaction.next_path, "already_linked")
    except auth_service.InactiveUserError:
        db.rollback()
        if transaction.purpose == "oidc_link":
            return _microsoft_result_redirect(transaction.next_path, "link_failed")
        return _login_error_redirect("microsoft_sign_in_failed")
    except (MicrosoftOidcError, ValueError):
        db.rollback()
        if transaction.purpose == "oidc_link":
            return _microsoft_result_redirect(transaction.next_path, "link_failed")
        return _login_error_redirect("microsoft_sign_in_failed")
    except Exception:
        # Callback failures are deliberately indistinguishable to the browser.
        db.rollback()
        if transaction.purpose == "oidc_link":
            return _microsoft_result_redirect(transaction.next_path, "link_failed")
        return _login_error_redirect("microsoft_sign_in_failed")

    response = _success_redirect(transaction.next_path)
    auth_service.set_web_auth_cookies(response, tokens)
    return response


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
