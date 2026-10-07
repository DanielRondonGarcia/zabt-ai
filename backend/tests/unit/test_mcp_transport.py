# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Minimal in-process Streamable HTTP client smoke coverage."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import mcp_server
from app.models import MCPToken, User
from app.mcp_server import mcp_http_app, mcp_token_service


def test_initialize_tools_list_and_tool_call_use_bearer_token(
    monkeypatch,
):
    monkeypatch.setattr(
        mcp_server.group_service,
        "list_for_user_with_meeting_counts",
        lambda user_id: [],
    )
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
            session.add(User(id=81, email="mcp-transport@example.com"))
            session.commit()

        original_engine = mcp_token_service.db_engine
        mcp_token_service.db_engine = db_engine
        _, raw_token = mcp_token_service.create(81, "local smoke", 30)

        @asynccontextmanager
        async def lifespan(_app):
            async with mcp_http_app.router.lifespan_context(mcp_http_app):
                yield

        test_app = FastAPI(lifespan=lifespan)
        test_app.mount("/api/v1", mcp_http_app)
        request_headers = {
            "Authorization": f"Bearer {raw_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "local-test", "version": "1"},
            },
        }

        with TestClient(test_app) as client:
            missing = client.post(
                "/api/v1/mcp",
                json=initialize_payload,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            assert missing.status_code == 401

            rejected_origin = client.post(
                "/api/v1/mcp",
                json=initialize_payload,
                headers={**request_headers, "Origin": "https://evil.example"},
            )
            assert rejected_origin.status_code == 403

            initialized = client.post(
                "/api/v1/mcp",
                json=initialize_payload,
                headers=request_headers,
            )
            assert initialized.status_code == 200, initialized.text
            protocol_headers = {
                **request_headers,
                "MCP-Protocol-Version": "2025-06-18",
            }

            listed = client.post(
                "/api/v1/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=protocol_headers,
            )
            assert listed.status_code == 200, listed.text
            listed_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert listed_names == {
                "list_groups",
                "list_group_meetings",
                "search_group_meetings",
                "get_meeting_context",
            }

            called = client.post(
                "/api/v1/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_groups", "arguments": {}},
                },
                headers=protocol_headers,
            )
            assert called.status_code == 200, called.text
            assert called.json()["result"]["isError"] is False
            assert called.json()["result"]["structuredContent"] == {"result": []}
    finally:
        mcp_token_service.db_engine = original_engine if "original_engine" in locals() else mcp_token_service.db_engine
        json_column.type = original_type
