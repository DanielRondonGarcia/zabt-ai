# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""OAuth 2.1 services for the read-only Streamable HTTP MCP resource."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import unquote, urlsplit

import redis
from sqlmodel import Session, select

from app.core.config import is_loopback_hostname, settings
from app.db.engine import engine
from app.models import (
    MCPAccessToken,
    MCPClientRegistration,
    MCPGrant,
    MCPRefreshToken,
    User,
)


MCP_READ_SCOPE = "mcp:read"
MCP_OAUTH_RESPONSE_TYPE = "code"
MCP_OAUTH_AUTHORIZATION_CODE_GRANT = "authorization_code"
MCP_OAUTH_REFRESH_TOKEN_GRANT = "refresh_token"
MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD = "none"
MCP_OAUTH_CODE_CHALLENGE_METHOD = "S256"
MCP_OAUTH_REQUEST_TTL_SECONDS = 600
MCP_OAUTH_CODE_TTL_SECONDS = 60
MCP_OAUTH_RATE_LIMIT_WINDOW_SECONDS = 60
MCP_OAUTH_DCR_RATE_LIMIT = 20
MCP_OAUTH_AUTHORIZE_RATE_LIMIT = 30

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_OPAQUE_VALUE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_AUTHORIZATION_CODE_RE = re.compile(r"^zabt_mcp_code_[A-Za-z0-9_-]{32,128}$")
_NATIVE_SCHEME_RE = re.compile(
    r"^(?=.{3,63}$)(?:[a-z][a-z0-9+.-]*\.)+[a-z][a-z0-9+.-]*$"
)
_UNSAFE_CUSTOM_SCHEMES = frozenset(
    {
        "about",
        "blob",
        "chrome",
        "chrome-extension",
        "data",
        "file",
        "javascript",
        "mailto",
        "ms-appx",
        "ms-appx-web",
        "resource",
        "sms",
        "tel",
        "vbscript",
        "view-source",
    }
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class MCPOAuthError(ValueError):
    """A safe OAuth protocol error suitable for a client response."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(description)
        self.code = code
        self.description = description


class MCPAuthorizationStateError(RuntimeError):
    """Raised when Redis state cannot be safely persisted or consumed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_list(value: str, *, field: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MCPAuthorizationStateError(f"Malformed stored {field}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise MCPAuthorizationStateError(f"Malformed stored {field}")
    return parsed


def _safe_text(value: object, *, field: str, max_length: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if not value and not required:
        return value
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    return normalized


def _validate_absolute_url(
    value: object,
    *,
    field: str,
    max_length: int,
    allow_query: bool = False,
) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    url = _safe_text(value, field=field, max_length=max_length)
    if "*" in unquote(url) or "#" in url:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise MCPOAuthError("invalid_request", f"Invalid {field}") from exc
    if not parsed.scheme or not parsed.netloc:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if not allow_query and parsed.query:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if port is not None and not 1 <= port <= 65_535:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if parsed.scheme not in {"http", "https"}:
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    if (
        settings.AUTH_ENVIRONMENT != "development"
        and parsed.scheme.casefold() == "http"
        and not is_loopback_hostname(parsed.hostname)
    ):
        raise MCPOAuthError("invalid_request", f"Invalid {field}")
    return url


def validate_redirect_uri(value: object) -> str:
    """Validate a precise redirect URI without accepting open redirects."""

    if not isinstance(value, str) or value != value.strip():
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    uri = _safe_text(value, field="redirect_uri", max_length=2048)
    if "*" in unquote(uri) or "#" in uri:
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in uri):
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri") from exc
    if not parsed.scheme or parsed.fragment or parsed.username is not None or parsed.password is not None:
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    if port is not None and not 1 <= port <= 65_535:
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")

    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        if not parsed.hostname or (
            scheme == "http" and parsed.hostname.casefold() not in _LOOPBACK_HOSTS
        ):
            raise MCPOAuthError(
                "invalid_request",
                "HTTP redirect URIs must use a loopback host",
            )
        if not parsed.netloc:
            raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    elif (
        not _NATIVE_SCHEME_RE.fullmatch(scheme)
        or scheme in _UNSAFE_CUSTOM_SCHEMES
        or (not parsed.netloc and not parsed.path.startswith("/"))
    ):
        raise MCPOAuthError("invalid_request", "Invalid redirect_uri")
    return uri


def validate_resource_url(value: object) -> str:
    return _validate_absolute_url(
        value,
        field="resource",
        max_length=2048,
        allow_query=False,
    )


def mcp_resource_url() -> str:
    return validate_resource_url(settings.MCP_PUBLIC_URL.rstrip("/"))


def oauth_issuer_url() -> str:
    configured = settings.MCP_OAUTH_ISSUER.strip()
    issuer = configured or f"{mcp_resource_url()}/oauth"
    return _validate_absolute_url(
        issuer,
        field="MCP_OAUTH_ISSUER",
        max_length=2048,
        allow_query=False,
    ).rstrip("/")


def oauth_endpoint_url(path: str) -> str:
    return f"{oauth_issuer_url()}/{path.lstrip('/')}"


def build_pkce_challenge(code_verifier: str) -> str:
    if not isinstance(code_verifier, str) or not _PKCE_VERIFIER_RE.fullmatch(code_verifier):
        raise MCPOAuthError("invalid_grant", "Invalid PKCE code_verifier")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_scope(scope: object) -> str:
    if not isinstance(scope, str) or scope != scope.strip():
        raise MCPOAuthError("invalid_scope", "Only mcp:read is supported")
    normalized = _safe_text(scope, field="scope", max_length=64)
    if normalized == MCP_READ_SCOPE:
        return MCP_READ_SCOPE
    if normalized in {"read write", "write read"}:
        return MCP_READ_SCOPE
    raise MCPOAuthError("invalid_scope", "Only mcp:read is supported")


def _validate_client_id(client_id: object) -> str:
    normalized = _safe_text(client_id, field="client_id", max_length=128)
    if not _CLIENT_ID_RE.fullmatch(normalized):
        raise MCPOAuthError("invalid_client", "Invalid client_id")
    return normalized


def _validate_state(state: object | None) -> str | None:
    if state is None:
        return None
    return _safe_text(state, field="state", max_length=512, required=False) or None


def _validate_pkce_challenge(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise MCPOAuthError("invalid_request", "Only S256 PKCE challenges are supported")
    challenge = _safe_text(value, field="code_challenge", max_length=128)
    if not _PKCE_CHALLENGE_RE.fullmatch(challenge):
        raise MCPOAuthError("invalid_request", "Only S256 PKCE challenges are supported")
    return challenge


@dataclass(frozen=True)
class MCPAuthorizationRequest:
    request_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    state: str | None
    resource: str
    scopes: tuple[str, ...]
    user_id: int | None


@dataclass(frozen=True)
class MCPAuthorizationCode:
    transaction: MCPAuthorizationRequest
    grant_generation: int


@dataclass(frozen=True)
class MCPTokenPair:
    access_token: str
    token_type: str
    expires_in: int
    scope: str
    refresh_token: str | None = None


@dataclass(frozen=True)
class ResolvedMCPAccessToken:
    token_id: int
    grant_id: int
    client_id: str
    user_id: int
    resource: str
    scope: str
    expires_at: datetime


@dataclass(frozen=True)
class MCPLinkedGrant:
    grant_id: int
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    resource: str
    scope: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


_CONSUME_REQUEST_FOR_USER_LUA = """
local encoded = redis.call('GET', KEYS[1])
if not encoded then
    return nil
end
local payload = cjson.decode(encoded)
if tostring(payload.user_id) ~= ARGV[1] then
    return false
end
redis.call('DEL', KEYS[1])
return encoded
"""

_CONSUME_PENDING_REQUEST_LUA = """
local encoded = redis.call('GET', KEYS[1])
if not encoded then
    return nil
end
local payload = cjson.decode(encoded)
if payload.user_id ~= cjson.null then
    return false
end
redis.call('DEL', KEYS[1])
return encoded
"""


class MCPAuthorizationStateStore:
    """Store MCP authorization requests and codes as hashed, one-time Redis keys."""

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        redis_url: str | None = None,
        request_ttl_seconds: int = MCP_OAUTH_REQUEST_TTL_SECONDS,
        code_ttl_seconds: int = MCP_OAUTH_CODE_TTL_SECONDS,
    ) -> None:
        if request_ttl_seconds <= 0 or code_ttl_seconds <= 0:
            raise ValueError("OAuth state TTLs must be positive")
        self._redis = (
            redis_client
            if redis_client is not None
            else redis.Redis.from_url(
                redis_url or settings.REDIS_URL,
                decode_responses=True,
            )
        )
        self.request_ttl_seconds = request_ttl_seconds
        self.code_ttl_seconds = code_ttl_seconds

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    @classmethod
    def _request_key(cls, request_id: str) -> str:
        return f"zabt:mcp-oauth:request:{cls._digest(request_id)}"

    @classmethod
    def _code_key(cls, code: str) -> str:
        return f"zabt:mcp-oauth:code:{cls._digest(code)}"

    @staticmethod
    def _payload(transaction: MCPAuthorizationRequest) -> dict[str, Any]:
        return {
            "client_id": transaction.client_id,
            "redirect_uri": transaction.redirect_uri,
            "code_challenge": transaction.code_challenge,
            "code_challenge_method": MCP_OAUTH_CODE_CHALLENGE_METHOD,
            "state": transaction.state,
            "resource": transaction.resource,
            "scopes": list(transaction.scopes),
            "user_id": transaction.user_id,
        }

    def create_request(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        state: str | None,
        resource: str,
        scopes: tuple[str, ...],
        user_id: int | None,
    ) -> MCPAuthorizationRequest:
        request_id = secrets.token_urlsafe(32)
        transaction = MCPAuthorizationRequest(
            request_id=request_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            resource=resource,
            scopes=scopes,
            user_id=user_id,
        )
        try:
            written = self._redis.set(
                self._request_key(request_id),
                json.dumps(self._payload(transaction), separators=(",", ":")),
                ex=self.request_ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        if not written:
            raise MCPAuthorizationStateError("Could not reserve OAuth request")
        return transaction

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any] | None:
        if raw is None or raw is False or raw == 0 or raw == b"" or raw == "":
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _parse(payload: dict[str, Any], request_id: str) -> MCPAuthorizationRequest | None:
        try:
            client_id = _validate_client_id(payload.get("client_id"))
            redirect_uri = validate_redirect_uri(payload.get("redirect_uri"))
            code_challenge = _validate_pkce_challenge(payload.get("code_challenge"))
            if payload.get("code_challenge_method") != MCP_OAUTH_CODE_CHALLENGE_METHOD:
                return None
            state = _validate_state(payload.get("state"))
            resource = validate_resource_url(payload.get("resource"))
            raw_scopes = payload.get("scopes")
            if not isinstance(raw_scopes, list) or raw_scopes != [MCP_READ_SCOPE]:
                return None
            if "user_id" not in payload:
                return None
            user_id = payload.get("user_id")
            if user_id is not None and (
                isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
            ):
                return None
        except MCPOAuthError:
            return None
        return MCPAuthorizationRequest(
            request_id=request_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            resource=resource,
            scopes=(MCP_READ_SCOPE,),
            user_id=user_id,
        )

    @classmethod
    def _parse_code(cls, payload: dict[str, Any], request_id: str) -> MCPAuthorizationCode | None:
        transaction = cls._parse(payload, request_id)
        grant_generation = payload.get("grant_generation")
        if (
            transaction is None
            or transaction.user_id is None
            or isinstance(grant_generation, bool)
            or not isinstance(grant_generation, int)
            or grant_generation < 0
        ):
            return None
        return MCPAuthorizationCode(
            transaction=transaction,
            grant_generation=grant_generation,
        )

    def consume_request_for_user(
        self,
        request_id: str,
        *,
        user_id: int,
    ) -> MCPAuthorizationRequest | None:
        if not isinstance(request_id, str) or not _OPAQUE_VALUE_RE.fullmatch(request_id):
            return None
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return None
        try:
            raw = self._redis.eval(
                _CONSUME_REQUEST_FOR_USER_LUA,
                1,
                self._request_key(request_id),
                str(user_id),
            )
        except AttributeError:
            # Test doubles and old Redis clients may not expose EVAL. A
            # read-before-GETDEL fallback still preserves owner rejection and
            # uses GETDEL for the one-time consume race.
            try:
                key = self._request_key(request_id)
                try:
                    preview = self._redis.get(key)
                except AttributeError:
                    raw = self._redis.getdel(key)
                else:
                    preview_payload = self._decode(preview)
                    preview_transaction = (
                        self._parse(preview_payload, request_id)
                        if preview_payload is not None
                        else None
                    )
                    if preview_transaction is None or preview_transaction.user_id != user_id:
                        return None
                    raw = self._redis.getdel(key)
            except Exception as exc:
                raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        payload = self._decode(raw)
        if payload is None:
            return None
        transaction = self._parse(payload, request_id)
        if transaction is None or transaction.user_id != user_id:
            return None
        return transaction

    def consume_pending_request(self, request_id: str) -> MCPAuthorizationRequest | None:
        """Consume an unbound browser authorization request exactly once."""

        if not isinstance(request_id, str) or not _OPAQUE_VALUE_RE.fullmatch(request_id):
            return None
        try:
            raw = self._redis.eval(
                _CONSUME_PENDING_REQUEST_LUA,
                1,
                self._request_key(request_id),
                "__pending__",
            )
        except AttributeError:
            # Test doubles and old Redis clients may not expose EVAL. The
            # read-before-GETDEL fallback still rejects already-bound requests.
            try:
                key = self._request_key(request_id)
                try:
                    preview = self._redis.get(key)
                except AttributeError:
                    raw = self._redis.getdel(key)
                else:
                    preview_payload = self._decode(preview)
                    preview_transaction = (
                        self._parse(preview_payload, request_id)
                        if preview_payload is not None
                        else None
                    )
                    if preview_transaction is None or preview_transaction.user_id is not None:
                        return None
                    raw = self._redis.getdel(key)
            except Exception as exc:
                raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        payload = self._decode(raw)
        if payload is None:
            return None
        transaction = self._parse(payload, request_id)
        if transaction is None or transaction.user_id is not None:
            return None
        return transaction

    def get_request(self, request_id: str) -> MCPAuthorizationRequest | None:
        """Read a pending authorization request without consuming it."""

        if not isinstance(request_id, str) or not _OPAQUE_VALUE_RE.fullmatch(request_id):
            return None
        try:
            raw = self._redis.get(self._request_key(request_id))
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth state store unavailable") from exc
        payload = self._decode(raw)
        if payload is None:
            return None
        transaction = self._parse(payload, request_id)
        if transaction is None or transaction.user_id is not None:
            return None
        return transaction

    def issue_code(self, transaction: MCPAuthorizationRequest, *, grant_generation: int) -> str:
        if isinstance(grant_generation, bool) or not isinstance(grant_generation, int) or grant_generation < 0:
            raise MCPAuthorizationStateError("Invalid OAuth grant generation")
        raw_code = f"zabt_mcp_code_{secrets.token_urlsafe(48)}"
        payload = self._payload(transaction)
        payload["grant_generation"] = grant_generation
        try:
            written = self._redis.set(
                self._code_key(raw_code),
                json.dumps(payload, separators=(",", ":")),
                ex=self.code_ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth code store unavailable") from exc
        if not written:
            raise MCPAuthorizationStateError("Could not reserve OAuth code")
        return raw_code

    def consume_code(self, raw_code: str) -> MCPAuthorizationCode | None:
        if not isinstance(raw_code, str) or not _AUTHORIZATION_CODE_RE.fullmatch(raw_code):
            return None
        try:
            raw = self._redis.getdel(self._code_key(raw_code))
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth code store unavailable") from exc
        payload = self._decode(raw)
        if payload is None:
            return None
        return self._parse_code(payload, "")

    def allow_attempt(self, category: str, identifier: str, *, limit: int) -> bool:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        key = f"zabt:mcp-oauth:rate:{category}:{digest}"
        try:
            count = int(self._redis.incr(key))
            if count == 1:
                self._redis.expire(key, MCP_OAUTH_RATE_LIMIT_WINDOW_SECONDS)
        except Exception as exc:
            raise MCPAuthorizationStateError("OAuth rate limiter unavailable") from exc
        return count <= limit


class MCPOAuthService:
    """Repository and protocol operations for MCP OAuth."""

    def __init__(
        self,
        db_engine=engine,
        state_store: MCPAuthorizationStateStore | None = None,
    ) -> None:
        self.db_engine = db_engine
        self.state_store = state_store or MCPAuthorizationStateStore()

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _registration_metadata(registration: MCPClientRegistration) -> dict[str, Any]:
        redirect_uris = _json_list(registration.redirect_uris_json, field="redirect URIs")
        grant_types = _json_list(registration.grant_types_json, field="grant types")
        return {
            "client_id": registration.client_id,
            "client_id_issued_at": int(_as_utc(registration.created_at).timestamp()),
            "redirect_uris": redirect_uris,
            "response_types": [MCP_OAUTH_RESPONSE_TYPE],
            "grant_types": grant_types,
            "token_endpoint_auth_method": MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD,
            "scope": registration.scope,
            "code_challenge_methods_supported": [MCP_OAUTH_CODE_CHALLENGE_METHOD],
            "client_name": registration.client_name,
        }

    @staticmethod
    def _validate_registration_metadata(
        *,
        client_name: object,
        redirect_uris: object,
        response_types: object,
        grant_types: object,
        token_endpoint_auth_method: object,
        scope: object,
        code_challenge_methods_supported: object,
    ) -> tuple[str, list[str], list[str], str]:
        normalized_name = _safe_text(client_name, field="client_name", max_length=100)
        if not isinstance(redirect_uris, list) or not 1 <= len(redirect_uris) <= 10:
            raise MCPOAuthError("invalid_client_metadata", "Invalid redirect_uris")
        normalized_redirects = [validate_redirect_uri(uri) for uri in redirect_uris]
        if len(set(normalized_redirects)) != len(normalized_redirects):
            raise MCPOAuthError("invalid_client_metadata", "redirect_uris must be unique")
        if sum(len(uri) for uri in normalized_redirects) > 16_384:
            raise MCPOAuthError("invalid_client_metadata", "redirect_uris are too large")

        if response_types is None:
            response_types = [MCP_OAUTH_RESPONSE_TYPE]
        if response_types != [MCP_OAUTH_RESPONSE_TYPE]:
            raise MCPOAuthError("invalid_client_metadata", "Only response_type=code is supported")

        if grant_types is None:
            grant_types = [MCP_OAUTH_AUTHORIZATION_CODE_GRANT]
        if (
            not isinstance(grant_types, list)
            or not 1 <= len(grant_types) <= 2
            or not all(isinstance(grant_type, str) for grant_type in grant_types)
        ):
            raise MCPOAuthError("invalid_client_metadata", "Invalid grant_types")
        if MCP_OAUTH_AUTHORIZATION_CODE_GRANT not in grant_types or len(set(grant_types)) != len(grant_types):
            raise MCPOAuthError("invalid_client_metadata", "authorization_code is required")
        if any(
            grant_type not in {
                MCP_OAUTH_AUTHORIZATION_CODE_GRANT,
                MCP_OAUTH_REFRESH_TOKEN_GRANT,
            }
            for grant_type in grant_types
        ):
            raise MCPOAuthError("invalid_client_metadata", "Unsupported grant type")
        grant_types = [
            MCP_OAUTH_AUTHORIZATION_CODE_GRANT,
            *(
                [MCP_OAUTH_REFRESH_TOKEN_GRANT]
                if MCP_OAUTH_REFRESH_TOKEN_GRANT in grant_types
                else []
            ),
        ]

        if token_endpoint_auth_method != MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD:
            raise MCPOAuthError(
                "invalid_client_metadata",
                "Only public clients with token_endpoint_auth_method=none are supported",
            )
        normalized_scope = _validate_scope(scope if scope is not None else MCP_READ_SCOPE)
        if code_challenge_methods_supported is None:
            code_challenge_methods_supported = [MCP_OAUTH_CODE_CHALLENGE_METHOD]
        if code_challenge_methods_supported != [MCP_OAUTH_CODE_CHALLENGE_METHOD]:
            raise MCPOAuthError("invalid_client_metadata", "Only S256 PKCE is supported")
        return normalized_name, normalized_redirects, grant_types, normalized_scope

    def register_client(
        self,
        *,
        client_name: object = "MCP client",
        redirect_uris: object,
        response_types: object = None,
        grant_types: object = None,
        token_endpoint_auth_method: object = MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD,
        scope: object = MCP_READ_SCOPE,
        code_challenge_methods_supported: object = None,
    ) -> dict[str, Any]:
        normalized_name, normalized_redirects, normalized_grants, normalized_scope = (
            self._validate_registration_metadata(
                client_name=client_name,
                redirect_uris=redirect_uris,
                response_types=response_types,
                grant_types=grant_types,
                token_endpoint_auth_method=token_endpoint_auth_method,
                scope=scope,
                code_challenge_methods_supported=code_challenge_methods_supported,
            )
        )

        with Session(self.db_engine) as session:
            registration = MCPClientRegistration(
                client_id=f"zabt_mcp_client_{secrets.token_urlsafe(32)}",
                client_name=normalized_name,
                redirect_uris_json=json.dumps(normalized_redirects, separators=(",", ":")),
                grant_types_json=json.dumps(normalized_grants, separators=(",", ":")),
                scope=normalized_scope,
                created_at=_utc_now(),
            )
            session.add(registration)
            session.commit()
            session.refresh(registration)
            return self._registration_metadata(registration)

    def get_registration(self, client_id: object) -> MCPClientRegistration | None:
        try:
            normalized = _validate_client_id(client_id)
        except MCPOAuthError:
            return None
        with Session(self.db_engine) as session:
            registration = session.exec(
                select(MCPClientRegistration).where(
                    MCPClientRegistration.client_id == normalized,
                    MCPClientRegistration.revoked_at.is_(None),
                )
            ).first()
            if registration is None:
                return None
            # Return a detached scalar-only object; JSON metadata is eagerly stored.
            return registration

    def client_metadata(self, client_id: object) -> dict[str, Any] | None:
        registration = self.get_registration(client_id)
        return self._registration_metadata(registration) if registration is not None else None

    @staticmethod
    def _supports_refresh(registration: MCPClientRegistration) -> bool:
        return MCP_OAUTH_REFRESH_TOKEN_GRANT in _json_list(
            registration.grant_types_json,
            field="grant types",
        )

    def create_authorization_request(
        self,
        *,
        client_id: object,
        redirect_uri: object,
        response_type: object,
        code_challenge: object,
        code_challenge_method: object,
        state: object | None,
        resource: object,
        scope: object,
        user_id: int | None,
    ) -> MCPAuthorizationRequest:
        normalized_client_id = _validate_client_id(client_id)
        if response_type != MCP_OAUTH_RESPONSE_TYPE:
            raise MCPOAuthError("unsupported_response_type", "Only response_type=code is supported")
        if code_challenge_method != MCP_OAUTH_CODE_CHALLENGE_METHOD:
            raise MCPOAuthError("invalid_request", "Only S256 PKCE is supported")
        normalized_redirect = validate_redirect_uri(redirect_uri)
        normalized_challenge = _validate_pkce_challenge(code_challenge)
        normalized_resource = validate_resource_url(resource)
        if normalized_resource != mcp_resource_url():
            raise MCPOAuthError("invalid_target", "The resource is not this MCP server")
        normalized_scope = _validate_scope(scope)
        normalized_state = _validate_state(state)

        with Session(self.db_engine) as session:
            registration = session.exec(
                select(MCPClientRegistration).where(
                    MCPClientRegistration.client_id == normalized_client_id,
                    MCPClientRegistration.revoked_at.is_(None),
                )
            ).first()
            if registration is None:
                raise MCPOAuthError("invalid_client", "Unknown client_id")
            registered_redirects = _json_list(
                registration.redirect_uris_json,
                field="redirect URIs",
            )
            if normalized_redirect not in registered_redirects:
                raise MCPOAuthError("invalid_request", "redirect_uri does not match registration")
            if registration.scope != normalized_scope:
                raise MCPOAuthError("invalid_scope", "Requested scope is not registered")
            if user_id is not None:
                if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
                    raise MCPOAuthError("login_required", "An authenticated user is required")
                user = session.get(User, user_id)
                if user is None or not user.is_active:
                    raise MCPOAuthError("login_required", "An authenticated user is required")

        return self.state_store.create_request(
            client_id=normalized_client_id,
            redirect_uri=normalized_redirect,
            code_challenge=normalized_challenge,
            state=normalized_state,
            resource=normalized_resource,
            scopes=(normalized_scope,),
            user_id=user_id,
        )

    def approve_authorization_request(
        self,
        request_id: str,
        *,
        user_id: int,
        approved: bool,
    ) -> tuple[MCPAuthorizationRequest, str | None]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise MCPOAuthError("login_required", "An authenticated user is required")

        transaction = self.state_store.consume_pending_request(request_id)
        if transaction is None:
            # Accept a request created by the previous phase while it remains
            # in Redis, but never allow it to be approved by another user.
            transaction = self.state_store.consume_request_for_user(request_id, user_id=user_id)
        if transaction is None:
            raise MCPOAuthError("invalid_request", "Authorization request is invalid or expired")
        bound_transaction = replace(transaction, user_id=user_id)
        if not approved:
            return bound_transaction, None
        try:
            grant_generation = self._prepare_grant_for_approval(bound_transaction)
            return bound_transaction, self.state_store.issue_code(
                bound_transaction,
                grant_generation=grant_generation,
            )
        except MCPAuthorizationStateError:
            raise

    def _prepare_grant_for_approval(self, transaction: MCPAuthorizationRequest) -> int:
        if transaction.user_id is None:
            raise MCPOAuthError("invalid_request", "Authorization request is invalid")
        with Session(self.db_engine) as session:
            registration = session.exec(
                select(MCPClientRegistration).where(
                    MCPClientRegistration.client_id == transaction.client_id,
                    MCPClientRegistration.revoked_at.is_(None),
                )
            ).first()
            user = session.get(User, transaction.user_id)
            if registration is None or user is None or not user.is_active:
                raise MCPOAuthError("invalid_request", "Authorization request is invalid")
            grant = self._get_or_create_grant(
                session,
                registration=registration,
                user_id=transaction.user_id,
                resource=transaction.resource,
                scope=transaction.scopes[0],
                allow_reactivation=True,
            )
            generation = int(grant.generation)
            session.commit()
            return generation

    def _get_or_create_grant(
        self,
        session: Session,
        *,
        registration: MCPClientRegistration,
        user_id: int,
        resource: str,
        scope: str,
        allow_reactivation: bool,
    ) -> MCPGrant:
        grant = session.exec(
            select(MCPGrant).where(
                MCPGrant.client_registration_id == registration.id,
                MCPGrant.user_id == user_id,
                MCPGrant.resource == resource,
            )
            .with_for_update()
        ).first()
        if grant is None:
            if not allow_reactivation:
                raise MCPOAuthError("invalid_grant", "Authorization grant is invalid")
            grant = MCPGrant(
                client_registration_id=registration.id,
                user_id=user_id,
                resource=resource,
                scope=scope,
                generation=0,
                created_at=_utc_now(),
            )
            session.add(grant)
            session.flush()
        elif grant.scope != scope:
            raise MCPOAuthError("invalid_scope", "Grant scope is not supported")
        elif allow_reactivation and grant.revoked_at is not None:
            grant.revoked_at = None
        return grant

    def _issue_token_pair(
        self,
        session: Session,
        *,
        registration: MCPClientRegistration,
        grant: MCPGrant,
        include_refresh: bool,
    ) -> MCPTokenPair:
        now = _utc_now()
        access_raw = f"zabt_mcp_at_{secrets.token_urlsafe(32)}"
        access_expires_at = now + timedelta(seconds=settings.MCP_OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS)
        access = MCPAccessToken(
            grant_id=grant.id,
            token_hash=self.hash_token(access_raw),
            token_prefix=access_raw[:24],
            created_at=now,
            expires_at=access_expires_at,
        )
        session.add(access)

        refresh_raw: str | None = None
        if include_refresh:
            refresh_raw = f"zabt_mcp_rt_{secrets.token_urlsafe(48)}"
            session.add(
                MCPRefreshToken(
                    grant_id=grant.id,
                    token_hash=self.hash_token(refresh_raw),
                    token_prefix=refresh_raw[:24],
                    created_at=now,
                    expires_at=now + timedelta(days=settings.MCP_OAUTH_REFRESH_TOKEN_EXPIRE_DAYS),
                )
            )
        grant.last_used_at = now
        session.add(grant)
        return MCPTokenPair(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=settings.MCP_OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS,
            scope=grant.scope,
            refresh_token=refresh_raw,
        )

    def exchange_authorization_code(
        self,
        *,
        code: str,
        client_id: object,
        redirect_uri: object,
        code_verifier: object,
        resource: object,
        scope: object | None = None,
    ) -> MCPTokenPair:
        authorization_code = self.state_store.consume_code(code)
        if authorization_code is None:
            raise MCPOAuthError("invalid_grant", "Authorization code is invalid or expired")
        transaction = authorization_code.transaction
        if transaction.user_id is None:
            raise MCPOAuthError("invalid_grant", "Authorization code is invalid")
        normalized_client_id = _validate_client_id(client_id)
        if transaction.client_id != normalized_client_id:
            raise MCPOAuthError("invalid_grant", "Authorization code binding is invalid")
        normalized_redirect = validate_redirect_uri(redirect_uri)
        if transaction.redirect_uri != normalized_redirect:
            raise MCPOAuthError("invalid_grant", "Authorization code binding is invalid")
        normalized_resource = validate_resource_url(resource)
        if transaction.resource != normalized_resource or normalized_resource != mcp_resource_url():
            raise MCPOAuthError("invalid_grant", "Authorization code resource is invalid")
        if scope is not None and _validate_scope(scope) != transaction.scopes[0]:
            raise MCPOAuthError("invalid_grant", "Authorization code scope is invalid")
        try:
            if not isinstance(code_verifier, str) or not _PKCE_VERIFIER_RE.fullmatch(code_verifier):
                raise MCPOAuthError("invalid_grant", "PKCE verification failed")
            expected_challenge = build_pkce_challenge(code_verifier)
        except MCPOAuthError as exc:
            raise MCPOAuthError("invalid_grant", "PKCE verification failed") from exc
        if not hmac.compare_digest(transaction.code_challenge, expected_challenge):
            raise MCPOAuthError("invalid_grant", "PKCE verification failed")

        with Session(self.db_engine) as session:
            registration = session.exec(
                select(MCPClientRegistration).where(
                    MCPClientRegistration.client_id == normalized_client_id,
                    MCPClientRegistration.revoked_at.is_(None),
                )
            ).first()
            user = session.get(User, transaction.user_id)
            if registration is None or user is None or not user.is_active:
                raise MCPOAuthError("invalid_grant", "Authorization code is invalid")
            if normalized_redirect not in _json_list(
                registration.redirect_uris_json,
                field="redirect URIs",
            ):
                raise MCPOAuthError("invalid_grant", "Authorization code binding is invalid")
            grant = self._get_or_create_grant(
                session,
                registration=registration,
                user_id=transaction.user_id,
                resource=normalized_resource,
                scope=transaction.scopes[0],
                allow_reactivation=False,
            )
            if (
                grant.generation != authorization_code.grant_generation
                or grant.revoked_at is not None
            ):
                raise MCPOAuthError("invalid_grant", "Authorization grant is invalid")
            pair = self._issue_token_pair(
                session,
                registration=registration,
                grant=grant,
                include_refresh=self._supports_refresh(registration),
            )
            session.commit()
            return pair

    def exchange_refresh_token(
        self,
        *,
        refresh_token: str,
        client_id: object,
        resource: object,
        scope: object | None = None,
    ) -> MCPTokenPair:
        if not isinstance(refresh_token, str) or not refresh_token.startswith("zabt_mcp_rt_"):
            raise MCPOAuthError("invalid_grant", "Refresh token is invalid")
        normalized_client_id = _validate_client_id(client_id)
        normalized_resource = validate_resource_url(resource)
        if normalized_resource != mcp_resource_url():
            raise MCPOAuthError("invalid_grant", "Refresh token resource is invalid")
        token_hash = self.hash_token(refresh_token)

        with Session(self.db_engine) as session:
            refresh = session.exec(
                select(MCPRefreshToken)
                .where(MCPRefreshToken.token_hash == token_hash)
                .with_for_update()
            ).first()
            if refresh is None:
                raise MCPOAuthError("invalid_grant", "Refresh token is invalid")
            grant = session.get(MCPGrant, refresh.grant_id)
            registration = session.get(
                MCPClientRegistration,
                grant.client_registration_id if grant is not None else -1,
            )
            if grant is None or registration is None:
                raise MCPOAuthError("invalid_grant", "Refresh token is invalid")
            if (
                registration.revoked_at is not None
                or grant.revoked_at is not None
                or registration.client_id != normalized_client_id
                or grant.resource != normalized_resource
                or grant.scope != MCP_READ_SCOPE
                or not self._supports_refresh(registration)
            ):
                raise MCPOAuthError("invalid_grant", "Refresh token is invalid")
            user = session.get(User, grant.user_id)
            if user is None or not user.is_active:
                raise MCPOAuthError("invalid_grant", "Refresh token is invalid")
            if scope is not None and _validate_scope(scope) != grant.scope:
                raise MCPOAuthError("invalid_grant", "Refresh token scope is invalid")

            now = _utc_now()
            if (
                refresh.used_at is not None
                or refresh.revoked_at is not None
                or _as_utc(refresh.expires_at) <= _as_utc(now)
            ):
                # Reuse of a rotated token invalidates the entire grant family.
                self._revoke_grant_in_session(session, grant, now=now)
                session.commit()
                raise MCPOAuthError("invalid_grant", "Refresh token is invalid")

            refresh.used_at = now
            refresh.revoked_at = now
            session.add(refresh)
            pair = self._issue_token_pair(
                session,
                registration=registration,
                grant=grant,
                include_refresh=True,
            )
            session.commit()
            return pair

    @staticmethod
    def _revoke_grant_in_session(session: Session, grant: MCPGrant, *, now: datetime) -> None:
        if grant.revoked_at is None:
            grant.generation += 1
            grant.revoked_at = now
        session.add(grant)
        for token in session.exec(
            select(MCPAccessToken).where(MCPAccessToken.grant_id == grant.id)
        ).all():
            token.revoked_at = now
            session.add(token)
        for token in session.exec(
            select(MCPRefreshToken).where(MCPRefreshToken.grant_id == grant.id)
        ).all():
            token.revoked_at = now
            session.add(token)

    def resolve_access_token(self, raw_token: str) -> ResolvedMCPAccessToken | None:
        if (
            not isinstance(raw_token, str)
            or not raw_token
            or len(raw_token) > 256
            or raw_token != raw_token.strip()
            or not raw_token.startswith("zabt_mcp_at_")
        ):
            return None
        with Session(self.db_engine) as session:
            token = session.exec(
                select(MCPAccessToken).where(
                    MCPAccessToken.token_hash == self.hash_token(raw_token)
                )
            ).first()
            if token is None:
                return None
            grant = session.get(MCPGrant, token.grant_id)
            registration = session.get(
                MCPClientRegistration,
                grant.client_registration_id if grant is not None else -1,
            )
            user = session.get(User, grant.user_id if grant is not None else -1)
            now = _utc_now()
            if (
                grant is None
                or registration is None
                or user is None
                or not user.is_active
                or registration.revoked_at is not None
                or grant.revoked_at is not None
                or token.revoked_at is not None
                or _as_utc(token.expires_at) <= _as_utc(now)
                or grant.resource != mcp_resource_url()
                or grant.scope != MCP_READ_SCOPE
                or not hmac.compare_digest(token.token_hash, self.hash_token(raw_token))
            ):
                return None
            token.last_used_at = now
            grant.last_used_at = now
            session.add(token)
            session.add(grant)
            session.commit()
            return ResolvedMCPAccessToken(
                token_id=int(token.id),
                grant_id=int(grant.id),
                client_id=registration.client_id,
                user_id=int(grant.user_id),
                resource=grant.resource,
                scope=grant.scope,
                expires_at=token.expires_at,
            )

    def revoke_token(self, *, token: str, client_id: object | None = None) -> None:
        if not isinstance(token, str) or not token or len(token) > 256:
            return
        normalized_client_id: str | None = None
        if client_id is not None:
            try:
                normalized_client_id = _validate_client_id(client_id)
            except MCPOAuthError:
                return
        token_hash = self.hash_token(token)
        with Session(self.db_engine) as session:
            access = session.exec(
                select(MCPAccessToken).where(MCPAccessToken.token_hash == token_hash)
            ).first()
            if access is not None:
                grant = session.get(MCPGrant, access.grant_id)
                registration = session.get(
                    MCPClientRegistration,
                    grant.client_registration_id if grant is not None else -1,
                )
                if normalized_client_id is None or (
                    registration is not None and registration.client_id == normalized_client_id
                ):
                    access.revoked_at = _utc_now()
                    session.add(access)
                    session.commit()
                return
            refresh = session.exec(
                select(MCPRefreshToken).where(MCPRefreshToken.token_hash == token_hash)
            ).first()
            if refresh is None:
                return
            grant = session.get(MCPGrant, refresh.grant_id)
            registration = session.get(
                MCPClientRegistration,
                grant.client_registration_id if grant is not None else -1,
            )
            if normalized_client_id is None or (
                registration is not None and registration.client_id == normalized_client_id
            ):
                refresh.revoked_at = _utc_now()
                session.add(refresh)
                session.commit()

    def list_linked_grants(self, user_id: int) -> list[MCPLinkedGrant]:
        with Session(self.db_engine) as session:
            grants = session.exec(
                select(MCPGrant)
                .where(MCPGrant.user_id == user_id)
                .order_by(MCPGrant.created_at.desc(), MCPGrant.id.desc())
            ).all()
            linked: list[MCPLinkedGrant] = []
            for grant in grants:
                registration = session.get(MCPClientRegistration, grant.client_registration_id)
                if registration is None:
                    continue
                linked.append(
                    MCPLinkedGrant(
                        grant_id=int(grant.id),
                        client_id=registration.client_id,
                        client_name=registration.client_name,
                        redirect_uris=tuple(
                            _json_list(registration.redirect_uris_json, field="redirect URIs")
                        ),
                        resource=grant.resource,
                        scope=grant.scope,
                        created_at=grant.created_at,
                        last_used_at=grant.last_used_at,
                        revoked_at=grant.revoked_at,
                    )
                )
            return linked

    def revoke_grant(self, *, grant_id: int, user_id: int) -> bool:
        with Session(self.db_engine) as session:
            grant = session.exec(
                select(MCPGrant).where(
                    MCPGrant.id == grant_id,
                    MCPGrant.user_id == user_id,
                )
            ).first()
            if grant is None:
                return False
            self._revoke_grant_in_session(session, grant, now=_utc_now())
            session.commit()
            return True


mcp_oauth_service = MCPOAuthService()
