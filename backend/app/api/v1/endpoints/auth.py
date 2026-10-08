# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Local authentication and Microsoft Entra public-client OIDC endpoints."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlmodel import Session, select

from app.api.deps import get_current_active_user, get_db, get_optional_current_active_user
from app.core import security
from app.core.config import settings
from app.models import (
    ExternalIdentity,
    ExternalIdentityProvider,
    MicrosoftOidcConfiguration,
    User,
    UserLoginMode,
    UserTier,
)
from app.services import auth as auth_service
from app.services import authentication_configuration as auth_mode_service
from app.services.integration import is_token_storage_configured
from app.services.microsoft_oidc import (
    OIDC_SCOPES,
    MicrosoftOidcAccountConflictError,
    MicrosoftOidcClient,
    MicrosoftOidcError,
    MicrosoftOidcExternalIdentityConflictError,
    MicrosoftOidcProviderError,
    identity_from_claims,
    link_external_identity,
    resolve_or_create_user,
)
from app.services.microsoft_oidc_configuration import (
    MicrosoftOidcConfigurationValidationError,
    get_microsoft_oidc_configuration,
    is_microsoft_oidc_runtime_configured,
    upsert_microsoft_oidc_configuration,
)
from app.services.oauth_state import (
    OAuthStateError,
    OAuthStateService,
    OAuthStateTransaction,
)


router = APIRouter()
oauth_state_service = OAuthStateService()

ClientKind = Literal["web", "mobile"]
OidcChallengePurpose = Literal["login", "link"]
OIDC_CHALLENGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
MAX_MICROSOFT_ID_TOKEN_LENGTH = 32_768
MAX_MICROSOFT_ID_TOKEN_REQUEST_BODY_LENGTH = MAX_MICROSOFT_ID_TOKEN_LENGTH + 512


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    picture: str | None
    tier: UserTier
    is_active: bool
    is_superuser: bool
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


class SuperuserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return auth_service.normalize_email(value)


class AuthenticationModeUpdate(BaseModel):
    mode: UserLoginMode


class AuthenticationModeResponse(BaseModel):
    mode: UserLoginMode
    oidc_configured: bool


class AuthenticationModeConfigurationResponse(AuthenticationModeResponse):
    unlinked_user_count: int
    unlinked_passwordless_user_count: int


class MicrosoftOidcChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: OidcChallengePurpose
    client: ClientKind


class MicrosoftOidcChallengeResponse(BaseModel):
    challenge_id: str
    nonce: str


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
    """Public Microsoft OIDC and separately reported Graph readiness."""

    configured: bool
    client_id: str | None
    tenant: str | None
    redirect_uri: str | None
    scopes: list[str]
    enabled: bool
    graph_configured: bool
    token_storage_configured: bool


class MicrosoftOidcConfigurationResponse(MicrosoftOidcStatus):
    """Authenticated configuration view with administration metadata."""

    can_manage: bool
    is_admin: bool
    is_superuser: bool
    created_at: datetime | None
    updated_at: datetime | None
    updated_by: int | None


class MicrosoftOidcConfigurationUpdate(BaseModel):
    """Only public SPA configuration is accepted; there is no secret field."""

    model_config = ConfigDict(hide_input_in_errors=True)

    client_id: str = Field(min_length=1, max_length=64)
    tenant: str | None = Field(default=None, min_length=1, max_length=255)
    # Accept the storage-oriented spelling for API clients that already use it,
    # while the browser form sends the shorter public ``tenant`` field.
    tenant_id: str | None = Field(default=None, min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_tenant_aliases(self) -> "MicrosoftOidcConfigurationUpdate":
        if not self.tenant and not self.tenant_id:
            raise ValueError("Tenant is required")
        if self.tenant and self.tenant_id and self.tenant != self.tenant_id:
            raise ValueError("Tenant values must match")
        return self

    @property
    def tenant_value(self) -> str:
        return self.tenant or self.tenant_id or ""


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


def _graph_configuration_configured() -> bool:
    """Check only the confidential delegated Graph deployment settings."""

    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            settings.MICROSOFT_CLIENT_ID,
            settings.MICROSOFT_CLIENT_SECRET,
            settings.MICROSOFT_REDIRECT_URI,
        )
    )


def _status_response(db: Session) -> MicrosoftOidcStatus:
    configuration = get_microsoft_oidc_configuration(db)
    return MicrosoftOidcStatus(
        configured=is_microsoft_oidc_runtime_configured(configuration),
        client_id=configuration.client_id if configuration else None,
        tenant=configuration.tenant_id if configuration else None,
        redirect_uri=configuration.redirect_uri if configuration else None,
        scopes=list(OIDC_SCOPES),
        enabled=bool(configuration.enabled) if configuration else False,
        # These values intentionally come from the separate Graph boundary.
        graph_configured=_graph_configuration_configured(),
        token_storage_configured=is_token_storage_configured(),
    )


def _configuration_response(
    db: Session,
    current_user: User,
) -> MicrosoftOidcConfigurationResponse:
    configuration = get_microsoft_oidc_configuration(db)
    return MicrosoftOidcConfigurationResponse(
        **_status_response(db).model_dump(),
        can_manage=bool(current_user.is_superuser),
        is_admin=bool(current_user.is_admin),
        is_superuser=bool(current_user.is_superuser),
        created_at=configuration.created_at if configuration else None,
        updated_at=configuration.updated_at if configuration else None,
        updated_by=configuration.updated_by if configuration else None,
    )


def _authentication_mode_response(db: Session) -> AuthenticationModeResponse:
    configuration = get_microsoft_oidc_configuration(db)
    return AuthenticationModeResponse(
        mode=auth_mode_service.get_user_login_mode(db),
        oidc_configured=is_microsoft_oidc_runtime_configured(configuration),
    )


def _authentication_mode_configuration_response(
    db: Session,
) -> AuthenticationModeConfigurationResponse:
    response = _authentication_mode_response(db)
    return AuthenticationModeConfigurationResponse(
        mode=response.mode,
        oidc_configured=response.oidc_configured,
        unlinked_user_count=auth_mode_service.count_unlinked_active_local_accounts(db),
        unlinked_passwordless_user_count=auth_mode_service.count_unlinked_passwordless_accounts(db),
    )


def _oidc_verifier(configuration: MicrosoftOidcConfiguration) -> MicrosoftOidcClient:
    return MicrosoftOidcClient.from_configuration(configuration)


def _oidc_not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Microsoft sign-in is not configured",
    )


def _oidc_validation_failure() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Microsoft sign-in could not be completed",
    )


def _invalid_id_token_request() -> HTTPException:
    """Return a static malformed-request error without echoing the assertion."""

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid Microsoft ID token request",
    )


async def _read_microsoft_id_token(request: Request) -> str:
    """Read and bound the browser assertion before any Pydantic error can echo it."""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_MICROSOFT_ID_TOKEN_REQUEST_BODY_LENGTH:
                raise _invalid_id_token_request()
        except (TypeError, ValueError) as exc:
            raise _invalid_id_token_request() from exc

    raw_body = await request.body()
    if len(raw_body) > MAX_MICROSOFT_ID_TOKEN_REQUEST_BODY_LENGTH:
        raise _invalid_id_token_request()
    try:
        body: Any = json.loads(raw_body)
    except (TypeError, ValueError) as exc:
        raise _invalid_id_token_request() from exc
    if not isinstance(body, dict):
        raise _invalid_id_token_request()
    id_token = body.get("id_token")
    if (
        not isinstance(id_token, str)
        or not id_token
        or len(id_token) > MAX_MICROSOFT_ID_TOKEN_LENGTH
    ):
        raise _invalid_id_token_request()
    return id_token


async def _read_microsoft_oidc_request(
    request: Request,
) -> tuple[str, str, ClientKind]:
    """Read bounded token, challenge, and transport fields without echoing them."""

    id_token = await _read_microsoft_id_token(request)
    try:
        body = json.loads(await request.body())
    except (TypeError, ValueError) as exc:
        raise _invalid_id_token_request() from exc
    challenge_id = body.get("challenge_id") if isinstance(body, dict) else None
    if not isinstance(challenge_id, str) or not OIDC_CHALLENGE_ID_RE.fullmatch(challenge_id):
        raise _invalid_id_token_request()
    client = body.get("client", "web") if isinstance(body, dict) else None
    if client not in ("web", "mobile"):
        raise _invalid_id_token_request()
    return id_token, challenge_id, client


def _consume_microsoft_oidc_challenge(
    challenge_id: str,
    *,
    purpose: Literal["oidc_login", "oidc_link"],
    client: ClientKind,
    user_id: int | None = None,
) -> OAuthStateTransaction:
    try:
        transaction = oauth_state_service.consume(challenge_id)
    except OAuthStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is temporarily unavailable",
        ) from exc
    if (
        transaction is None
        or transaction.purpose != purpose
        or transaction.client != client
        or transaction.user_id != user_id
    ):
        raise _invalid_id_token_request()
    return transaction


def _has_microsoft_identity(db: Session, user_id: int) -> bool:
    return (
        db.exec(
            select(ExternalIdentity.id).where(
                ExternalIdentity.user_id == user_id,
                ExternalIdentity.provider == ExternalIdentityProvider.MICROSOFT,
            )
        ).first()
        is not None
    )


def _validate_oidc_request_origin(request: Request, client: ClientKind) -> None:
    if client == "web" or request.headers.get("origin") is not None:
        security.validate_request_origin(request)


@router.get("/mode", response_model=AuthenticationModeResponse)
def authentication_mode(
    response: Response,
    db: Session = Depends(get_db),
) -> AuthenticationModeResponse:
    """Return the global regular-user sign-in mode before authentication."""

    response.headers["Cache-Control"] = "no-store"
    return _authentication_mode_response(db)


@router.post(
    "/microsoft/oidc/challenge",
    response_model=MicrosoftOidcChallengeResponse,
)
def create_microsoft_oidc_challenge(
    payload: MicrosoftOidcChallengeRequest,
    request: Request,
    response: Response,
    current_user: User | None = Depends(get_optional_current_active_user),
    db: Session = Depends(get_db),
) -> MicrosoftOidcChallengeResponse:
    """Create a one-time Redis-backed challenge for OIDC login or account linking."""

    _validate_oidc_request_origin(request, payload.client)
    mode = auth_mode_service.get_user_login_mode(db)
    if payload.purpose == "login":
        if mode != UserLoginMode.MICROSOFT_OIDC:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Microsoft sign-in is not enabled",
            )
        state_purpose = "oidc_login"
        user_id = None
    else:
        if current_user is None or current_user.id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if mode == UserLoginMode.MICROSOFT_OIDC and not _has_microsoft_identity(
            db,
            current_user.id,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Microsoft account linking is not enabled for this account",
            )
        state_purpose = "oidc_link"
        user_id = current_user.id

    configuration = get_microsoft_oidc_configuration(db)
    if not is_microsoft_oidc_runtime_configured(configuration):
        raise _oidc_not_configured()

    try:
        transaction = oauth_state_service.create_transaction(
            purpose=state_purpose,
            client=payload.client,
            user_id=user_id,
        )
    except (OAuthStateError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is temporarily unavailable",
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    return MicrosoftOidcChallengeResponse(
        challenge_id=transaction.state,
        nonce=transaction.nonce,
    )


@router.get("/mode/config", response_model=AuthenticationModeConfigurationResponse)
def authentication_mode_configuration(
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> AuthenticationModeConfigurationResponse:
    """Return readiness details to the system superuser only."""

    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System superuser access is required",
        )
    response.headers["Cache-Control"] = "no-store"
    return _authentication_mode_configuration_response(db)


@router.put("/mode/config", response_model=AuthenticationModeConfigurationResponse)
def update_authentication_mode(
    payload: AuthenticationModeUpdate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> AuthenticationModeConfigurationResponse:
    """Change the global sign-in mode for regular accounts."""

    if not current_user.is_superuser or current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System superuser access is required",
        )
    security.validate_request_origin(request)

    try:
        with auth_service._authentication_policy_lock(db):
            if payload.mode == UserLoginMode.MICROSOFT_OIDC:
                passwordless_count = auth_mode_service.count_unlinked_passwordless_accounts(db)
                if passwordless_count:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Active passwordless accounts without a linked Microsoft identity "
                            "require identity reconciliation or deactivation by the system "
                            "superuser before switching to Microsoft OIDC."
                        ),
                    )
                oidc_configuration = get_microsoft_oidc_configuration(db)
                if not is_microsoft_oidc_runtime_configured(oidc_configuration):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Microsoft OIDC must be fully configured and enabled "
                            "before selecting this sign-in mode."
                        ),
                    )
                unlinked_count = auth_mode_service.count_unlinked_active_local_accounts(db)
                if unlinked_count:
                    noun = "account" if unlinked_count == 1 else "accounts"
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Link Microsoft for {unlinked_count} active local {noun} "
                            "before selecting Microsoft OIDC."
                        ),
                    )

            auth_mode_service.set_user_login_mode(
                db,
                mode=payload.mode,
                updated_by=current_user.id,
            )
            db.commit()
    except HTTPException:
        db.rollback()
        raise

    response.headers["Cache-Control"] = "no-store"
    return _authentication_mode_configuration_response(db)


@router.get("/microsoft/status", response_model=MicrosoftOidcStatus)
def microsoft_oidc_status(
    response: Response,
    db: Session = Depends(get_db),
) -> MicrosoftOidcStatus:
    """Return non-secret runtime configuration before the user is authenticated."""

    response.headers["Cache-Control"] = "no-store"
    return _status_response(db)


@router.get(
    "/microsoft/config",
    response_model=MicrosoftOidcConfigurationResponse,
)
def microsoft_oidc_configuration(
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> MicrosoftOidcConfigurationResponse:
    """Return the global OIDC configuration to the system superuser only."""

    response.headers["Cache-Control"] = "no-store"
    if not current_user.is_superuser or current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System superuser access is required",
        )
    return _configuration_response(db, current_user)


@router.put(
    "/microsoft/config",
    response_model=MicrosoftOidcConfigurationResponse,
)
def update_microsoft_oidc_configuration(
    payload: MicrosoftOidcConfigurationUpdate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> MicrosoftOidcConfigurationResponse:
    """Save the singleton public OIDC configuration for the system superuser."""

    if not current_user.is_superuser or current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System superuser access is required",
        )
    request_origin = security.validate_request_origin(request)

    try:
        with auth_service._authentication_policy_lock(db):
            oidc_mode_active = (
                auth_mode_service.get_user_login_mode(db) == UserLoginMode.MICROSOFT_OIDC
            )
            active_configuration = get_microsoft_oidc_configuration(db)
            if oidc_mode_active and not payload.enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Switch regular-user sign-in mode to local before disabling Microsoft OIDC."
                    ),
                )
            if oidc_mode_active and active_configuration is not None and (
                payload.client_id.casefold() != active_configuration.client_id.casefold()
                or payload.tenant_value.casefold() != active_configuration.tenant_id.casefold()
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Switch regular-user sign-in mode to local before changing the "
                        "Microsoft client or tenant."
                    ),
                )

            upsert_microsoft_oidc_configuration(
                db,
                client_id=payload.client_id,
                tenant=payload.tenant_value,
                redirect_uri=payload.redirect_uri,
                enabled=payload.enabled,
                updated_by=current_user.id,
                spa_origin=request_origin,
            )
            db.commit()
    except HTTPException:
        db.rollback()
        raise
    except MicrosoftOidcConfigurationValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Microsoft OIDC configuration is invalid",
        ) from exc

    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return _configuration_response(db, current_user)


@router.post(
    "/microsoft/oidc/exchange",
    response_model=AuthResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["id_token", "challenge_id"],
                        "properties": {
                            "id_token": {
                                "type": "string",
                                "maxLength": MAX_MICROSOFT_ID_TOKEN_LENGTH,
                            },
                            "challenge_id": {
                                "type": "string",
                                "minLength": 32,
                                "maxLength": 128,
                            },
                            "client": {
                                "type": "string",
                                "enum": ["web", "mobile"],
                                "default": "web",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def exchange_microsoft_oidc_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Validate an ID token and issue web cookies or native-client tokens."""

    if auth_mode_service.get_user_login_mode(db) != UserLoginMode.MICROSOFT_OIDC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Microsoft sign-in is not enabled",
        )
    id_token, challenge_id, client = await _read_microsoft_oidc_request(request)
    _validate_oidc_request_origin(request, client)
    configuration = get_microsoft_oidc_configuration(db)
    if not is_microsoft_oidc_runtime_configured(configuration):
        raise _oidc_not_configured()
    assert configuration is not None
    challenge = _consume_microsoft_oidc_challenge(
        challenge_id,
        purpose="oidc_login",
        client=client,
    )

    try:
        claims = await _oidc_verifier(configuration).validate_id_token(
            id_token,
            nonce=challenge.nonce,
        )
        user = resolve_or_create_user(db, identity_from_claims(claims))
        if user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Microsoft sign-in is not enabled for this account",
            )
        tokens = auth_service.issue_tokens(db, user)
    except HTTPException:
        db.rollback()
        raise
    except MicrosoftOidcAccountConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A local account already uses this email. Sign in locally and link Microsoft from settings.",
        ) from exc
    except auth_service.InactiveUserError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        ) from exc
    except MicrosoftOidcProviderError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is temporarily unavailable",
        ) from exc
    except (MicrosoftOidcError, TypeError, ValueError) as exc:
        db.rollback()
        raise _oidc_validation_failure() from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is temporarily unavailable",
        ) from exc

    return _auth_response(response, user, tokens, client)


@router.post(
    "/microsoft/oidc/link",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["id_token", "challenge_id"],
                        "properties": {
                            "id_token": {
                                "type": "string",
                                "maxLength": MAX_MICROSOFT_ID_TOKEN_LENGTH,
                            },
                            "challenge_id": {
                                "type": "string",
                                "minLength": 32,
                                "maxLength": 128,
                            },
                            "client": {
                                "type": "string",
                                "enum": ["web", "mobile"],
                                "default": "web",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def link_microsoft_oidc_token(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Explicitly link a browser-issued Microsoft identity to the current user."""

    id_token, challenge_id, client = await _read_microsoft_oidc_request(request)
    _validate_oidc_request_origin(request, client)
    configuration = get_microsoft_oidc_configuration(db)
    if not is_microsoft_oidc_runtime_configured(configuration):
        raise _oidc_not_configured()
    assert configuration is not None
    if current_user.id is None:
        raise _invalid_id_token_request()
    challenge = _consume_microsoft_oidc_challenge(
        challenge_id,
        purpose="oidc_link",
        client=client,
        user_id=current_user.id,
    )

    try:
        claims = await _oidc_verifier(configuration).validate_id_token(
            id_token,
            nonce=challenge.nonce,
        )
        link_external_identity(db, identity_from_claims(claims), current_user)
        db.commit()
    except MicrosoftOidcExternalIdentityConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Microsoft account is already linked to another Zabt account.",
        ) from exc
    except auth_service.InactiveUserError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user") from exc
    except MicrosoftOidcProviderError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft account linking is temporarily unavailable",
        ) from exc
    except (MicrosoftOidcError, TypeError, ValueError) as exc:
        db.rollback()
        raise _oidc_validation_failure() from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft account linking is temporarily unavailable",
        ) from exc

    return {"status": "linked"}


@router.get("/microsoft/start", include_in_schema=False)
def microsoft_oidc_legacy_start() -> None:
    """Retire the former confidential OIDC start route without reading a secret."""

    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Microsoft OIDC now uses the public SPA flow",
    )


@router.get("/microsoft/link/start", include_in_schema=False)
def microsoft_oidc_legacy_link_start() -> None:
    """Retire the former server-side OIDC link route."""

    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Microsoft OIDC now uses the public SPA flow",
    )


@router.get("/microsoft/callback", include_in_schema=False)
def microsoft_oidc_legacy_callback() -> None:
    """Return a safe compatibility response for old confidential callbacks."""

    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Microsoft OIDC now uses the public SPA flow",
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
        with auth_service._authentication_policy_lock(db):
            if auth_mode_service.get_user_login_mode(db) != UserLoginMode.LOCAL:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Local sign-in is not enabled",
                )
            user = auth_service.create_user(
                db,
                email=payload.email,
                password=payload.password,
                full_name=payload.full_name,
            )
            tokens = auth_service.issue_tokens(db, user)
    except HTTPException:
        db.rollback()
        raise
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
        with auth_service._authentication_policy_lock(db):
            if auth_mode_service.get_user_login_mode(db) != UserLoginMode.LOCAL:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Local sign-in is not enabled",
                )
            user = auth_service.authenticate_user(
                db,
                email=payload.email,
                password=payload.password,
            )
            if user.is_superuser:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Use the system superuser sign-in endpoint",
                )
            tokens = auth_service.issue_tokens(db, user)
    except HTTPException:
        db.rollback()
        raise
    except auth_service.InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user") from exc
    except (auth_service.InvalidCredentialsError, ValueError) as exc:
        raise _invalid_credentials() from exc
    return _auth_response(response, user, tokens, payload.client)


@router.post("/superuser/login", response_model=AuthResponse)
def superuser_login(
    payload: SuperuserLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Authenticate the deployment-owned operator using web-only cookies."""

    security.validate_request_origin(request)
    try:
        user = auth_service.authenticate_user(
            db,
            email=payload.email,
            password=payload.password,
        )
    except (auth_service.InvalidCredentialsError, auth_service.InactiveUserError, ValueError) as exc:
        raise _invalid_credentials() from exc
    if not user.is_superuser:
        raise _invalid_credentials()

    tokens = auth_service.issue_tokens(db, user)
    return _auth_response(response, user, tokens, "web")


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
