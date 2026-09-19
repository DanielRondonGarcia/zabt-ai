# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused DeviceService coverage without app startup or external services."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Device
from app.services import device as device_module
from app.services.device import DeviceService


class FakeExecResult:
    def __init__(self, rows: list[Device]):
        self.rows = rows

    def all(self) -> list[Device]:
        return list(self.rows)

    def first(self) -> Device | None:
        return self.rows[0] if self.rows else None


class DeviceStore:
    def __init__(self) -> None:
        self.devices: dict[int, Device] = {}
        self.next_device_id = 1

    def add_device(self, **kwargs: object) -> Device:
        device = Device(**kwargs)
        self.persist(device)
        return device

    def persist(self, device: Device) -> None:
        if device.id is None:
            device.id = self.next_device_id
            self.next_device_id += 1
        self.devices[device.id] = device

    def select(self, statement: object) -> list[Device]:
        params = statement.compile().params
        user_id = params.get("user_id_1")
        token = params.get("expo_push_token_1")
        device_id = params.get("id_1")

        if token is not None:
            return [
                device
                for device in self.devices.values()
                if device.user_id == user_id and device.expo_push_token == token
            ]

        if device_id is not None:
            return [
                device
                for device in self.devices.values()
                if device.id == device_id and device.user_id == user_id
            ]

        return [device for device in self.devices.values() if device.user_id == user_id]


class FakeDeviceSession:
    def __init__(self, store: DeviceStore):
        self.store = store
        self.added: list[Device] = []
        self.deleted: list[Device] = []
        self.refreshed: list[Device] = []
        self.commits = 0

    def exec(self, statement: object) -> FakeExecResult:
        return FakeExecResult(self.store.select(statement))

    def add(self, device: Device) -> None:
        self.added.append(device)
        self.store.persist(device)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, device: Device) -> None:
        self.refreshed.append(device)

    def delete(self, device: Device) -> None:
        self.deleted.append(device)
        if device.id is not None:
            self.store.devices.pop(device.id, None)


@pytest.fixture(name="store")
def fixture_store() -> DeviceStore:
    return DeviceStore()


@pytest.fixture(name="session")
def fixture_session(store: DeviceStore) -> FakeDeviceSession:
    return FakeDeviceSession(store)


@pytest.fixture(name="service")
def fixture_service(session: FakeDeviceSession) -> DeviceService:
    return DeviceService(session)  # type: ignore[arg-type]


def test_upsert_refreshes_existing_device_and_commits(
    service: DeviceService,
    session: FakeDeviceSession,
    store: DeviceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_seen_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    new_seen_at = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    existing = store.add_device(
        user_id=42,
        expo_push_token="ExponentPushToken[existing]",
        platform="ios",
        last_seen_at=original_seen_at,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return new_seen_at if tz is not None else new_seen_at.replace(tzinfo=None)

    monkeypatch.setattr(device_module, "datetime", FrozenDateTime)

    result = service.upsert(
        user_id=42,
        expo_push_token="ExponentPushToken[existing]",
        platform="android",
    )

    assert result is existing
    assert existing.platform == "android"
    assert existing.last_seen_at == new_seen_at
    assert session.added == [existing]
    assert session.commits == 1
    assert session.refreshed == [existing]
    assert len(store.devices) == 1


def test_upsert_creates_new_device_and_refreshes(
    service: DeviceService,
    session: FakeDeviceSession,
    store: DeviceStore,
) -> None:
    result = service.upsert(
        user_id=7,
        expo_push_token="ExponentPushToken[new]",
        platform="ios",
    )

    assert result.id == 1
    assert result.user_id == 7
    assert result.expo_push_token == "ExponentPushToken[new]"
    assert result.platform == "ios"
    assert store.devices == {1: result}
    assert session.added == [result]
    assert session.commits == 1
    assert session.refreshed == [result]


def test_get_user_devices_returns_only_devices_for_user(
    service: DeviceService,
    store: DeviceStore,
) -> None:
    first = store.add_device(user_id=5, expo_push_token="token-a", platform="ios")
    second = store.add_device(user_id=5, expo_push_token="token-b", platform="android")
    store.add_device(user_id=6, expo_push_token="token-c", platform="ios")

    assert service.get_user_devices(user_id=5) == [first, second]


def test_delete_by_id_deletes_owned_device_and_commits(
    service: DeviceService,
    session: FakeDeviceSession,
    store: DeviceStore,
) -> None:
    owned = store.add_device(user_id=11, expo_push_token="owned", platform="ios")

    deleted = service.delete_by_id(device_id=owned.id or 0, user_id=11)

    assert deleted is True
    assert owned.id not in store.devices
    assert session.deleted == [owned]
    assert session.commits == 1


@pytest.mark.parametrize(
    ("device_id", "user_id"),
    [
        (999, 11),
        (1, 99),
    ],
)
def test_delete_by_id_returns_false_for_missing_or_wrong_user_without_commit(
    service: DeviceService,
    session: FakeDeviceSession,
    store: DeviceStore,
    device_id: int,
    user_id: int,
) -> None:
    retained = store.add_device(user_id=11, expo_push_token="retained", platform="android")

    deleted = service.delete_by_id(device_id=device_id, user_id=user_id)

    assert deleted is False
    assert store.devices == {retained.id: retained}
    assert session.deleted == []
    assert session.commits == 0
    assert session.refreshed == []
