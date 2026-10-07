# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Microsoft Entra OpenID Connect client and identity-linking service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hmac
import logging
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit

import httpx
from jose import JWTError, jwt
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import (
    MICROSOFT_TENANT_ALIASES,
    MICROSOFT_TENANT_GUID_RE,
    Settings,
    settings,
    validate_microsoft_tenant_id,
)
from app.models import ExternalIdentity, ExternalIdentityProvider, User
from app.services import auth as auth_service


logger = logging.getLogger(__name__)

MICROSOFT_AUTH_BASE = "https://login.microsoftonline.com"
OIDC_SCOPES: tuple[str, ...] = ("openid", "profile", "email")
OIDC_SCOPE_STRING = " ".join(OIDC_SCOPES)
_MICROSOFT_HOST = "login.microsoftonline.com"
_MULTI_TENANT_IDS = MICROSOFT_TENANT_ALIASES
_MICROSOFT_ISSUER_TEMPLATE = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
_TENANT_GUID_RE = MICROSOFT_TENANT_GUID_RE


class MicrosoftOidcError(Exception):
    """Base class for errors that are safe to collapse into a browser error."""


class MicrosoftOidcConfigurationError(MicrosoftOidcError):
    """Raised when the OIDC client is not safely configured."""


class MicrosoftOidcProviderError(MicrosoftOidcError):
    """Raised when discovery or token exchange cannot complete."""


class MicrosoftOidcValidationError(MicrosoftOidcError):
    """Raised when an ID token does not satisfy the OIDC contract."""


class MicrosoftOidcAccountConflictError(MicrosoftOidcError):
    """Raised when anonymous OIDC would otherwise link a local account."""


class MicrosoftOidcExternalIdentityConflictError(MicrosoftOidcError):
    """Raised when an external identity belongs to another local user."""


@dataclass(frozen=True)
class MicrosoftOidcIdentity:
    subject: str
    tenant_id: str
    email: str | None
    full_name: str | None
    picture: str | None


def is_microsoft_oidc_configured(config: Settings = settings) -> bool:
    """Return whether the confidential OIDC callback has all required settings."""

    required = (
        config.MICROSOFT_CLIENT_ID,
        config.MICROSOFT_CLIENT_SECRET,
        config.MICROSOFT_TENANT_ID,
        config.MICROSOFT_OIDC_REDIRECT_URI,
    )
    return all(isinstance(value, str) and bool(value.strip()) for value in required)


def _bounded_string(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum:
        return None
    return value


class MicrosoftOidcClient:
    """Small OIDC client that never treats an ID token as a Graph access token."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        redirect_uri: str,
        http_timeout: float = 10.0,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri.strip()
        self.http_timeout = http_timeout
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise MicrosoftOidcConfigurationError("Microsoft OIDC is not configured")
        try:
            self.tenant_id = validate_microsoft_tenant_id(tenant_id)
        except ValueError as exc:
            raise MicrosoftOidcConfigurationError("Microsoft tenant is invalid") from exc
        if http_timeout <= 0 or http_timeout > 60:
            raise MicrosoftOidcConfigurationError("Microsoft OIDC timeout is invalid")

    @classmethod
    def from_settings(cls, config: Settings = settings) -> "MicrosoftOidcClient":
        if not is_microsoft_oidc_configured(config):
            raise MicrosoftOidcConfigurationError("Microsoft OIDC is not configured")
        return cls(
            client_id=config.MICROSOFT_CLIENT_ID,
            client_secret=config.MICROSOFT_CLIENT_SECRET,
            tenant_id=config.MICROSOFT_TENANT_ID,
            redirect_uri=config.MICROSOFT_OIDC_REDIRECT_URI,
            http_timeout=config.MICROSOFT_OIDC_HTTP_TIMEOUT_SECONDS,
        )

    @property
    def authority(self) -> str:
        return f"{self._tenant_base}/v2.0"

    @property
    def _tenant_base(self) -> str:
        return f"{MICROSOFT_AUTH_BASE}/{quote(self.tenant_id, safe='')}"

    @property
    def authorization_endpoint(self) -> str:
        return f"{self._tenant_base}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self._tenant_base}/oauth2/v2.0/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self._tenant_base}/discovery/v2.0/keys"

    @property
    def expected_issuer(self) -> str:
        if self.tenant_id.casefold() in _MULTI_TENANT_IDS:
            return _MICROSOFT_ISSUER_TEMPLATE
        return f"{MICROSOFT_AUTH_BASE}/{quote(self.tenant_id, safe='')}/v2.0"

    @property
    def is_multi_tenant(self) -> bool:
        return self.tenant_id.casefold() in _MULTI_TENANT_IDS

    @property
    def discovery_url(self) -> str:
        return f"{self.authority}/.well-known/openid-configuration"

    async def discovery(self) -> dict[str, Any]:
        """Fetch Microsoft metadata and pin every security-sensitive endpoint."""

        metadata = await self._get_json(self.discovery_url, operation="discovery")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                raise MicrosoftOidcProviderError("Microsoft OIDC metadata is incomplete")
        self._require_microsoft_url(
            metadata["authorization_endpoint"],
            expected=self.authorization_endpoint,
        )
        self._require_microsoft_url(
            metadata["token_endpoint"],
            expected=self.token_endpoint,
        )
        self._require_microsoft_url(metadata["jwks_uri"], expected=self.jwks_uri)
        self._validate_discovery_issuer(metadata["issuer"])
        return metadata

    async def build_authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        await self.discovery()
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": OIDC_SCOPE_STRING,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange an authorization code without returning or logging token bodies."""

        await self.discovery()
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        token_data = await self._post_form(
            self.token_endpoint,
            data=data,
            operation="token exchange",
        )
        if not isinstance(token_data.get("id_token"), str):
            raise MicrosoftOidcProviderError("Microsoft OIDC response did not contain an ID token")
        return token_data

    async def validate_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        """Validate signature, issuer, audience, nonce, and time claims via JWKS."""

        if not isinstance(id_token, str) or not id_token:
            raise MicrosoftOidcValidationError("Microsoft ID token is missing")
        try:
            header = jwt.get_unverified_header(id_token)
            unverified_claims = jwt.get_unverified_claims(id_token)
        except JWTError as exc:
            raise MicrosoftOidcValidationError("Microsoft ID token is malformed") from exc

        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise MicrosoftOidcValidationError("Microsoft ID token signing algorithm is invalid")
        tenant_id = _bounded_string(unverified_claims.get("tid"), maximum=255)
        if tenant_id is None:
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is missing")
        if self.is_multi_tenant:
            if not _TENANT_GUID_RE.fullmatch(tenant_id):
                raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")
        elif tenant_id.casefold() != self.tenant_id.casefold():
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")

        expected_issuer = (
            f"{MICROSOFT_AUTH_BASE}/{tenant_id}/v2.0"
            if self.is_multi_tenant
            else self.expected_issuer
        )
        await self.discovery()
        jwks = await self._get_json(self.jwks_uri, operation="JWKS")
        allowed_key_issuers = {expected_issuer}
        if self.is_multi_tenant:
            allowed_key_issuers.add(_MICROSOFT_ISSUER_TEMPLATE)
        key = self._select_signing_key(
            jwks,
            header["kid"],
            allowed_issuers=allowed_key_issuers,
        )
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=expected_issuer,
                options={
                    "require_exp": True,
                    "require_sub": True,
                    "require_iss": True,
                    "require_aud": True,
                },
            )
        except JWTError as exc:
            raise MicrosoftOidcValidationError("Microsoft ID token validation failed") from exc

        signed_nonce = claims.get("nonce")
        if not isinstance(signed_nonce, str) or not hmac.compare_digest(signed_nonce, nonce):
            raise MicrosoftOidcValidationError("Microsoft ID token nonce is invalid")
        signed_tenant_id = claims.get("tid")
        if not isinstance(signed_tenant_id, str):
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")
        if signed_tenant_id.casefold() != tenant_id.casefold():
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")
        return claims

    async def _get_json(self, url: str, *, operation: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(url)
        except (httpx.HTTPError, TimeoutError) as exc:
            logger.warning("Microsoft OIDC %s request failed", operation)
            raise MicrosoftOidcProviderError("Microsoft OIDC request failed") from exc
        if response.status_code != 200:
            logger.warning("Microsoft OIDC %s request returned status %s", operation, response.status_code)
            raise MicrosoftOidcProviderError("Microsoft OIDC request failed")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise MicrosoftOidcProviderError("Microsoft OIDC response was invalid") from exc
        if not isinstance(payload, dict):
            raise MicrosoftOidcProviderError("Microsoft OIDC response was invalid")
        return payload

    async def _post_form(
        self,
        url: str,
        *,
        data: Mapping[str, str],
        operation: str,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(
                    url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except (httpx.HTTPError, TimeoutError) as exc:
            logger.warning("Microsoft OIDC %s request failed", operation)
            raise MicrosoftOidcProviderError("Microsoft OIDC request failed") from exc
        if response.status_code != 200:
            logger.warning("Microsoft OIDC %s request returned status %s", operation, response.status_code)
            raise MicrosoftOidcProviderError("Microsoft OIDC request failed")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise MicrosoftOidcProviderError("Microsoft OIDC response was invalid") from exc
        if not isinstance(payload, dict):
            raise MicrosoftOidcProviderError("Microsoft OIDC response was invalid")
        return payload

    @classmethod
    def _require_microsoft_url(cls, value: str, *, expected: str) -> None:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise MicrosoftOidcProviderError("Microsoft OIDC metadata URL is invalid") from exc
        expected_parts = urlsplit(expected)
        if (
            parsed.scheme != "https"
            or parsed.netloc != _MICROSOFT_HOST
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != expected_parts.path
        ):
            raise MicrosoftOidcProviderError("Microsoft OIDC metadata URL is invalid")

    def _validate_discovery_issuer(self, value: str) -> None:
        if self.is_multi_tenant:
            expected = _MICROSOFT_ISSUER_TEMPLATE
        else:
            expected = self.expected_issuer
        self._require_microsoft_url(value, expected=expected)

    @staticmethod
    def _select_signing_key(
        jwks: Mapping[str, Any],
        kid: str,
        *,
        allowed_issuers: set[str],
    ) -> dict[str, Any]:
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise MicrosoftOidcValidationError("Microsoft OIDC signing keys are missing")
        for key in keys:
            if not isinstance(key, dict) or key.get("kid") != kid:
                continue
            if key.get("kty") != "RSA" or key.get("use") not in (None, "sig"):
                break
            if key.get("alg") not in (None, "RS256"):
                break
            key_issuer = key.get("issuer")
            if key_issuer is not None and (
                not isinstance(key_issuer, str) or key_issuer not in allowed_issuers
            ):
                break
            return key
        raise MicrosoftOidcValidationError("Microsoft OIDC signing key is unavailable")


def identity_from_claims(claims: Mapping[str, Any]) -> MicrosoftOidcIdentity:
    """Reduce validated claims to the fields needed for local account linking."""

    subject = _bounded_string(claims.get("sub"), maximum=255)
    tenant_id = _bounded_string(claims.get("tid"), maximum=255)
    if subject is None or tenant_id is None:
        raise MicrosoftOidcValidationError("Microsoft identity claims are incomplete")

    email = None
    for candidate in (claims.get("email"), claims.get("preferred_username"), claims.get("upn")):
        candidate = _bounded_string(candidate, maximum=320)
        if candidate is None:
            continue
        try:
            email = auth_service.normalize_email(candidate)
        except ValueError:
            continue
        break

    full_name = _bounded_string(claims.get("name"), maximum=200)
    picture = _bounded_string(claims.get("picture"), maximum=2048)
    if picture is not None and urlsplit(picture).scheme not in {"http", "https"}:
        picture = None
    return MicrosoftOidcIdentity(
        subject=subject,
        tenant_id=tenant_id,
        email=email,
        full_name=full_name,
        picture=picture,
    )


def _find_external_identity(db: Session, identity: MicrosoftOidcIdentity) -> ExternalIdentity | None:
    return db.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == ExternalIdentityProvider.MICROSOFT,
            ExternalIdentity.tenant_id == identity.tenant_id,
            ExternalIdentity.subject == identity.subject,
        )
    ).first()


def _touch_external_identity(
    db: Session,
    external: ExternalIdentity,
    identity: MicrosoftOidcIdentity,
) -> None:
    if identity.email is not None:
        external.email = identity.email
    external.last_login_at = datetime.utcnow()
    db.add(external)


def resolve_or_create_user(db: Session, identity: MicrosoftOidcIdentity) -> User:
    """Resolve an external identity or create a new external-only local user.

    Anonymous OIDC login never links to an existing local email. Linking an
    existing account requires the authenticated settings flow below.
    """

    external = _find_external_identity(db, identity)
    if external is not None:
        user = db.get(User, external.user_id)
        if user is None:
            raise MicrosoftOidcValidationError("Microsoft identity owner is missing")
        if not user.is_active:
            raise auth_service.InactiveUserError
        _touch_external_identity(db, external, identity)
        return user

    if identity.email is None:
        raise MicrosoftOidcValidationError("Microsoft identity email is unavailable")

    user = db.exec(
        select(User).where(func.lower(User.email) == identity.email)
    ).first()
    if user is not None:
        raise MicrosoftOidcAccountConflictError

    user = User(
        email=identity.email,
        full_name=identity.full_name,
        picture=identity.picture,
        password_hash=None,
        supabase_id=None,
        is_active=True,
    )
    db.add(user)
    db.flush()

    external = ExternalIdentity(
        user_id=user.id,
        provider=ExternalIdentityProvider.MICROSOFT,
        subject=identity.subject,
        tenant_id=identity.tenant_id,
        email=identity.email,
    )
    db.add(external)
    db.flush()
    return user


def link_external_identity(
    db: Session,
    identity: MicrosoftOidcIdentity,
    user: User,
) -> User:
    """Link a validated external identity after an authenticated user action."""

    if user.id is None:
        raise MicrosoftOidcValidationError("Microsoft link owner is missing")
    if not user.is_active:
        raise auth_service.InactiveUserError

    external = _find_external_identity(db, identity)
    if external is not None:
        if external.user_id != user.id:
            raise MicrosoftOidcExternalIdentityConflictError
        _touch_external_identity(db, external, identity)
        return user

    external = ExternalIdentity(
        user_id=user.id,
        provider=ExternalIdentityProvider.MICROSOFT,
        subject=identity.subject,
        tenant_id=identity.tenant_id,
        email=identity.email,
    )
    db.add(external)
    db.flush()
    return user
