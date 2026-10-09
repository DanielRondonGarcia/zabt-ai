# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused tests for the opaque MCP token repository and management routes."""

from collections.abc import Generator
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import deps
from app.api.v1.endpoints import mcp
from app.models import MCPToken, User
from app.services.mcp_tokens import MCPTokenService


@pytest.fixture()
def mcp_database() -> Generator:
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    json_column = User.__table__.c.language_preferences
    original_type = json_column.type
    json_column.type = JSON()
    try:
        SQLModel.metadata.create_all(
            db_engine,
            tables=[User.__table__, MCPToken.__table__],
        )
        with Session(db_engine) as session:
            session.add_all(
                [
                    User(id=1, email="mcp-owner@example.com"),
                    User(id=2, email="mcp-other@example.com"),
                ]
            )
            session.commit()
        yield db_engine
    finally:
        json_column.type = original_type


def test_create_persists_only_digest_and_returns_raw_value_once(mcp_database):
    service = MCPTokenService(mcp_database)

    record, raw_token = service.create(1, "Claude Desktop", 30)

    assert raw_token.startswith("zabt_mcp_")
    assert record.token_hash == service.hash_token(raw_token)
    assert raw_token not in record.token_hash
    assert "token" not in MCPToken.__table__.columns

    listed = service.list_for_user(1)
    assert len(listed) == 1
    assert listed[0].token_prefix == raw_token[:16]
    assert raw_token not in repr(listed[0])


def test_owner_filter_blocks_foreign_revoke_and_resolution(mcp_database):
    service = MCPTokenService(mcp_database)
    record, raw_token = service.create(1, "Owner token", 30)

    assert service.revoke_for_user(record.id, 2) is False
    assert service.resolve_bearer_token(raw_token, owner_id=2) is None
    assert service.resolve_bearer_token(raw_token, owner_id=1) is not None


def test_expired_and_revoked_tokens_are_rejected(mcp_database):
    service = MCPTokenService(mcp_database)
    expired_record, expired_raw = service.create(1, "Expired", 1)
    with Session(mcp_database) as session:
        stored = session.get(MCPToken, expired_record.id)
        assert stored is not None
        stored.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(stored)
        session.commit()

    revoked_record, revoked_raw = service.create(1, "Revoked", 1)
    assert service.revoke(revoked_record.id, 1) is True

    assert service.resolve(expired_raw) is None
    assert service.resolve(revoked_raw) is None


@pytest.mark.parametrize(
    ("label", "expires_in_days"),
    [("", 30), ("   ", 30), ("valid", 0), ("valid", 366)],
)
def test_create_validates_label_and_expiry(mcp_database, label, expires_in_days):
    service = MCPTokenService(mcp_database)

    with pytest.raises(ValueError):
        service.create(1, label, expires_in_days)


@pytest.fixture()
def mcp_management_client(mcp_database, monkeypatch: pytest.MonkeyPatch):
    service = MCPTokenService(mcp_database)
    monkeypatch.setattr(mcp, "mcp_token_service", service)
    test_app = FastAPI()
    test_app.include_router(mcp.router, prefix="/mcp")
    test_app.dependency_overrides[deps.get_current_active_user] = lambda: User(
        id=1,
        email="mcp-owner@example.com",
    )
    with TestClient(test_app) as client:
        yield client


def test_management_routes_return_raw_token_only_on_create(
    mcp_management_client,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        mcp.settings,
        "MCP_PUBLIC_URL",
        "https://mcp.example.com/api/v1/mcp",
    )
    created = mcp_management_client.post(
        "/mcp/tokens",
        json={"label": "Cursor", "expires_in_days": 30},
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    raw_token = body["token"]
    assert raw_token.startswith("zabt_mcp_")

    listed = mcp_management_client.get("/mcp/tokens")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert len(listed_body) == 1
    assert "token" not in listed_body[0]
    assert "token_hash" not in listed_body[0]
    assert raw_token not in listed.text

    status_response = mcp_management_client.get("/mcp/status")
    assert status_response.status_code == 200
    assert status_response.json() == {
        "enabled": True,
        "endpoint": "https://mcp.example.com/api/v1/mcp",
        "auth_mode": "bearer_token",
        "tools": [
            "list_groups",
            "list_group_meetings",
            "search_group_meetings",
            "get_meeting_context",
        ],
    }


def test_management_revoke_is_owner_scoped(mcp_database, monkeypatch: pytest.MonkeyPatch):
    service = MCPTokenService(mcp_database)
    record, raw_token = service.create(1, "Owner token", 30)
    monkeypatch.setattr(mcp, "mcp_token_service", service)

    test_app = FastAPI()
    test_app.include_router(mcp.router, prefix="/mcp")
    test_app.dependency_overrides[deps.get_current_active_user] = lambda: User(
        id=2,
        email="mcp-other@example.com",
    )
    with TestClient(test_app) as client:
        response = client.delete(f"/mcp/tokens/{record.id}")

    assert response.status_code == 404
    assert service.resolve(raw_token, owner_id=1) is not None
