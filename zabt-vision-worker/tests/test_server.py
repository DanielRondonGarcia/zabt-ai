# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from zabt_vision.pipeline.run import PipelineStageError
from zabt_vision.server import app
from zabt_vision.settings import Settings
from zabt_vision.types import JobResult, VisualSegment


@pytest.fixture
def client():
    with patch("zabt_vision.server.get_settings", return_value=Settings(vision_enabled=True)):
        yield TestClient(app)


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_synchronous_run_endpoint(client):
    fake_result = JobResult(
        status="completed",
        segments=[
            VisualSegment(
                id="s1",
                sequence=0,
                start_time=0.0,
                end_time=10.0,
                screenshot_s3_key="k",
                caption="X",
                confidence=0.9,
            )
        ],
        raw_output_s3_key="raw",
        model="qwen3-vl:8b-thinking",
        params={"fps": 2},
        stage_metrics={"extract_frames": {"duration_ms": 100}},
    )
    with (
        patch("zabt_vision.server.run_pipeline", return_value=fake_result),
        patch("zabt_vision.server.make_inference", return_value=MagicMock()),
        patch("zabt_vision.server.make_s3_client", return_value=MagicMock()),
    ):
        r = client.post(
            "/run",
            json={
                "video_url": "file:///tmp/x.mp4",
                "owner_id": "u1",
                "meeting_id": "m1",
                "transcript": [],
                "params": {},
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert len(body["segments"]) == 1


def test_run_returns_sanitized_disabled_result_without_running_pipeline(client):
    with (
        patch("zabt_vision.server.get_settings", return_value=Settings(vision_enabled=False)),
        patch("zabt_vision.server.run_pipeline") as pipeline,
        patch("zabt_vision.server.make_inference") as make_inference,
        patch("zabt_vision.server.make_s3_client") as make_s3_client,
    ):
        response = client.post(
            "/run",
            json={
                "video_url": "https://signed.example/video.mp4?token=secret",
                "owner_id": "u1",
                "meeting_id": "m1",
                "transcript": [],
                "params": {"api_key": "secret"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "visual processing is disabled"
    assert body["params"] == {}
    assert "secret" not in response.text
    pipeline.assert_not_called()
    make_inference.assert_not_called()
    make_s3_client.assert_not_called()


def test_run_returns_failed_status_on_exception(client):
    with (
        patch("zabt_vision.server.run_pipeline", side_effect=RuntimeError("ffmpeg blew up")),
        patch("zabt_vision.server.make_inference", return_value=MagicMock()),
        patch("zabt_vision.server.make_s3_client", return_value=MagicMock()),
    ):
        r = client.post(
            "/run",
            json={
                "video_url": "x",
                "owner_id": "u",
                "meeting_id": "m",
                "transcript": [],
                "params": {},
            },
        )
    assert r.status_code == 200  # surface failure in body, not HTTP
    body = r.json()
    assert body["status"] == "failed"
    assert "ffmpeg" in body["error"]


def test_invalid_input_uses_sanitized_fallback(client):
    with (
        patch(
            "zabt_vision.server.run_pipeline",
            side_effect=ValueError("invalid media token=secret"),
        ),
        patch("zabt_vision.server.make_inference", return_value=MagicMock()),
        patch("zabt_vision.server.make_s3_client", return_value=MagicMock()),
    ):
        response = client.post(
            "/run",
            json={
                "video_url": "https://signed.example/video.mp4?token=secret",
                "owner_id": "u1",
                "meeting_id": "m1",
                "transcript": [],
                "params": {"media_kind": "unknown"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "secret" not in response.text


def test_nonzero_ffprobe_failure_keeps_stage_and_hides_command_details(client):
    with (
        patch(
            "zabt_vision.server.run_pipeline",
            side_effect=PipelineStageError(
                "extract_frames", RuntimeError("ffprobe stderr token=secret")
            ),
        ),
        patch("zabt_vision.server.make_inference", return_value=MagicMock()),
        patch("zabt_vision.server.make_s3_client", return_value=MagicMock()),
    ):
        response = client.post(
            "/run",
            json={
                "video_url": "https://signed.example/video.mp4?token=secret",
                "owner_id": "u1",
                "meeting_id": "m1",
                "transcript": [],
                "params": {},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failed_stage"] == "extract_frames"
    assert "secret" not in response.text
