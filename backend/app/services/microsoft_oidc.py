# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Microsoft Entra public-client OIDC verification and identity resolution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hmac
import logging
import threading
import time
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx
from jose import JWTError, jwt
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import (
    MICROSOFT_TENANT_ALIASES,
    MICROSOFT_TENANT_GUID_RE,
    settings,
    validate_microsoft_tenant_id,
)
from app.models import (
    ExternalIdentity,
    ExternalIdentityProvider,
    MicrosoftOidcConfiguration,
    User,
)
from app.services import auth as auth_service
from app.services.microsoft_oidc_configuration import (
    is_microsoft_oidc_runtime_configured,
)


logger = logging.getLogger(__name__)

MICROSOFT_AUTH_BASE = "https://login.microsoftonline.com"
OIDC_SCOPES: tuple[str, ...] = ("openid", "profile", "email")
OIDC_SCOPE_STRING = " ".join(OIDC_SCOPES)
_MICROSOFT_HOST = "login.microsoftonline.com"
_MULTI_TENANT_IDS = MICROSOFT_TENANT_ALIASES
_MICROSOFT_ISSUER_TEMPLATE = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
_TENANT_GUID_RE = MICROSOFT_TENANT_GUID_RE
_MAX_ID_TOKEN_LENGTH = 32_768
# Keep provider metadata warm without allowing stale discovery/JWKS to live
# indefinitely. Unknown-kid refreshes are deliberately slower than normal
# cache expiry so a structurally valid but forged token cannot amplify calls.
MICROSOFT_OIDC_DISCOVERY_TTL_SECONDS = 300.0
MICROSOFT_OIDC_JWKS_TTL_SECONDS = 300.0
MICROSOFT_OIDC_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 30.0
MICROSOFT_OIDC_FAILURE_COOLDOWN_SECONDS = 30.0


@dataclass(frozen=True)
class _JsonCacheEntry:
    value: dict[str, Any]
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class _JsonFailureEntry:
    failed_at: float
    failure_type: str


_CACHE_STATE_LOCK = threading.RLock()
_JSON_CACHE: dict[tuple[str, str], _JsonCacheEntry] = {}
_JSON_FAILURES: dict[tuple[str, str], _JsonFailureEntry] = {}
_UNKNOWN_KID_REFRESHED_AT: dict[str, float] = {}
_ASYNC_CACHE_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}


def clear_microsoft_oidc_caches() -> None:
    """Clear process-local OIDC caches for tests and controlled key rotation."""

    with _CACHE_STATE_LOCK:
        _JSON_CACHE.clear()
        _JSON_FAILURES.clear()
        _UNKNOWN_KID_REFRESHED_AT.clear()


def _cache_lock(kind: str, url: str) -> asyncio.Lock:
    """Return a per-event-loop lock so async tests do not share loop-bound locks."""

    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, kind, url)
    with _CACHE_STATE_LOCK:
        lock = _ASYNC_CACHE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _ASYNC_CACHE_LOCKS[key] = lock
        return lock


def _record_unknown_kid_refresh(url: str) -> None:
    """Record a provider fetch so the next unknown-kid failure is throttled."""

    with _CACHE_STATE_LOCK:
        _UNKNOWN_KID_REFRESHED_AT[url] = time.monotonic()


def _record_cache_failure(kind: str, url: str, failure_type: str) -> None:
    """Store only bounded failure metadata and discard any stale success."""

    with _CACHE_STATE_LOCK:
        _JSON_CACHE.pop((kind, url), None)
        _JSON_FAILURES[(kind, url)] = _JsonFailureEntry(
            failed_at=time.monotonic(),
            failure_type=failure_type,
        )


def _raise_if_cache_failure(kind: str, url: str) -> None:
    now = time.monotonic()
    cache_key = (kind, url)
    with _CACHE_STATE_LOCK:
        failure = _JSON_FAILURES.get(cache_key)
        if failure is None:
            return
        if now - failure.failed_at >= MICROSOFT_OIDC_FAILURE_COOLDOWN_SECONDS:
            _JSON_FAILURES.pop(cache_key, None)
            return
    raise _CachedProviderFailure("Microsoft OIDC provider temporarily unavailable")


class MicrosoftOidcError(Exception):
    """Base class for errors that are safe to collapse into a browser error."""


class MicrosoftOidcConfigurationError(MicrosoftOidcError):
    """Raised when the stored public OIDC configuration is unsafe or incomplete."""


class MicrosoftOidcProviderError(MicrosoftOidcError):
    """Raised when Microsoft discovery or JWKS retrieval cannot complete."""


class _CachedProviderFailure(MicrosoftOidcProviderError):
    """Internal marker for a local negative-cache rejection."""


class _MicrosoftOidcFetchError(MicrosoftOidcProviderError):
    """Safe provider failure carrying only a non-sensitive failure category."""

    def __init__(self, message: str, failure_type: str) -> None:
        super().__init__(message)
        self.failure_type = failure_type


class MicrosoftOidcValidationError(MicrosoftOidcError):
    """Raised when an ID token does not satisfy the Microsoft OIDC contract."""


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


def is_microsoft_oidc_configured(
    configuration: MicrosoftOidcConfiguration | None = None,
) -> bool:
    """Compatibility helper for callers that already loaded the DB row.

    OIDC readiness is intentionally database-backed. It never consults the
    Graph client secret or any other deployment-managed confidential setting.
    """

    return is_microsoft_oidc_runtime_configured(configuration)


def _bounded_string(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum:
        return None
    return value


class MicrosoftOidcClient:
    """Verify browser-issued Microsoft ID tokens using discovery and JWKS.

    This is a public-client verifier. It deliberately has no redirect URI,
    authorization-code exchange, or client-secret parameter. The browser uses
    MSAL with PKCE; the API only receives and validates the resulting ID token.
    """

    def __init__(
        self,
        *,
        client_id: str,
        tenant_id: str,
        http_timeout: float = 10.0,
    ) -> None:
        self.client_id = client_id.strip() if isinstance(client_id, str) else ""
        self.http_timeout = http_timeout
        if not self.client_id:
            raise MicrosoftOidcConfigurationError("Microsoft OIDC is not configured")
        try:
            self.tenant_id = validate_microsoft_tenant_id(tenant_id)
        except (TypeError, ValueError) as exc:
            raise MicrosoftOidcConfigurationError("Microsoft tenant is invalid") from exc
        if http_timeout <= 0 or http_timeout > 60:
            raise MicrosoftOidcConfigurationError("Microsoft OIDC timeout is invalid")

    @classmethod
    def from_configuration(
        cls,
        configuration: MicrosoftOidcConfiguration,
    ) -> "MicrosoftOidcClient":
        if not is_microsoft_oidc_runtime_configured(configuration):
            raise MicrosoftOidcConfigurationError("Microsoft OIDC is not configured")
        return cls(
            client_id=configuration.client_id,
            tenant_id=configuration.tenant_id,
            http_timeout=settings.MICROSOFT_OIDC_HTTP_TIMEOUT_SECONDS,
        )

    @property
    def authority(self) -> str:
        return f"{self._tenant_base}/v2.0"

    @property
    def _tenant_base(self) -> str:
        return f"{MICROSOFT_AUTH_BASE}/{quote(self.tenant_id, safe='')}"

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
        """Fetch metadata and pin every security-sensitive endpoint."""

        metadata, _ = await self._get_cached_json(
            kind="discovery",
            url=self.discovery_url,
            ttl_seconds=MICROSOFT_OIDC_DISCOVERY_TTL_SECONDS,
            operation="discovery",
        )
        try:
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
                if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                    raise MicrosoftOidcProviderError("Microsoft OIDC metadata is incomplete")
            # These values are inspected for integrity, but all runtime requests
            # remain constructed from MICROSOFT_AUTH_BASE and the stored tenant.
            self._require_microsoft_url(
                metadata["authorization_endpoint"],
                expected=f"{self._tenant_base}/oauth2/v2.0/authorize",
            )
            self._require_microsoft_url(
                metadata["token_endpoint"],
                expected=f"{self._tenant_base}/oauth2/v2.0/token",
            )
            self._require_microsoft_url(metadata["jwks_uri"], expected=self.jwks_uri)
            self._validate_discovery_issuer(metadata["issuer"])
        except _CachedProviderFailure:
            raise
        except MicrosoftOidcProviderError:
            _record_cache_failure("discovery", self.discovery_url, "invalid_payload")
            raise
        return metadata

    async def validate_id_token(
        self,
        id_token: str,
        *,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        """Validate signature, issuer, audience, tenant, nonce, and time claims."""

        if (
            not isinstance(id_token, str)
            or not id_token
            or len(id_token) > _MAX_ID_TOKEN_LENGTH
        ):
            raise MicrosoftOidcValidationError("Microsoft ID token is invalid")
        try:
            header = jwt.get_unverified_header(id_token)
            unverified_claims = jwt.get_unverified_claims(id_token)
        except (JWTError, TypeError, ValueError) as exc:
            raise MicrosoftOidcValidationError("Microsoft ID token is malformed") from exc

        if (
            not isinstance(header, dict)
            or header.get("alg") != "RS256"
            or not isinstance(header.get("kid"), str)
        ):
            raise MicrosoftOidcValidationError("Microsoft ID token signing algorithm is invalid")
        if not isinstance(unverified_claims, dict):
            raise MicrosoftOidcValidationError("Microsoft ID token claims are invalid")

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
        jwks, fetched = await self._get_cached_json(
            kind="jwks",
            url=self.jwks_uri,
            ttl_seconds=MICROSOFT_OIDC_JWKS_TTL_SECONDS,
            operation="JWKS",
        )
        if not isinstance(jwks.get("keys"), list):
            _record_cache_failure("jwks", self.jwks_uri, "invalid_payload")
            raise MicrosoftOidcProviderError("Microsoft OIDC signing keys are missing")
        allowed_key_issuers = {expected_issuer}
        if self.is_multi_tenant:
            allowed_key_issuers.add(_MICROSOFT_ISSUER_TEMPLATE)
        try:
            key = self._select_signing_key(
                jwks,
                header["kid"],
                allowed_issuers=allowed_key_issuers,
            )
        except MicrosoftOidcValidationError:
            # The first fetch already checked the current key set. Only a
            # cached key set gets one bounded refresh attempt for rotation;
            # concurrent/rapid unknown-kid failures reuse the cached set.
            if fetched:
                _record_unknown_kid_refresh(self.jwks_uri)
            else:
                refreshed = await self._refresh_jwks_for_unknown_kid()
                jwks = refreshed
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
                    "require_iat": True,
                    "require_sub": True,
                    "require_iss": True,
                    "require_aud": True,
                },
            )
        except (JWTError, TypeError, ValueError) as exc:
            raise MicrosoftOidcValidationError("Microsoft ID token validation failed") from exc

        signed_nonce = _bounded_string(claims.get("nonce"), maximum=255)
        if signed_nonce is None:
            raise MicrosoftOidcValidationError("Microsoft ID token nonce is invalid")
        if nonce is not None and not hmac.compare_digest(signed_nonce, nonce):
            raise MicrosoftOidcValidationError("Microsoft ID token nonce is invalid")

        signed_tenant_id = claims.get("tid")
        if not isinstance(signed_tenant_id, str):
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")
        if signed_tenant_id.casefold() != tenant_id.casefold():
            raise MicrosoftOidcValidationError("Microsoft ID token tenant is invalid")
        return claims

    async def _get_cached_json(
        self,
        *,
        kind: str,
        url: str,
        ttl_seconds: float,
        operation: str,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Fetch JSON once per cache key and coalesce concurrent refreshes."""

        requested_at = time.monotonic()
        cache_key = (kind, url)
        _raise_if_cache_failure(kind, url)
        with _CACHE_STATE_LOCK:
            entry = _JSON_CACHE.get(cache_key)
            if entry is not None and entry.expires_at > requested_at and not force_refresh:
                return entry.value, False

        lock = _cache_lock(kind, url)
        async with lock:
            _raise_if_cache_failure(kind, url)
            with _CACHE_STATE_LOCK:
                entry = _JSON_CACHE.get(cache_key)
                # A force-refresh caller that waited for another caller uses
                # the newly populated entry instead of issuing a duplicate.
                if entry is not None and (
                    not force_refresh or entry.created_at > requested_at
                ):
                    return entry.value, False

            try:
                payload = await self._get_json(url, operation=operation)
            except MicrosoftOidcProviderError as exc:
                _record_cache_failure(
                    kind,
                    url,
                    getattr(exc, "failure_type", "provider"),
                )
                raise
            with _CACHE_STATE_LOCK:
                created_at = time.monotonic()
                _JSON_FAILURES.pop(cache_key, None)
                _JSON_CACHE[cache_key] = _JsonCacheEntry(
                    value=payload,
                    created_at=created_at,
                    expires_at=created_at + ttl_seconds,
                )
            return payload, True

    async def _refresh_jwks_for_unknown_kid(self) -> dict[str, Any]:
        """Refresh JWKS at most once per cooldown window for this URI."""

        _raise_if_cache_failure("jwks", self.jwks_uri)
        now = time.monotonic()
        force_refresh = False
        with _CACHE_STATE_LOCK:
            last_refresh = _UNKNOWN_KID_REFRESHED_AT.get(self.jwks_uri)
            if (
                last_refresh is not None
                and now - last_refresh < MICROSOFT_OIDC_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
            ):
                cached = _JSON_CACHE.get(("jwks", self.jwks_uri))
                if cached is not None:
                    return cached.value
                # A previous refresh may still be in-flight. The normal
                # coalesced path below will wait for it without bypassing the
                # cooldown.
            else:
                _UNKNOWN_KID_REFRESHED_AT[self.jwks_uri] = now
                force_refresh = True

        refreshed, _ = await self._get_cached_json(
            kind="jwks",
            url=self.jwks_uri,
            ttl_seconds=MICROSOFT_OIDC_JWKS_TTL_SECONDS,
            operation="JWKS",
            force_refresh=force_refresh,
        )
        return refreshed

    async def _get_json(self, url: str, *, operation: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(url)
        except (httpx.HTTPError, TimeoutError) as exc:
            logger.warning("Microsoft OIDC %s request failed", operation)
            raise _MicrosoftOidcFetchError(
                "Microsoft OIDC request failed",
                "network",
            ) from exc
        if response.status_code != 200:
            logger.warning(
                "Microsoft OIDC %s request returned status %s",
                operation,
                response.status_code,
            )
            raise _MicrosoftOidcFetchError(
                "Microsoft OIDC request failed",
                "status",
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise _MicrosoftOidcFetchError(
                "Microsoft OIDC response was invalid",
                "invalid_payload",
            ) from exc
        if not isinstance(payload, dict):
            raise _MicrosoftOidcFetchError(
                "Microsoft OIDC response was invalid",
                "invalid_payload",
            )
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
        expected = _MICROSOFT_ISSUER_TEMPLATE if self.is_multi_tenant else self.expected_issuer
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
    """Resolve an identity or create an external-only local user.

    Anonymous OIDC login never links to an existing local email. Linking an
    existing account requires the authenticated explicit-link endpoint.
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
    """Link a validated external identity after an explicit user action."""

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
