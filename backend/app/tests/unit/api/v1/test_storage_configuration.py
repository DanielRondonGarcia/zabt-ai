# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Public API checks for administrator-managed storage configuration."""

from collections.abc import Generator
from datetime import datetime, timezone
from types import SimpleNamespace

from botocore.exceptions import ClientError
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
    endpoint = "https://my-namespace.compat.objectstorage.us-ashburn-1.oraclecloud.com"

    saved = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-east-1",
            "s3_endpoint_url": endpoint,
            "aws_bucket": "zabt-recordings",
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": secret,
        },
    )

    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "provider": "aws_s3",
        "aws_region": "us-east-1",
        "s3_endpoint_url": endpoint,
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


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://s3.example.com",
        "https://secret-user:secret-password@s3.example.com",
        "https://s3.example.com?token=secret-token",
        "https://s3.example.com#fragment",
    ],
)
def test_invalid_s3_endpoint_is_rejected_without_leaking_or_changing_configuration(
    storage_client, endpoint: str
):
    client, user = storage_client
    user.is_superuser = True
    baseline = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-east-1",
            "aws_bucket": "baseline-bucket",
            "aws_access_key_id": "BASELINEKEY",
            "aws_secret_access_key": "baseline-secret",
        },
    )
    assert baseline.status_code == 200, baseline.text

    rejected = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-east-1",
            "s3_endpoint_url": endpoint,
            "aws_bucket": "replacement-bucket",
            "aws_access_key_id": "REPLACEMENTKEY",
            "aws_secret_access_key": "replacement-secret",
        },
    )

    assert rejected.status_code == 422
    assert rejected.json() == {
        "detail": "S3 endpoint URL must be an absolute HTTPS URL without credentials, query, or fragment."
    }
    assert "secret-password" not in rejected.text
    assert "secret-token" not in rejected.text
    assert "replacement-secret" not in rejected.text
    unchanged = client.get("/api/v1/storage/configuration")
    assert unchanged.status_code == 200
    assert unchanged.json() == baseline.json()


def test_s3_object_listing_is_superuser_only_and_never_resolves_storage_for_regular_user(
    storage_client, monkeypatch: pytest.MonkeyPatch
):
    client, user = storage_client
    from app.api.v1.endpoints import storage_configuration as endpoint

    called = False

    def fail_if_storage_is_resolved(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("storage must not be resolved for a non-superuser")

    monkeypatch.setattr(
        endpoint,
        "get_storage_for_provider",
        fail_if_storage_is_resolved,
        raising=False,
    )

    response = client.get("/api/v1/storage/objects")

    assert response.status_code == 403
    assert response.json() == {"detail": "System superuser access is required"}
    assert called is False


def test_superuser_can_read_only_list_the_configured_s3_bucket(storage_client, monkeypatch):
    client, user = storage_client
    user.is_superuser = True
    saved = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-ashburn-1",
            "s3_endpoint_url": "https://namespace.compat.objectstorage.us-ashburn-1.oraclecloud.com",
            "aws_bucket": "admin-configured-bucket",
            "aws_access_key_id": "ADMINACCESS",
            "aws_secret_access_key": "admin-secret",
        },
    )
    assert saved.status_code == 200, saved.text

    timestamp = datetime(2025, 2, 3, 4, 5, tzinfo=timezone.utc)
    calls = []
    fake_storage = SimpleNamespace(
        list_objects_page=lambda **kwargs: calls.append(kwargs)
        or {
            "objects": [
                {
                    "key": "users/7/test.wav",
                    "size": 123,
                    "last_modified": timestamp,
                }
            ],
            "prefixes": ["users/7/meetings/"],
            "next_continuation_token": "next-cursor",
        }
    )

    from app.api.v1.endpoints import storage_configuration as endpoint

    resolved = []

    def resolve_storage(provider, session):
        resolved.append((provider, session))
        return fake_storage

    monkeypatch.setattr(endpoint, "get_storage_for_provider", resolve_storage, raising=False)

    response = client.get(
        "/api/v1/storage/objects",
        params={
            "prefix": "users/7/",
            "continuation_token": "incoming-cursor",
            "bucket": "attacker-selected-bucket",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["objects"] == [
        {
            "key": "users/7/test.wav",
            "size": 123,
            "last_modified": "2025-02-03T04:05:00Z",
        }
    ]
    assert result["prefixes"] == ["users/7/meetings/"]
    assert result["next_continuation_token"] == "next-cursor"
    assert calls == [
        {
            "prefix": "users/7/",
            "continuation_token": "incoming-cursor",
            "max_keys": 50,
        }
    ]
    assert len(resolved) == 1 and resolved[0][0] == "aws_s3" and resolved[0][1] is not None
    assert "admin-secret" not in response.text
    assert "namespace.compat.objectstorage" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_s3_object_listing_hides_upstream_error_details(storage_client, monkeypatch):
    client, user = storage_client
    user.is_superuser = True
    saved = client.put(
        "/api/v1/storage/configuration",
        headers={"Origin": "http://localhost:3000"},
        json={
            "provider": "aws_s3",
            "aws_region": "us-ashburn-1",
            "aws_bucket": "admin-configured-bucket",
            "aws_access_key_id": "ADMINACCESS",
            "aws_secret_access_key": "admin-secret",
        },
    )
    assert saved.status_code == 200, saved.text

    def fail_with_sensitive_error(**_kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "private-endpoint and admin-secret must not be returned",
                }
            },
            "ListObjectsV2",
        )

    fake_storage = SimpleNamespace(list_objects_page=fail_with_sensitive_error)
    from app.api.v1.endpoints import storage_configuration as endpoint

    monkeypatch.setattr(
        endpoint,
        "get_storage_for_provider",
        lambda *_args, **_kwargs: fake_storage,
        raising=False,
    )

    response = client.get("/api/v1/storage/objects")

    assert response.status_code == 502
    assert response.json() == {"detail": "Configured S3 storage could not be listed."}
    assert "private-endpoint" not in response.text
    assert "admin-secret" not in response.text
