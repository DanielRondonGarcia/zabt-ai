# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for EmailShareService without network or real database access."""

from datetime import datetime

import pytest

from app.models import EmailShare, EmailShareStatus, Integration, IntegrationProvider, Meeting
from app.services import email_share as email_share_module
from app.services.email_share import EmailShareService
from app.services.microsoft_graph import MicrosoftGraphError


class FakeExecResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ShareStore:
    def __init__(self):
        self.meetings: dict[int, Meeting] = {}
        self.shares: dict[int, EmailShare] = {}
        self.next_share_id = 1
        self.missing_share_on_update = False

    def session(self):
        return FakeSession(self)

    def persist_share(self, share: EmailShare) -> None:
        if share.id is None:
            share.id = self.next_share_id
            self.next_share_id += 1
        self.shares[share.id] = share


class FakeSession:
    def __init__(self, store: ShareStore):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, model, obj_id: int):
        if model is Meeting:
            return self.store.meetings.get(obj_id)
        if model is EmailShare:
            if self.store.missing_share_on_update:
                return None
            return self.store.shares.get(obj_id)
        raise AssertionError(f"unexpected model {model}")

    def add(self, obj):
        if isinstance(obj, EmailShare):
            self.store.persist_share(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def exec(self, _statement):
        rows = sorted(self.store.shares.values(), key=lambda share: share.created_at, reverse=True)
        return FakeExecResult(rows)


class FakeIntegrationService:
    def __init__(self, integration: Integration | None):
        self.integration = integration

    def get_by_provider(self, user_id: int, provider: IntegrationProvider):
        assert user_id == 7
        assert provider is IntegrationProvider.MICROSOFT
        return self.integration

    def decrypt_token(self, encrypted: str) -> str:
        assert encrypted == "encrypted-token"
        return "decrypted-access-token"


class FakeGraphClient:
    failures: set[str] = set()
    sent: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def send_email(self, **kwargs):
        self.sent.append(kwargs)
        if kwargs["to_email"] in self.failures:
            raise MicrosoftGraphError("graph send failed", status_code=503)


@pytest.fixture(name="store")
def fixture_store(monkeypatch: pytest.MonkeyPatch) -> ShareStore:
    store = ShareStore()
    monkeypatch.setattr(email_share_module, "Session", lambda _engine: store.session())
    return store


@pytest.fixture(autouse=True)
def fake_settings_and_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeGraphClient.failures = set()
    FakeGraphClient.sent = []
    monkeypatch.setattr(email_share_module, "MicrosoftGraphClient", FakeGraphClient)
    monkeypatch.setattr(email_share_module.settings, "MICROSOFT_CLIENT_ID", "client-id", raising=False)
    monkeypatch.setattr(email_share_module.settings, "MICROSOFT_CLIENT_SECRET", "client-secret", raising=False)
    monkeypatch.setattr(email_share_module.settings, "MICROSOFT_TENANT_ID", "tenant-id", raising=False)
    monkeypatch.setattr(email_share_module.settings, "MICROSOFT_REDIRECT_URI", "https://app.example/callback", raising=False)


def make_meeting(**overrides) -> Meeting:
    data = {
        "id": 10,
        "owner_id": 7,
        "title": "Coverage Sync",
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
        "duration_seconds": 125,
        "summary_text": "Hello **team**\n\n- Ship tests\n* Review coverage",
        "action_items_text": "- Alice follows up",
    }
    data.update(overrides)
    return Meeting(**data)


def make_integration() -> Integration:
    return Integration(
        id=22,
        user_id=7,
        provider=IntegrationProvider.MICROSOFT,
        access_token="encrypted-token",
        refresh_token="encrypted-refresh",
        scopes=[],
    )


def test_md_to_simple_html_handles_bold_bullets_and_blank_lines() -> None:
    html = EmailShareService._md_to_simple_html("Intro **bold**\n\n- one\n* two\nTail")

    assert "<strong>bold</strong>" in html
    assert '<ul style="margin:8px 0;padding-left:20px;">' in html
    assert "<li>one</li>" in html
    assert "<li>two</li>" in html
    assert "<br>" in html
    assert "<p style='margin:4px 0;'>Tail</p>" in html
    assert EmailShareService._md_to_simple_html("") == ""


def test_build_summary_html_includes_duration_sections_and_omits_missing_sections() -> None:
    service = EmailShareService()

    html = service.build_summary_html(make_meeting())
    assert "Coverage Sync" in html
    assert "January 02, 2026" in html
    assert "&middot; 2 min" in html
    assert "Summary" in html
    assert "Action Items" in html
    assert "Powered by" in html

    no_sections = service.build_summary_html(
        make_meeting(duration_seconds=None, summary_text=None, action_items_text=None)
    )
    assert "&middot;" not in no_sections
    assert "Summary" not in no_sections
    assert "Action Items" not in no_sections

    sub_minute = service.build_summary_html(make_meeting(duration_seconds=30))
    assert "&lt; 1 min" not in sub_minute
    assert "< 1 min" in sub_minute


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failures", "expected_status", "expected_error"),
    [
        (set(), EmailShareStatus.SENT, None),
        ({"b@example.com"}, EmailShareStatus.PARTIALLY_FAILED, "1/2 failed"),
        ({"a@example.com", "b@example.com"}, EmailShareStatus.FAILED, "2/2 failed"),
    ],
)
async def test_send_to_recipients_records_all_success_partial_and_all_failed(
    monkeypatch: pytest.MonkeyPatch,
    store: ShareStore,
    failures: set[str],
    expected_status: EmailShareStatus,
    expected_error: str | None,
) -> None:
    store.meetings[10] = make_meeting()
    monkeypatch.setattr(email_share_module, "integration_service", FakeIntegrationService(make_integration()))
    FakeGraphClient.failures = failures

    share = await EmailShareService().send_to_recipients(10, 7, [" a@example.com ", "b@example.com"])

    assert share.status == expected_status
    assert share.error_message == expected_error
    assert [recipient["status"] for recipient in share.recipients] == [
        "failed" if recipient["email"] in failures else "sent"
        for recipient in share.recipients
    ]
    assert [recipient["email"] for recipient in share.recipients] == ["a@example.com", "b@example.com"]
    assert share.sent_at is not None
    assert len(FakeGraphClient.sent) == 2
    assert all(call["access_token"] == "decrypted-access-token" for call in FakeGraphClient.sent)
    assert FakeGraphClient.sent[0]["subject"].startswith("Meeting Notes — Coverage Sync")


@pytest.mark.asyncio
async def test_send_to_recipients_raises_for_missing_meeting(store: ShareStore) -> None:
    with pytest.raises(ValueError, match="Meeting 404 not found"):
        await EmailShareService().send_to_recipients(404, 7, ["a@example.com"])


@pytest.mark.asyncio
async def test_send_to_recipients_raises_for_missing_integration(
    monkeypatch: pytest.MonkeyPatch, store: ShareStore
) -> None:
    store.meetings[10] = make_meeting()
    monkeypatch.setattr(email_share_module, "integration_service", FakeIntegrationService(None))

    with pytest.raises(ValueError, match="Microsoft integration not connected"):
        await EmailShareService().send_to_recipients(10, 7, ["a@example.com"])


@pytest.mark.asyncio
async def test_send_to_recipients_returns_pending_share_when_db_update_missing(
    monkeypatch: pytest.MonkeyPatch, store: ShareStore
) -> None:
    store.meetings[10] = make_meeting()
    store.missing_share_on_update = True
    monkeypatch.setattr(email_share_module, "integration_service", FakeIntegrationService(make_integration()))

    share = await EmailShareService().send_to_recipients(10, 7, ["a@example.com"])

    assert share.status == EmailShareStatus.PENDING
    assert share.recipients == [{"email": "a@example.com", "name": "", "status": "pending"}]


def test_get_shares_for_meeting_returns_session_results(store: ShareStore) -> None:
    older = EmailShare(
        id=1,
        meeting_id=10,
        sender_user_id=7,
        integration_id=22,
        recipients=[],
        status=EmailShareStatus.SENT,
        created_at=datetime(2026, 1, 1),
    )
    newer = EmailShare(
        id=2,
        meeting_id=10,
        sender_user_id=7,
        integration_id=22,
        recipients=[],
        status=EmailShareStatus.FAILED,
        created_at=datetime(2026, 1, 2),
    )
    store.persist_share(older)
    store.persist_share(newer)

    shares = EmailShareService().get_shares_for_meeting(10, 7)

    assert shares == [newer, older]
