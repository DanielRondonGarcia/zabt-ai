# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused coverage tests for MicrosoftGraphClient without network access."""

from datetime import timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import microsoft_graph as graph_module
from app.services.microsoft_graph import (
    GRAPH_API_BASE,
    MICROSOFT_AUTH_BASE,
    SCOPES,
    MicrosoftGraphClient,
    MicrosoftGraphError,
    _detect_platform,
    _find_conferencing_url,
    _parse_graph_datetime,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    responses: list[FakeResponse] = []
    calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.responses = []
    FakeAsyncClient.calls = []
    monkeypatch.setattr(graph_module.httpx, "AsyncClient", FakeAsyncClient)


@pytest.fixture(name="client")
def fixture_client() -> MicrosoftGraphClient:
    return MicrosoftGraphClient(
        client_id="client-id",
        client_secret="client-secret",
        tenant_id="tenant-id",
        redirect_uri="https://app.example/callback",
    )


def test_build_auth_url_contains_oauth_parameters_and_scopes(client: MicrosoftGraphClient) -> None:
    url = client.build_auth_url("state-123")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert url.startswith(f"{MICROSOFT_AUTH_BASE}/tenant-id/oauth2/v2.0/authorize")
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["https://app.example/callback"]
    assert params["response_type"] == ["code"]
    assert params["response_mode"] == ["query"]
    assert params["state"] == ["state-123"]
    assert params["scope"] == [" ".join(SCOPES)]


@pytest.mark.asyncio
async def test_exchange_code_success_posts_form_without_exposing_secret(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(200, {"access_token": "at", "refresh_token": "rt"})]

    tokens = await client.exchange_code("code-123")

    assert tokens == {"access_token": "at", "refresh_token": "rt"}
    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == f"{MICROSOFT_AUTH_BASE}/tenant-id/oauth2/v2.0/token"
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "code-123"
    assert kwargs["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.mark.asyncio
async def test_exchange_code_error_raises_graph_error(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(400, text="bad code")]

    with pytest.raises(MicrosoftGraphError) as exc_info:
        await client.exchange_code("bad")

    assert exc_info.value.status_code == 400
    assert "Token exchange failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_refresh_access_token_success_and_error(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(200, {"access_token": "new"})]

    assert await client.refresh_access_token("refresh-token") == {"access_token": "new"}
    assert FakeAsyncClient.calls[0][2]["data"]["grant_type"] == "refresh_token"
    assert FakeAsyncClient.calls[0][2]["data"]["refresh_token"] == "refresh-token"

    FakeAsyncClient.responses = [FakeResponse(401, text="expired")]
    with pytest.raises(MicrosoftGraphError) as exc_info:
        await client.refresh_access_token("expired-refresh")
    assert exc_info.value.status_code == 401
    assert "Token refresh failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_user_profile_normalizes_mail_and_user_principal_name(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [
        FakeResponse(200, {"id": "u1", "mail": "mail@example.com", "displayName": "Mail User"}),
        FakeResponse(200, {"id": "u2", "userPrincipalName": "upn@example.com"}),
    ]

    assert await client.get_user_profile("token") == {
        "id": "u1",
        "email": "mail@example.com",
        "display_name": "Mail User",
    }
    assert await client.get_user_profile("token") == {
        "id": "u2",
        "email": "upn@example.com",
        "display_name": "",
    }
    assert FakeAsyncClient.calls[0][1] == f"{GRAPH_API_BASE}/me"
    assert FakeAsyncClient.calls[0][2]["headers"] == {"Authorization": "Bearer token"}


@pytest.mark.asyncio
async def test_get_user_profile_error_raises_graph_error(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(403, text="forbidden")]

    with pytest.raises(MicrosoftGraphError) as exc_info:
        await client.get_user_profile("token")

    assert exc_info.value.status_code == 403
    assert "Failed to fetch user profile" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_calendar_events_normalizes_events_and_request_shape(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [
        FakeResponse(
            200,
            {
                "@odata.nextLink": "https://graph.example/next-page-is-not-followed",
                "value": [
                    {
                        "id": "evt-1",
                        "subject": "Demo",
                        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"},
                        "organizer": {"emailAddress": {"address": "org@example.com"}},
                        "attendees": [
                            {"emailAddress": {"address": "a@example.com", "name": "A"}}
                        ],
                        "start": {"dateTime": "2026-04-10T10:00:00.1234567"},
                        "end": {"dateTime": "2026-04-10T11:00:00"},
                    },
                    {
                        "id": "evt-2",
                        "subject": None,
                        "location": {"displayName": "Zoom https://acme.zoom.us/j/123"},
                        "start": {"dateTime": "2026-04-10T12:00:00"},
                        "end": {"dateTime": "2026-04-10T12:30:00"},
                    },
                ],
            },
        )
    ]

    events = await client.fetch_calendar_events("calendar-token", window_hours=2)

    assert [event["external_event_id"] for event in events] == ["evt-1", "evt-2"]
    assert events[0]["title"] == "Demo"
    assert events[0]["conferencing_platform"] == "teams"
    assert events[0]["join_url"].startswith("https://teams.microsoft.com/")
    assert events[0]["organizer_email"] == "org@example.com"
    assert events[0]["attendees"] == [{"email": "a@example.com", "name": "A"}]
    assert events[0]["start_time"].microsecond == 123456
    assert events[0]["start_time"].tzinfo == timezone.utc
    assert events[1]["title"] == "Untitled meeting"
    assert events[1]["conferencing_platform"] == "zoom"
    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "GET"
    assert url == f"{GRAPH_API_BASE}/me/calendarview"
    assert kwargs["params"]["$orderby"] == "start/dateTime"
    assert kwargs["params"]["$top"] == "100"
    assert kwargs["headers"]["Prefer"] == 'outlook.timezone="UTC"'


@pytest.mark.asyncio
async def test_fetch_calendar_events_error_raises_graph_error(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(500, text="graph down")]

    with pytest.raises(MicrosoftGraphError) as exc_info:
        await client.fetch_calendar_events("token")

    assert exc_info.value.status_code == 500
    assert "Failed to fetch calendar events" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 202])
async def test_send_email_accepts_graph_success_codes(client: MicrosoftGraphClient, status_code: int) -> None:
    FakeAsyncClient.responses = [FakeResponse(status_code)]

    await client.send_email("token", "to@example.com", "To Name", "Subject", "<p>Body</p>")

    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == f"{GRAPH_API_BASE}/me/sendMail"
    assert kwargs["headers"] == {"Authorization": "Bearer token"}
    assert kwargs["json"]["message"]["toRecipients"][0]["emailAddress"] == {
        "address": "to@example.com",
        "name": "To Name",
    }
    assert kwargs["json"]["saveToSentItems"] is True


@pytest.mark.asyncio
async def test_send_email_error_raises_graph_error(client: MicrosoftGraphClient) -> None:
    FakeAsyncClient.responses = [FakeResponse(429, text="rate limited")]

    with pytest.raises(MicrosoftGraphError) as exc_info:
        await client.send_email("token", "to@example.com", "", "Subject", "Body")

    assert exc_info.value.status_code == 429
    assert "Failed to send email" in str(exc_info.value)


def test_date_and_url_helpers_cover_empty_unknown_and_body_extraction(client: MicrosoftGraphClient) -> None:
    assert _parse_graph_datetime("").tzinfo is not None
    assert _parse_graph_datetime("2026-04-10T10:00:00+00:00").tzinfo is not None
    assert _detect_platform("https://meet.google.com/abc-defg-hij") == "meet"
    assert _detect_platform("https://example.com/nope") is None
    assert _find_conferencing_url("join https://meet.google.com/abc-defg-hij now") == "https://meet.google.com/abc-defg-hij"
    assert _find_conferencing_url("no conferencing here") is None

    url, platform = client._extract_conferencing(
        {"body": {"content": "Please join https://meet.google.com/abc-defg-hij"}}
    )
    assert (url, platform) == ("https://meet.google.com/abc-defg-hij", "meet")
    assert client._extract_conferencing({}) == (None, None)
