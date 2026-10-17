# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public API checks for administrator-managed storage configuration."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

@pytest.fixture()
def storage_client(monkeypatch: pytest.MonkeyPatch) -> Generator:
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    from app.api.api import api_router
    from app.api.deps import get_current_active_user, get_db
    from app.core.config import settings

    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    storage_table = SQLModel.metadata.tables.get("storageconfiguration")
    if storage_table is not None:
        storage_table.create(db_engine)

    def get_test_db():
        with Session(db_engine) as session:
            yield session

    user = SimpleNamespace(
        id=101,
        email="storage-admin@example.com",
        is_active=True,
        is_superuser=False,
    )
    test_app = FastAPI()
    test_app.include_router(api_router, prefix="/api/v1")
    test_app.dependency_overrides[get_db] = get_test_db
    test_app.dependency_overrides[get_current_active_user] = lambda: user
    monkeypatch.setattr(settings, "AUTH_ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    with TestClient(test_app) as client:
        yield client, user


def test_storage_configuration_is_superuser_only_and_denied_write_has_no_effect(
    storage_client,
):
    client, user = storage_client
    payload = {
        "provider": "aws_s3",
        "aws_region": "us-east-1",
        "aws_bucket": "zabt-recordings",
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key": "test-secret-value",
    }

    read_response = client.get("/api/v1/storage/configuration")
    write_response = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json=payload,
    )

    assert read_response.status_code == 403
    assert read_response.json() == {"detail": "System superuser access is required"}
    assert write_response.status_code == 403
    assert write_response.json() == {"detail": "System superuser access is required"}

    user.is_superuser = True
    unchanged = client.get("/api/v1/storage/configuration")
    assert unchanged.status_code == 200
    assert unchanged.json()["provider"] == "minio"
    assert unchanged.json()["aws_secret_access_key_configured"] is False


def test_superuser_can_configure_s3_without_secret_in_api_response(storage_client):
    client, user = storage_client
    user.is_superuser = True
    secret = "test-secret-value"

    saved = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-east-1",
            "aws_bucket": "zabt-recordings",
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": secret,
        },
    )

    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "provider": "aws_s3",
        "aws_region": "us-east-1",
        "aws_bucket": "zabt-recordings",
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key_configured": True,
    }
    assert secret not in saved.text

    loaded = client.get("/api/v1/storage/configuration")
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()
    assert secret not in loaded.text
    assert loaded.headers["cache-control"] == "no-store"


def test_incomplete_s3_configuration_is_rejected_without_switching_provider(storage_client):
    client, user = storage_client
    user.is_superuser = True

    rejected = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_bucket": "zabt-recordings",
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": "test-secret-value",
        },
    )

    assert rejected.status_code == 422
    unchanged = client.get("/api/v1/storage/configuration")
    assert unchanged.status_code == 200
    assert unchanged.json()["provider"] == "minio"
    assert unchanged.json()["aws_secret_access_key_configured"] is False
