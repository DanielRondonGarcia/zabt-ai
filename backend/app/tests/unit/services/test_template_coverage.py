# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for TemplateService without app startup or live providers."""

from collections.abc import Iterator

from fastapi import HTTPException
import pytest

from app.models import SummaryTemplate, User
from app.services import base as base_module
from app.services import template as template_module
from app.services.template import TemplateService


class FakeExecResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class TemplateStore:
    def __init__(self):
        self.templates: dict[int, SummaryTemplate] = {}
        self.users: dict[int, User] = {}
        self.next_template_id = 1
        self.exec_queue: list[list[SummaryTemplate]] = []

    def session(self):
        return FakeTemplateSession(self)

    def add_template(self, **kwargs) -> SummaryTemplate:
        template = SummaryTemplate(**kwargs)
        self.persist(template)
        return template

    def add_user(self, **kwargs) -> User:
        user = User(**kwargs)
        if user.id is None:
            user.id = len(self.users) + 1
        self.users[user.id] = user
        return user

    def persist(self, obj):
        if isinstance(obj, SummaryTemplate):
            if obj.id is None:
                obj.id = self.next_template_id
                self.next_template_id += 1
            self.templates[obj.id] = obj
        elif isinstance(obj, User):
            if obj.id is None:
                obj.id = len(self.users) + 1
            self.users[obj.id] = obj

    def get(self, model, obj_id: int):
        if model is SummaryTemplate:
            return self.templates.get(obj_id)
        if model is User:
            return self.users.get(obj_id)
        raise AssertionError(f"Unexpected model: {model}")

    def exec_rows(self):
        if self.exec_queue:
            return self.exec_queue.pop(0)
        return [
            template
            for template in self.templates.values()
            if template.template_type == "built_in" or template.owner_id == 1
        ]


class FakeTemplateSession:
    def __init__(self, store: TemplateStore):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, model, obj_id: int):
        return self.store.get(model, obj_id)

    def exec(self, _statement):
        return FakeExecResult(self.store.exec_rows())

    def add(self, obj):
        self.store.persist(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def delete(self, obj):
        if isinstance(obj, SummaryTemplate) and obj.id in self.store.templates:
            del self.store.templates[obj.id]


@pytest.fixture(name="store", autouse=True)
def fixture_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[TemplateStore]:
    store = TemplateStore()
    monkeypatch.setattr(template_module, "Session", lambda _engine: store.session())
    monkeypatch.setattr(base_module, "Session", lambda _engine: store.session())
    yield store


@pytest.fixture(name="service")
def fixture_service() -> TemplateService:
    return TemplateService()


def test_list_for_user_returns_built_in_and_owned_templates(
    service: TemplateService, store: TemplateStore
) -> None:
    built_in = store.add_template(name="Built", body="Body", template_type="built_in")
    owned = store.add_template(name="Owned", body="Body", template_type="custom", owner_id=1)
    foreign = store.add_template(name="Foreign", body="Body", template_type="custom", owner_id=2)
    store.exec_queue.append([built_in, owned])

    templates = service.list_for_user(1)

    assert [template.id for template in templates] == [built_in.id, owned.id]
    assert foreign.id not in [template.id for template in templates]


def test_get_accessible_returns_built_in_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Built", body="Body", template_type="built_in")

    assert service.get_accessible(template.id, user_id=99) is template


def test_get_accessible_returns_owned_custom_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Owned", body="Body", template_type="custom", owner_id=1)

    assert service.get_accessible(template.id, user_id=1) is template


def test_get_accessible_missing_template_raises_404(service: TemplateService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.get_accessible(404, user_id=1)

    assert exc_info.value.status_code == 404


def test_get_accessible_foreign_template_raises_403(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Foreign", body="Body", template_type="custom", owner_id=2)

    with pytest.raises(HTTPException) as exc_info:
        service.get_accessible(template.id, user_id=1)

    assert exc_info.value.status_code == 403


def test_create_custom_validates_and_persists_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = service.create_custom(user_id=1, name="Custom", body="Summarize this")

    assert template.id is not None
    assert store.templates[template.id] is template
    assert template.owner_id == 1
    assert template.template_type == "custom"
    assert template.is_system_default is False


def test_update_custom_persists_owned_template_changes(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Old", body="Old body", template_type="custom", owner_id=1)

    updated = service.update_custom(template.id, user_id=1, name="New", body="New body")

    assert updated.name == "New"
    assert updated.body == "New body"
    assert updated.updated_at is not None


def test_update_custom_missing_template_raises_404(service: TemplateService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.update_custom(404, user_id=1, name="New", body="Body")

    assert exc_info.value.status_code == 404


def test_update_custom_rejects_built_in_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Built", body="Body", template_type="built_in")

    with pytest.raises(HTTPException) as exc_info:
        service.update_custom(template.id, user_id=1, name="New", body="Body")

    assert exc_info.value.status_code == 403
    assert "Built-in" in exc_info.value.detail


def test_update_custom_rejects_foreign_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Foreign", body="Body", template_type="custom", owner_id=2)

    with pytest.raises(HTTPException) as exc_info:
        service.update_custom(template.id, user_id=1, name="New", body="Body")

    assert exc_info.value.status_code == 403


def test_delete_custom_removes_owned_template_and_clears_user_default(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Owned", body="Body", template_type="custom", owner_id=1)
    user = store.add_user(id=1, email="owner@example.com", default_template_id=template.id)

    service.delete_custom(template.id, user_id=1)

    assert template.id not in store.templates
    assert user.default_template_id is None


def test_delete_custom_missing_template_raises_404(service: TemplateService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.delete_custom(404, user_id=1)

    assert exc_info.value.status_code == 404


def test_delete_custom_rejects_built_in_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Built", body="Body", template_type="built_in")

    with pytest.raises(HTTPException) as exc_info:
        service.delete_custom(template.id, user_id=1)

    assert exc_info.value.status_code == 403
    assert "deleted" in exc_info.value.detail


def test_delete_custom_rejects_foreign_template(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Foreign", body="Body", template_type="custom", owner_id=2)

    with pytest.raises(HTTPException) as exc_info:
        service.delete_custom(template.id, user_id=1)

    assert exc_info.value.status_code == 403


def test_set_user_default_updates_existing_user(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Owned", body="Body", template_type="custom", owner_id=1)
    user = store.add_user(id=1, email="owner@example.com")

    result = service.set_user_default(user_id=1, template_id=template.id)

    assert result is template
    assert user.default_template_id == template.id


def test_set_user_default_missing_user_raises_404(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Built", body="Body", template_type="built_in")

    with pytest.raises(HTTPException) as exc_info:
        service.set_user_default(user_id=404, template_id=template.id)

    assert exc_info.value.status_code == 404


def test_get_active_default_returns_user_default_when_present(
    service: TemplateService, store: TemplateStore
) -> None:
    template = store.add_template(name="Default", body="Body", template_type="custom", owner_id=1)
    store.add_user(id=1, email="owner@example.com", default_template_id=template.id)

    assert service.get_active_default(user_id=1) is template


def test_get_active_default_falls_back_to_system_default(
    service: TemplateService, store: TemplateStore
) -> None:
    system_default = store.add_template(
        name="System", body="Body", template_type="built_in", is_system_default=True
    )
    store.add_user(id=1, email="owner@example.com")
    store.exec_queue.append([system_default])

    assert service.get_active_default(user_id=1) is system_default


@pytest.mark.parametrize("name", ["", "   ", "A" * 101])
def test_validate_name_rejects_empty_or_too_long_values(name: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        TemplateService._validate_name(name)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("body", ["", "   ", "A" * 4001])
def test_validate_body_rejects_empty_or_too_long_values(body: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        TemplateService._validate_body(body)

    assert exc_info.value.status_code == 400


def test_validate_name_and_body_accept_boundary_values() -> None:
    TemplateService._validate_name("A" * 100)
    TemplateService._validate_body("B" * 4000)
