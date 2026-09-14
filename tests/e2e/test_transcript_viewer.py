# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Browser proof for synchronized audio/video transcript playback.

The local fixture server deliberately implements the storage behavior required
by a signed media GET: CORS, ``video/mp4``, byte ranges, and ``206`` responses.
Failure paths are also local and deterministic; no cloud storage is required.
"""

import json
import os
import re
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("E2E_BASE_URL", os.environ.get("FRONTEND_URL", "http://localhost:3001"))
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "short-video.mp4"
MOCK_PATH = Path(__file__).parent / "mocks" / "transcript_mock.json"
MOCK_MEETING = json.loads(MOCK_PATH.read_text(encoding="utf-8"))


class _MediaFixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def _common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header(
            "Access-Control-Expose-Headers",
            "Accept-Ranges, Content-Length, Content-Range, Content-Type",
        )
        self.send_header("Accept-Ranges", "bytes")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._common_headers("text/plain", 0)
        self.end_headers()

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def _serve(self, send_body: bool) -> None:
        path = urlparse(self.path).path
        if path == "/expired.mp4":
            body = b"signed URL expired"
            self.send_response(403)
            self._common_headers("text/plain", len(body))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        if path == "/unsupported.mp4":
            body = b"not a browser-supported media stream"
            self.send_response(200)
            self._common_headers("video/mp4", len(body))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        if path != "/short-video.mp4":
            self.send_response(404)
            self._common_headers("text/plain", 0)
            self.end_headers()
            return

        body = FIXTURE_PATH.read_bytes()
        start = 0
        end = len(body) - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                self.send_response(416)
                self._common_headers("video/mp4", 0)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.end_headers()
                return

            requested_start, requested_end = match.groups()
            if requested_start:
                start = int(requested_start)
            elif requested_end:
                suffix_length = int(requested_end)
                start = max(0, len(body) - suffix_length)
            if requested_end and requested_start:
                end = int(requested_end)
            end = min(end, len(body) - 1)
            if start > end or start >= len(body):
                self.send_response(416)
                self._common_headers("video/mp4", 0)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.end_headers()
                return

            body = body[start : end + 1]
            self.send_response(206)
            self._common_headers("video/mp4", len(body))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(FIXTURE_PATH.read_bytes())}")
        else:
            self.send_response(200)
            self._common_headers("video/mp4", len(body))
        self.end_headers()
        if send_body:
            self.wfile.write(body)


@pytest.fixture()
def media_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive(), "media fixture server did not shut down"


def _meeting(media_url: str, media_type: str | None, meeting_id: int = 999) -> dict:
    result = deepcopy(MOCK_MEETING)
    result.update(
        {
            "id": meeting_id,
            "file_path": media_url,
            "audio_url": media_url,
            "media_type": media_type,
            "duration_seconds": 4,
        }
    )
    return result


def _configure_meeting_routes(page: Page, meetings: dict[int, dict]) -> None:
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
    page.route("**/api/v1/languages", lambda route: route.fulfill(json=[]))

    def handle_meeting(route) -> None:
        path = urlparse(route.request.url).path.rstrip("/")
        for meeting_id, payload in meetings.items():
            if path.endswith(f"/meetings/{meeting_id}"):
                route.fulfill(json=payload)
                return
        if route.request.method == "GET" and path.endswith("/meetings"):
            route.fulfill(json=[])
            return
        route.continue_()

    page.route("**/api/v1/meetings**", handle_meeting)


def _open_transcript(page: Page, meeting_id: int = 999) -> None:
    page.goto(f"{BASE_URL}/meetings/{meeting_id}")
    transcript_tab = page.get_by_role("tab", name="Transcript")
    if transcript_tab.count() == 0:
        transcript_tab = page.get_by_role("button", name="Transcript")
    transcript_tab.click()
    expect(page.get_by_text("specific", exact=True).first).to_be_visible()


def test_short_video_fixture_serves_browser_storage_contract(page: Page, media_server: str) -> None:
    full = page.request.get(f"{media_server}/short-video.mp4", headers={"Origin": BASE_URL})
    assert full.status == 200
    full_headers = {key.lower(): value for key, value in full.headers.items()}
    assert full_headers["content-type"].startswith("video/mp4")
    assert full_headers["access-control-allow-origin"] == "*"
    assert full_headers["accept-ranges"] == "bytes"

    partial = page.request.get(
        f"{media_server}/short-video.mp4",
        headers={"Origin": BASE_URL, "Range": "bytes=0-31"},
    )
    assert partial.status == 206
    partial_headers = {key.lower(): value for key, value in partial.headers.items()}
    assert partial_headers["content-range"].startswith("bytes 0-31/")
    assert len(partial.body()) == 32


def test_video_transcript_viewer_syncs_controls_and_preserves_layout(
    page: Page, media_server: str
) -> None:
    _configure_meeting_routes(page, {999: _meeting(f"{media_server}/short-video.mp4", "video")})
    _open_transcript(page)

    video = page.locator("video")
    expect(video).to_be_visible()
    page.wait_for_function(
        """() => {
            const media = document.querySelector('video');
            return media && media.readyState >= 1 && Number.isFinite(media.duration) && media.duration > 0;
        }"""
    )
    duration = video.evaluate("media => media.duration")
    assert duration > 0

    player = page.locator("div.fixed").filter(has=video)
    expect(player).to_be_visible()
    expect(player.get_by_role("button", name="Play")).to_be_visible()
    expect(player.get_by_text(re.compile(r"^\d{2}:\d{2}$"))).to_have_count(2)

    player.get_by_role("button", name="Play").click()
    expect(player.get_by_role("button", name="Pause")).to_be_visible(timeout=2_000)
    player.get_by_role("button", name="Pause").click()
    expect(player.get_by_role("button", name="Play")).to_be_visible()

    player.get_by_role("button", name="Playback speed 1x").click()
    expect(player.get_by_role("button", name="Playback speed 1.5x")).to_be_visible()
    player.get_by_role("button", name="Playback speed 1.5x").click()
    expect(player.get_by_role("button", name="Playback speed 2x")).to_be_visible()

    timeline = player.locator("div.h-1.cursor-pointer")
    timeline_box = timeline.bounding_box()
    assert timeline_box is not None and timeline_box["width"] > 0
    timeline.click(position={"x": timeline_box["width"] / 2, "y": 1})
    page.wait_for_function("() => document.querySelector('video').currentTime > 0")

    word = page.get_by_text("specific", exact=True).first
    word.click()
    page.wait_for_function("() => document.querySelector('video').currentTime >= 1")
    expect(word).to_have_class(re.compile(r"\bbg-primary\b"))

    page.get_by_role("button", name="Seek to 0:00").click()
    page.wait_for_function("() => document.querySelector('video').currentTime < 0.2")

    page.set_viewport_size({"width": 375, "height": 800})
    player_box = player.bounding_box()
    assert player_box is not None
    assert 0 <= player_box["x"]
    assert player_box["x"] + player_box["width"] <= 375
    assert player_box["y"] + player_box["height"] <= 800
    reservations = page.locator('div[aria-hidden="true"]').evaluate_all(
        "elements => elements.map(element => element.getBoundingClientRect().height)"
    )
    assert max(reservations, default=0) >= player_box["height"] - 1


def test_audio_transcript_regression_keeps_shared_controls_and_seeking(
    page: Page, media_server: str
) -> None:
    _configure_meeting_routes(page, {999: _meeting(f"{media_server}/short-video.mp4", None)})
    _open_transcript(page)

    audio = page.locator("audio")
    expect(audio).to_be_attached()
    expect(audio).to_have_attribute("aria-hidden", "true")
    expect(page.locator("video")).to_have_count(0)

    player = page.locator("div.fixed").filter(has=audio)
    player.get_by_role("button", name="Play").click()
    expect(player.get_by_role("button", name="Pause")).to_be_visible(timeout=2_000)
    player.get_by_role("button", name="Pause").click()
    player.get_by_role("button", name="Playback speed 1x").click()
    expect(player.get_by_role("button", name="Playback speed 1.5x")).to_be_visible()

    word = page.get_by_text("specific", exact=True).first
    word.click()
    page.wait_for_function("() => document.querySelector('audio').currentTime >= 1")
    expect(word).to_have_class(re.compile(r"\bbg-primary\b"))
    expect(page.get_by_text("What are specific AI tools", exact=False)).to_be_visible()


def test_media_identity_cleanup_stops_old_media_and_clears_state(
    page: Page, media_server: str
) -> None:
    page.add_init_script(
        """(() => {
            const originalPause = HTMLMediaElement.prototype.pause;
            HTMLMediaElement.prototype.pause = function () {
                const calls = JSON.parse(sessionStorage.getItem('e2e-media-pauses') || '[]');
                calls.push(this.currentSrc || this.src);
                sessionStorage.setItem('e2e-media-pauses', JSON.stringify(calls));
                return originalPause.call(this);
            };
        })();"""
    )
    _configure_meeting_routes(
        page,
        {
            999: _meeting(f"{media_server}/short-video.mp4", "video", 999),
            1000: _meeting(f"{media_server}/short-video.mp4", None, 1000),
        },
    )
    _open_transcript(page, 999)
    page.locator("video").evaluate("media => { media.muted = true; media.currentTime = 1.5; media.play(); }")

    page.goto(f"{BASE_URL}/meetings/1000")
    expect(page.get_by_text("Transcript", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Transcript").click()
    expect(page.locator("audio")).to_be_attached()
    expect(page.locator("video")).to_have_count(0)
    expect(page.locator("div.fixed").get_by_text("00:00", exact=True).first).to_be_visible()
    pause_calls = page.evaluate("JSON.parse(sessionStorage.getItem('e2e-media-pauses') || '[]')")
    assert any("short-video.mp4" in call for call in pause_calls)


@pytest.mark.parametrize("failure_path", ["expired.mp4", "unsupported.mp4"])
def test_media_failures_are_accessible_without_blocking_transcript(
    page: Page, media_server: str, failure_path: str
) -> None:
    _configure_meeting_routes(page, {999: _meeting(f"{media_server}/{failure_path}", "video")})
    _open_transcript(page)

    status = page.get_by_role("status")
    expect(status).to_contain_text("This media could not be loaded", timeout=5_000)
    word = page.get_by_text("specific", exact=True).first
    expect(word).to_be_visible()
    word.click()
    expect(word).to_have_class(re.compile(r"cursor-pointer"))
