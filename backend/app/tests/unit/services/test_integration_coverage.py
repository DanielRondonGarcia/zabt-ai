# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for IntegrationService without live OAuth providers."""

from collections.abc import Iterator
from datetime import datetime

import pytest

from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.services import integration as integration_module
from app.services.integration import IntegrationService


class FakeFernet:
    def encrypt(self, value: bytes) -> bytes:
        return b"enc:" + value

    def decrypt(self, value: bytes) -> bytes:
        assert value.startswith(b"enc:")
        return value.removeprefix(b"enc:")


class FakeExecResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class IntegrationStore:
    def __init__(self):
        self.integrations: dict[int, Integration] = {}
        self.next_id = 1
        self.exec_queue: list[list[Integration]] = []

    def session(self):
        return FakeIntegrationSession(self)

    def add_integration(self, **kwargs) -> Integration:
        integration = Integration(**kwargs)
        self.persist(integration)
        return integration

    def persist(self, integration: Integration) -> None:
        if integration.id is None:
            integration.id = self.next_id
            self.next_id += 1
        self.integrations[integration.id] = integration

    def exec_rows(self):
        if self.exec_queue:
            return self.exec_queue.pop(0)
        return list(self.integrations.values())


class FakeIntegrationSession:
    def __init__(self, store: IntegrationStore):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def exec(self, _statement):
        return FakeExecResult(self.store.exec_rows())

    def get(self, model, obj_id: int):
        assert model is Integration
        return self.store.integrations.get(obj_id)

    def add(self, obj):
        assert isinstance(obj, Integration)
        self.store.persist(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def delete(self, obj):
        if obj.id in self.store.integrations:
            del self.store.integrations[obj.id]


@pytest.fixture(name="store", autouse=True)
def fixture_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationStore]:
    store = IntegrationStore()
    monkeypatch.setattr(integration_module, "Session", lambda _engine: store.session())
    yield store


@pytest.fixture(name="service")
def fixture_service() -> IntegrationService:
    service = IntegrationService(encryption_key="")
    service._fernet = FakeFernet()
    return service


def test_encrypt_and_decrypt_token_use_configured_fernet(service: IntegrationService) -> None:
    encrypted = service.encrypt_token("secret-token")

    assert encrypted == "enc:secret-token"
    assert service.decrypt_token(encrypted) == "secret-token"


def test_missing_encryption_key_raises_when_token_operation_is_used() -> None:
    service = IntegrationService(encryption_key="")
    service._fernet = None

    with pytest.raises(ValueError) as exc_info:
        service.encrypt_token("secret")

    assert "TOKEN_ENCRYPTION_KEY" in str(exc_info.value)


def test_upsert_from_oauth_creates_new_active_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    store.exec_queue.append([])

    integration = service.upsert_from_oauth(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
        scopes=["calendar.read"],
        provider_user_id="google-user",
        provider_email="user@example.com",
    )

    assert integration.id in store.integrations
    assert integration.access_token == "enc:access"
    assert integration.refresh_token == "enc:refresh"
    assert integration.expires_at is not None
    assert integration.scopes == ["calendar.read"]
    assert integration.provider_user_id == "google-user"
    assert integration.provider_email == "user@example.com"
    assert integration.status == IntegrationStatus.ACTIVE
    assert integration.connected_at is not None


def test_upsert_from_oauth_updates_existing_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    existing = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.MICROSOFT,
        access_token="old",
        refresh_token="old-refresh",
        scopes=["old"],
        status=IntegrationStatus.EXPIRED,
    )
    store.exec_queue.append([existing])

    updated = service.upsert_from_oauth(
        user_id=1,
        provider=IntegrationProvider.MICROSOFT,
        access_token="new-access",
        refresh_token="new-refresh",
        expires_in=0,
        scopes=["mail.read"],
        provider_email="updated@example.com",
    )

    assert updated is existing
    assert updated.access_token == "enc:new-access"
    assert updated.refresh_token == "enc:new-refresh"
    assert updated.expires_at is None
    assert updated.scopes == ["mail.read"]
    assert updated.provider_email == "updated@example.com"
    assert updated.status == IntegrationStatus.ACTIVE


def test_get_for_user_returns_integrations_from_query(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="access",
        refresh_token="refresh",
    )
    store.exec_queue.append([integration])

    assert service.get_for_user(1) == [integration]


def test_get_by_provider_returns_first_matching_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="access",
        refresh_token="refresh",
    )
    store.exec_queue.append([integration])

    assert service.get_by_provider(1, IntegrationProvider.GOOGLE) is integration


def test_get_all_active_by_provider_returns_query_results(
    service: IntegrationService, store: IntegrationStore
) -> None:
    active = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.MICROSOFT,
        access_token="access",
        refresh_token="refresh",
        status=IntegrationStatus.ACTIVE,
    )
    store.exec_queue.append([active])

    assert service.get_all_active_by_provider(IntegrationProvider.MICROSOFT) == [active]


def test_disconnect_returns_false_when_integration_is_missing(
    service: IntegrationService, store: IntegrationStore
) -> None:
    store.exec_queue.append([])

    assert service.disconnect(1, IntegrationProvider.GOOGLE) is False


def test_disconnect_deletes_existing_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="access",
        refresh_token="refresh",
    )
    store.exec_queue.append([integration])

    assert service.disconnect(1, IntegrationProvider.GOOGLE) is True
    assert integration.id not in store.integrations


def test_update_tokens_returns_none_for_missing_integration(
    service: IntegrationService,
) -> None:
    assert service.update_tokens(404, "access", "refresh", 3600) is None


def test_update_tokens_encrypts_and_reactivates_existing_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="old",
        refresh_token="old-refresh",
        status=IntegrationStatus.EXPIRED,
    )

    updated = service.update_tokens(integration.id, "fresh-access", "fresh-refresh", 60)

    assert updated is integration
    assert updated.access_token == "enc:fresh-access"
    assert updated.refresh_token == "enc:fresh-refresh"
    assert updated.expires_at is not None
    assert updated.status == IntegrationStatus.ACTIVE
    assert isinstance(updated.updated_at, datetime)


def test_update_tokens_allows_missing_expiry(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.GOOGLE,
        access_token="old",
        refresh_token="old-refresh",
    )

    updated = service.update_tokens(integration.id, "access", "refresh", 0)

    assert updated is integration
    assert updated.expires_at is None


def test_mark_expired_returns_none_for_missing_integration(service: IntegrationService) -> None:
    assert service.mark_expired(404) is None


def test_mark_expired_sets_status_on_existing_integration(
    service: IntegrationService, store: IntegrationStore
) -> None:
    integration = store.add_integration(
        user_id=1,
        provider=IntegrationProvider.MICROSOFT,
        access_token="access",
        refresh_token="refresh",
        status=IntegrationStatus.ACTIVE,
    )

    expired = service.mark_expired(integration.id)

    assert expired is integration
    assert expired.status == IntegrationStatus.EXPIRED
    assert isinstance(expired.updated_at, datetime)
