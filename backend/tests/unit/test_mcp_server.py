# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Read-only MCP verifier, tool, and schema checks."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import AuthenticatedUser, auth_context_var
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from app import mcp_server
from app.core.config import Settings
from app.models import MCPToken


def _json_size_for_test(value) -> int:
    if isinstance(value, list):
        value = [item.model_dump(mode="json") for item in value]
    else:
        value = value.model_dump(mode="json")
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


@pytest.fixture()
def authenticated_owner():
    access_token = AccessToken(
        token="opaque-test-token",
        client_id="mcp-token-1",
        scopes=[mcp_server.MCP_READ_SCOPE],
        subject="7",
        claims={"user_id": 7, "token_id": 1},
        expires_at=int((datetime.now() + timedelta(minutes=5)).timestamp()),
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        yield access_token
    finally:
        auth_context_var.reset(context_token)


class _FakeTokenService:
    def __init__(self, record: MCPToken | None):
        self.record = record
        self.seen_token: str | None = None

    def resolve_bearer_token(self, raw_token: str):
        self.seen_token = raw_token
        return self.record


@pytest.mark.asyncio
async def test_token_verifier_exposes_owner_claims_and_read_scope():
    record = MCPToken(
        id=11,
        user_id=7,
        label="test",
        token_hash="a" * 64,
        token_prefix="zabt_mcp_abc",
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    token_service = _FakeTokenService(record)

    verified = await mcp_server.MCPTokenVerifier(token_service).verify_token("opaque")

    assert verified is not None
    assert verified.subject == "7"
    assert verified.claims == {"user_id": 7, "token_id": 11, "scope": ["mcp:read"]}
    assert verified.scopes == ["mcp:read"]
    assert token_service.seen_token == "opaque"


@pytest.mark.asyncio
async def test_invalid_token_verifier_result_is_none():
    assert await mcp_server.MCPTokenVerifier(_FakeTokenService(None)).verify_token("bad") is None


class _FakeGroupService:
    def __init__(self):
        self.access_checks: list[tuple[int, int]] = []

    def list_for_user_with_meeting_counts(self, user_id: int):
        return [(SimpleNamespace(id=3, name="Owned", description=None), 2)]

    def get_accessible(self, group_id: int, user_id: int):
        self.access_checks.append((group_id, user_id))
        if user_id != 7:
            raise HTTPException(status_code=403, detail="Access denied")
        return SimpleNamespace(id=group_id, name="Owned", description="Private")


class _FakeMeetingService:
    def __init__(self):
        self.owner_queries: list[tuple[int, int]] = []
        self.meeting = SimpleNamespace(
            id=9,
            owner_id=7,
            title="Private meeting",
            group_id=3,
            status="completed",
            summary_text="Summary",
            action_items_text="Action item",
            structured_output={"topic": "planning"},
            transcript_text="x" * 500,
            segments=[],
        )

    def get_group_meetings(self, group_id: int, owner_id: int, limit: int):
        return [
            SimpleNamespace(
                id=9,
                title="Private meeting",
                description=None,
                duration_seconds=60,
                owner_id=owner_id,
                group_id=group_id,
                created_at=datetime.utcnow(),
                status="completed",
                sub_status=None,
                summary_text="short summary",
            )
        ][:limit]

    def get_meeting(self, meeting_id: int):
        return self.meeting if meeting_id == self.meeting.id else None

    def get_meeting_for_owner(self, meeting_id: int, owner_id: int):
        self.owner_queries.append((meeting_id, owner_id))
        if meeting_id != self.meeting.id or owner_id != self.meeting.owner_id:
            return None
        return self.meeting


@pytest.mark.asyncio
async def test_tools_use_authenticated_owner_and_bound_context(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_groups = _FakeGroupService()
    fake_meetings = _FakeMeetingService()
    monkeypatch.setattr(mcp_server, "group_service", fake_groups)
    monkeypatch.setattr(mcp_server, "meeting_service", fake_meetings)

    groups = await mcp_server.list_groups()
    meetings = await mcp_server.list_group_meetings(3, limit=1)
    context = await mcp_server.get_meeting_context(9, max_chars=120)

    assert groups[0].id == 3
    assert meetings[0].group_id == 3
    assert fake_groups.access_checks == [(3, 7), (3, 7)]
    assert fake_meetings.owner_queries == [(9, 7)]
    assert len(context.transcript_context) <= 120
    assert "file_path" not in context.model_dump()
    assert "source_url" not in context.model_dump()


@pytest.mark.asyncio
async def test_meeting_context_redacts_and_bounds_structured_output(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_groups = _FakeGroupService()
    fake_meetings = _FakeMeetingService()
    sentinel = "nested-secret-sentinel-value"
    fake_meetings.meeting.title = "meeting-title-" + "T" * 10_000
    fake_meetings.meeting.summary_text = "summary " * 500
    fake_meetings.meeting.action_items_text = "action " * 500
    fake_meetings.meeting.transcript_text = "transcript " * 500
    fake_meetings.meeting.structured_output = {
        "key_questions": ["Which safe question should remain?"],
        "safe": {"description": "x" * 400},
        "provider": {
            "api_key": sentinel,
            "nested": {"secret": sentinel, "access_token": sentinel},
        },
        "PRIVATE KEY": sentinel,
        "Signing-Key": sentinel,
        "Encryption Key": sentinel,
        "my_private_key": sentinel,
        "x_signing_key_value": sentinel,
        "my-encryption-key": sentinel,
        "Api Key": sentinel,
        "CLIENT SECRET": sentinel,
        "Credentials": sentinel,
        "AUTHORIZATION": sentinel,
        "Access Token": sentinel,
        "REFRESH TOKEN": sentinel,
        "nested": {
            "level_one": {
                "level_two": {
                    "level_three": {
                        "level_four": {"level_five": sentinel},
                    }
                }
            }
        },
        "oversized_items": [{"value": "x" * 1_000} for _ in range(100)],
    }
    fake_groups.get_accessible = lambda group_id, user_id: SimpleNamespace(
        id=group_id,
        name="group-name-" + "G" * 10_000,
        description="description " * 1_000,
    )
    monkeypatch.setattr(mcp_server, "group_service", fake_groups)
    monkeypatch.setattr(mcp_server, "meeting_service", fake_meetings)

    context = await mcp_server.get_meeting_context(9, max_chars=1_000)
    encoded_context = json.dumps(context.model_dump(), ensure_ascii=False)
    encoded_structured = json.dumps(context.structured_output, ensure_ascii=False)

    assert sentinel not in encoded_context
    assert all(
        sensitive_name not in encoded_context.casefold()
        for sensitive_name in (
            "provider",
            "api_key",
            "secret",
            "access_token",
            "refresh_token",
        )
    )
    assert "which safe question should remain" in encoded_context.casefold()
    assert len(encoded_structured) <= 1_000
    assert len(context.title) <= mcp_server._MCP_METADATA_TEXT_MAX_CHARS
    assert len(context.group.name) <= mcp_server._MCP_METADATA_TEXT_MAX_CHARS
    assert len(context.transcript_context) <= 1_000
    assert len(context.summary or "") <= 1_000
    assert len(context.action_items or "") <= 1_000
    assert len(context.group.description or "") <= mcp_server._MCP_DESCRIPTION_MAX_CHARS
    assert len(encoded_context) <= mcp_server._MCP_RESPONSE_MAX_CHARS


def test_sensitive_key_matching_is_normalized_without_redacting_domain_keys():
    sensitive_keys = (
        "private_key",
        "PRIVATE KEY",
        "Signing-Key",
        "signingKey",
        "encryption key",
        "my_private_key",
        "x_signing_key_value",
        "my-encryption-key",
        "prefix_api_key_value",
        "some_client_secret_value",
        "API Key",
        "Client Secret",
        "credentials",
        "Authorization",
        "Access Token",
        "refresh-token",
    )

    assert all(mcp_server._is_sensitive_key(key) for key in sensitive_keys)
    assert mcp_server._is_sensitive_key("key_questions") is False
    assert mcp_server._is_sensitive_key("question_key") is False


@pytest.mark.asyncio
async def test_collection_metadata_and_tool_responses_are_bounded(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_groups = _FakeGroupService()
    fake_meetings = _FakeMeetingService()
    hostile_name = "N" * 10_000
    fake_groups.list_for_user_with_meeting_counts = lambda user_id: [
        (SimpleNamespace(id=index, name=hostile_name, description=hostile_name), 1)
        for index in range(100)
    ]
    fake_groups.get_accessible = lambda group_id, user_id: SimpleNamespace(
        id=group_id,
        name=hostile_name,
        description=hostile_name,
    )
    fake_meetings.get_group_meetings = lambda group_id, owner_id, limit: [
        SimpleNamespace(
            id=index,
            title=hostile_name,
            description=hostile_name,
            duration_seconds=60,
            group_id=group_id,
            created_at=datetime.utcnow(),
            status=hostile_name,
            sub_status=hostile_name,
            summary_text=hostile_name,
        )
        for index in range(100)
    ]
    monkeypatch.setattr(mcp_server, "group_service", fake_groups)
    monkeypatch.setattr(mcp_server, "meeting_service", fake_meetings)

    groups = await mcp_server.list_groups()
    meetings = await mcp_server.list_group_meetings(3)

    assert groups
    assert meetings
    assert len(groups[0].name) <= mcp_server._MCP_METADATA_TEXT_MAX_CHARS
    assert len(groups[0].description or "") <= mcp_server._MCP_DESCRIPTION_MAX_CHARS
    assert len(meetings[0].title) <= mcp_server._MCP_METADATA_TEXT_MAX_CHARS
    assert len(meetings[0].description or "") <= mcp_server._MCP_DESCRIPTION_MAX_CHARS
    assert _json_size_for_test(groups) <= mcp_server._MCP_RESPONSE_MAX_CHARS
    assert _json_size_for_test(meetings) <= mcp_server._MCP_RESPONSE_MAX_CHARS


class _CaptureLogger:
    def __init__(self):
        self.warning_calls: list[tuple[tuple, dict]] = []

    def warning(self, *args, **kwargs):
        self.warning_calls.append((args, kwargs))


@pytest.mark.asyncio
async def test_unexpected_tool_error_is_generic_and_logs_only_safe_context(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
):
    capture_logger = _CaptureLogger()

    class _ExplodingGroupService:
        def list_for_user_with_meeting_counts(self, user_id: int):
            raise RuntimeError("provider body SECRET transcript TOKEN")

    monkeypatch.setattr(mcp_server, "logger", capture_logger)
    monkeypatch.setattr(mcp_server, "group_service", _ExplodingGroupService())

    with pytest.raises(ToolError, match="read request") as exc_info:
        await mcp_server.list_groups()

    assert "provider body" not in str(exc_info.value)
    rendered_log = repr(capture_logger.warning_calls)
    assert "provider body" not in rendered_log
    assert "SECRET" not in rendered_log
    assert "transcript" not in rendered_log
    assert "TOKEN" not in rendered_log
    assert "RuntimeError" in rendered_log
    assert "list_groups" in rendered_log
    assert "user_id=%s" in rendered_log


def test_transport_security_is_explicit_and_stateless():
    transport_security = mcp_server._mcp_transport_security()

    assert transport_security.enable_dns_rebinding_protection is True
    assert transport_security.allowed_hosts
    assert transport_security.allowed_origins
    assert mcp_server.MCP_STATELESS_HTTP is True
    assert "*" not in transport_security.allowed_hosts
    assert "*" not in transport_security.allowed_origins


def test_production_transport_settings_reject_wildcards():
    with pytest.raises(ValidationError, match="MCP_ALLOWED_HOSTS"):
        Settings(
            _env_file=None,
            AUTH_JWT_SECRET="mcp-config-test-secret-with-enough-entropy-12345!",
            AUTH_ENVIRONMENT="production",
            AUTH_COOKIE_SECURE=True,
            MICROSOFT_OIDC_REDIRECT_URI="https://api.example.com/oidc/callback",
            MCP_ALLOWED_HOSTS="*",
            MCP_ALLOWED_ORIGINS="https://app.example.com",
        )


@pytest.mark.asyncio
async def test_search_preserves_safe_retrieval_failure_as_tool_error(
    authenticated_owner,
    monkeypatch: pytest.MonkeyPatch,
):
    def unavailable(**kwargs):
        assert kwargs["user_id"] == 7
        raise HTTPException(status_code=503, detail="retrieval unavailable")

    monkeypatch.setattr(
        mcp_server.group_service,
        "get_accessible",
        lambda group_id, user_id: None,
    )
    monkeypatch.setattr(mcp_server.retrieval_service, "search", unavailable)

    with pytest.raises(ToolError, match="temporarily unavailable"):
        await mcp_server.search_group_meetings(3, "planning")


@pytest.mark.asyncio
async def test_meeting_context_rejects_a_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_meetings = _FakeMeetingService()
    monkeypatch.setattr(mcp_server, "meeting_service", fake_meetings)
    foreign_access = AccessToken(
        token="foreign-token",
        client_id="mcp-token-2",
        scopes=[mcp_server.MCP_READ_SCOPE],
        subject="8",
        expires_at=int((datetime.now() + timedelta(minutes=5)).timestamp()),
    )
    context_token = auth_context_var.set(AuthenticatedUser(foreign_access))
    try:
        with pytest.raises(ToolError, match="not available"):
            await mcp_server.get_meeting_context(9)
    finally:
        auth_context_var.reset(context_token)


@pytest.mark.asyncio
async def test_tool_listing_is_read_only_and_requires_mcp_read_scope():
    tools = await mcp_server.mcp_server.list_tools()

    assert [tool.name for tool in tools] == list(mcp_server.MCP_TOOL_NAMES)
    assert mcp_server.mcp_server.settings.auth.required_scopes == ["mcp:read"]
    assert all(tool.annotations.read_only_hint is True for tool in tools)
    assert all(tool.annotations.destructive_hint is False for tool in tools)
    assert not {tool.name for tool in tools} & {"create_group", "delete_group", "revoke_mcp_token"}
