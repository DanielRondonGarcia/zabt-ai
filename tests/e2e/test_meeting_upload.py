# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Browser coverage for the primary meeting upload contract.

The API is intercepted so the tests do not require a database, object storage,
or a real account. The direct PUT remains a browser request and is captured to
prove that the presigned-upload contract is unchanged.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("E2E_BASE_URL", os.environ.get("FRONTEND_URL", "http://localhost:3001"))
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "short-video.mp4"


def _json_body(route) -> dict:
    return json.loads(route.request.post_data or "{}")


def _mock_authenticated_dashboard(page: Page) -> tuple[dict, dict]:
    requests = {"presigned": [], "meeting": []}
    put_headers: dict[str, str] = {}

    page.route(
        "**/api/v1/users/me",
        lambda route: route.fulfill(
            json={
                "id": 1,
                "email": "e2e@example.com",
                "full_name": "E2E User",
                "tier": "pro",
                "is_active": True,
                "minutes_used_this_month": 0,
            }
        ),
    )

    def handle_meeting_api(route) -> None:
        request = route.request
        path = urlparse(request.url).path

        if request.method == "GET" and path.endswith("/meetings/"):
            route.fulfill(json=[])
            return

        if request.method == "POST" and path.endswith("/presigned-upload"):
            requests["presigned"].append(_json_body(route))
            route.fulfill(
                json={
                    "upload_url": "https://storage.example.test/presigned-upload",
                    "file_key": "users/1/meetings/e2e-upload.mp4",
                    "storage_provider": "minio",
                }
            )
            return

        if request.method == "POST" and path.rstrip("/").endswith("/meetings"):
            requests["meeting"].append(_json_body(route))
            route.fulfill(json={"id": 9001})
            return

        route.continue_()

    page.route("**/api/v1/meetings**", handle_meeting_api)

    def handle_put(route) -> None:
        if route.request.method == "PUT":
            put_headers.update(route.request.headers)
        route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "PUT, OPTIONS",
                "Access-Control-Allow-Headers": "content-type",
            },
        )

    page.route("https://storage.example.test/presigned-upload", handle_put)
    page.route("**/api/v1/languages", lambda route: route.fulfill(json=[]))
    page.goto(f"{BASE_URL}/")
    expect(page.get_by_role("button", name="Upload a meeting").first).to_be_visible()
    return requests, put_headers


def _open_upload_modal(page: Page) -> None:
    page.get_by_role("button", name="Upload a meeting").first.click()
    expect(page.get_by_text("Transcribe audio and video")).to_be_visible()


def test_upload_modal_opens(page: Page) -> None:
    requests, _ = _mock_authenticated_dashboard(page)
    _open_upload_modal(page)

    expect(page.get_by_text("Select a file to upload")).to_be_visible()
    expect(page.get_by_role("button", name="Browse files")).to_be_visible()
    expect(page.get_by_text("3 of 3 imports left")).to_be_visible()
    assert requests == {"presigned": [], "meeting": []}


def test_upload_cancellation(page: Page) -> None:
    _mock_authenticated_dashboard(page)
    _open_upload_modal(page)

    page.keyboard.press("Escape")
    expect(page.get_by_text("Transcribe audio and video")).not_to_be_visible()


def test_video_upload_preserves_presigned_put_and_propagates_mime(page: Page) -> None:
    requests, put_headers = _mock_authenticated_dashboard(page)
    _open_upload_modal(page)

    with page.expect_file_chooser() as chooser_info:
        page.get_by_role("button", name="Browse files").click()
    chooser_info.value.set_files(str(FIXTURE_PATH))

    expect(page.get_by_text("short-video.mp4")).to_be_visible()
    expect(page.locator(".bg-emerald-500")).to_be_visible(timeout=5_000)

    assert requests["presigned"] == [
        {"filename": "short-video.mp4", "content_type": "video/mp4"}
    ]
    assert requests["meeting"][0]["content_type"] == "video/mp4"
    assert requests["meeting"][0]["file_key"] == "users/1/meetings/e2e-upload.mp4"
    assert put_headers["content-type"] == "video/mp4"


def test_empty_browser_mime_uses_conservative_fallback(page: Page) -> None:
    requests, put_headers = _mock_authenticated_dashboard(page)
    _open_upload_modal(page)

    with page.expect_file_chooser() as chooser_info:
        page.get_by_role("button", name="Browse files").click()
    chooser_info.value.set_files(
        {
            "name": "legacy-video.mp4",
            "mimeType": "",
            "buffer": FIXTURE_PATH.read_bytes(),
        }
    )

    expect(page.get_by_text("legacy-video.mp4")).to_be_visible()
    expect(page.locator(".bg-emerald-500")).to_be_visible(timeout=5_000)

    assert requests["presigned"] == [
        {"filename": "legacy-video.mp4", "content_type": "audio/mpeg"}
    ]
    assert requests["meeting"][0]["content_type"] == ""
    assert put_headers["content-type"] == "audio/mpeg"
