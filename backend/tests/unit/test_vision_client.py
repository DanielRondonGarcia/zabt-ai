# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Tests for VisionClient — HTTP local mode (mocked httpx)."""
import threading
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.visual_breakdown.direct_service import (
    CandidateFrame,
    DirectVisionError,
    DirectVisionService,
    OpenAIVisionClient,
    VisualAnalysis,
    VisualAnalysisSegment,
)

# Keep this focused module independent of repository settings and the real `.env`.
fake_config = ModuleType("app.core.config")
fake_config.settings = SimpleNamespace(
    VISION_BACKEND="local",
    VISION_LOCAL_URL="http://worker:8003",
    VISION_TIMEOUT=1800,
    VISION_POLL_INTERVAL=5,
)
original_config = sys.modules.get("app.core.config")
sys.modules["app.core.config"] = fake_config

from app.services.visual_breakdown.types import VisionWorkerResult
from app.services.visual_breakdown.vision_client import VisionClient, VisionClientError

if original_config is not None:
    sys.modules["app.core.config"] = original_config
else:
    del sys.modules["app.core.config"]


def _make_client_with_mocked_httpx(post_response: dict | Exception):
    """Returns (client, mock_post) tuple. post_response is either a dict to
    return as JSON, or an Exception to raise from .post()."""
    fake_resp = MagicMock()
    if isinstance(post_response, Exception):
        post_method = MagicMock(side_effect=post_response)
    else:
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = post_response
        post_method = MagicMock(return_value=fake_resp)

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(post=post_method))
    cm.__exit__ = MagicMock(return_value=False)

    return cm, post_method


def test_submit_and_wait_local_returns_parsed_result():
    cm, post_method = _make_client_with_mocked_httpx({
        "status": "completed",
        "segments": [],
        "raw_output_s3_key": "k",
        "model": "qwen3-vl:8b-thinking",
        "params": {"fps": 2},
        "stage_metrics": {"extract_frames": {"duration_ms": 100}},
    })
    with patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm):
        client = VisionClient(backend="local", local_url="http://worker:8003", timeout=1800)
        result = client.submit_and_wait({
            "video_url": "x", "owner_id": "u", "meeting_id": "m",
        })

    assert isinstance(result, VisionWorkerResult)
    assert result.status == "completed"
    assert result.model == "qwen3-vl:8b-thinking"
    post_method.assert_called_once()
    args, kwargs = post_method.call_args
    assert args[0] == "http://worker:8003/run"
    assert kwargs["timeout"] == 1800


def test_local_request_refreshes_heartbeat_while_blocked_and_stops_afterward():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "status": "completed",
        "segments": [],
        "model": "qwen3-vl:8b-thinking",
        "params": {},
        "stage_metrics": {},
    }
    heartbeat_started = threading.Event()

    def heartbeat():
        heartbeat_started.set()

    http_client = MagicMock()

    def post(*args, **kwargs):
        assert heartbeat_started.wait(timeout=1)
        return response

    http_client.post.side_effect = post
    cm = MagicMock()
    cm.__enter__.return_value = http_client
    cm.__exit__.return_value = False

    with (
        patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm),
        patch("app.services.visual_breakdown.vision_client._HEARTBEAT_INTERVAL_SECONDS", 0.001),
    ):
        client = VisionClient(backend="local", local_url="http://worker:8003")
        result = client.submit_and_wait(
            {"video_url": "x", "owner_id": "u", "meeting_id": "m"},
            on_heartbeat=heartbeat,
        )

    assert result.status == "completed"
    assert http_client.post.call_count == 1
    heartbeat_count_after_request = heartbeat_started.is_set()
    time.sleep(0.02)
    assert heartbeat_count_after_request is True
    assert not any(
        thread.name == "vision-client-heartbeat" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_local_heartbeat_error_is_logged_without_failing_request(caplog):
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "status": "completed",
        "segments": [],
        "model": "qwen3-vl:8b-thinking",
        "params": {},
        "stage_metrics": {},
    }
    heartbeat_started = threading.Event()

    def heartbeat():
        heartbeat_started.set()
        raise RuntimeError("database unavailable")

    http_client = MagicMock()

    def post(*args, **kwargs):
        assert heartbeat_started.wait(timeout=1)
        return response

    http_client.post.side_effect = post
    cm = MagicMock()
    cm.__enter__.return_value = http_client
    cm.__exit__.return_value = False

    with (
        patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm),
        patch("app.services.visual_breakdown.vision_client._HEARTBEAT_INTERVAL_SECONDS", 0.001),
        caplog.at_level("WARNING"),
    ):
        client = VisionClient(backend="local", local_url="http://worker:8003")
        result = client.submit_and_wait(
            {"video_url": "x", "owner_id": "u", "meeting_id": "m"},
            on_heartbeat=heartbeat,
        )

    assert result.status == "completed"
    assert "heartbeat refresh failed" in caplog.text


def test_submit_and_wait_local_raises_on_timeout():
    cm, _ = _make_client_with_mocked_httpx(httpx.TimeoutException("timeout"))
    with patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm):
        client = VisionClient(
            backend="local",
            local_url="http://worker:8003",
            timeout=10,
            max_retries=0,
            retry_backoff_seconds=0,
        )
        with pytest.raises(httpx.TimeoutException):
            client.submit_and_wait({"video_url": "x", "owner_id": "u", "meeting_id": "m"})


def test_submit_and_wait_local_returns_failed_status_when_worker_says_so():
    cm, _ = _make_client_with_mocked_httpx({
        "status": "failed",
        "segments": [],
        "model": "qwen3-vl:8b-thinking",
        "params": {},
        "stage_metrics": {},
        "error": "ffmpeg blew up",
        "failed_stage": "extract_frames",
    })
    with patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm):
        client = VisionClient(backend="local", local_url="http://worker:8003")
        result = client.submit_and_wait({"video_url": "x", "owner_id": "u", "meeting_id": "m"})

    assert result.status == "failed"
    assert result.failed_stage == "extract_frames"
    assert "ffmpeg" in result.error


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        VisionClient(backend="bogus")


def test_local_timeout_retries_only_within_configured_bound_and_redacts_url():
    cm, post_method = _make_client_with_mocked_httpx(
        httpx.TimeoutException("signed-secret-url")
    )
    with (
        patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm),
        patch("app.services.visual_breakdown.vision_client.time.sleep") as sleep,
    ):
        client = VisionClient(
            backend="local",
            local_url="http://worker:8003",
            timeout=10,
            max_retries=2,
            retry_backoff_seconds=0,
        )
        with pytest.raises(httpx.TimeoutException) as exc_info:
            client.submit_and_wait(
                {"video_url": "https://signed-secret-url", "owner_id": "u", "meeting_id": "m"}
            )

    assert post_method.call_count == 3
    assert sleep.call_count == 2
    assert "signed-secret-url" not in str(exc_info.value)


def test_local_non_2xx_is_bounded_and_does_not_expose_response_body():
    response = MagicMock(status_code=503, text="provider-secret=sensitive")
    cm = MagicMock()
    post_method = MagicMock(return_value=response)
    cm.__enter__ = MagicMock(return_value=MagicMock(post=post_method))
    cm.__exit__ = MagicMock(return_value=False)

    with (
        patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm),
        patch("app.services.visual_breakdown.vision_client.time.sleep"),
    ):
        client = VisionClient(
            backend="local",
            local_url="http://worker:8003",
            max_retries=1,
            retry_backoff_seconds=0,
        )
        with pytest.raises(VisionClientError) as exc_info:
            client.submit_and_wait({"video_url": "x", "owner_id": "u", "meeting_id": "m"})

    assert post_method.call_count == 2
    assert "provider-secret" not in str(exc_info.value)
    assert "sensitive" not in str(exc_info.value)


def test_malformed_worker_response_is_non_retryable_and_sanitized():
    cm, post_method = _make_client_with_mocked_httpx(
        {"status": "completed", "error": "api-key=secret"}
    )
    with patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm):
        client = VisionClient(
            backend="local",
            local_url="http://worker:8003",
            max_retries=3,
            retry_backoff_seconds=0,
        )
        with pytest.raises(VisionClientError) as exc_info:
            client.submit_and_wait({"video_url": "x", "owner_id": "u", "meeting_id": "m"})

    assert post_method.call_count == 1
    assert "api-key" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_local_failure_never_switches_to_runpod():
    cm, _ = _make_client_with_mocked_httpx(httpx.TimeoutException("timeout"))
    with (
        patch("app.services.visual_breakdown.vision_client.httpx.Client", return_value=cm),
        patch.object(VisionClient, "_run_runpod") as runpod,
    ):
        client = VisionClient(
            backend="local",
            local_url="http://worker:8003",
            max_retries=0,
            retry_backoff_seconds=0,
        )
        with pytest.raises(httpx.TimeoutException):
            client.submit_and_wait({"video_url": "x", "owner_id": "u", "meeting_id": "m"})

    runpod.assert_not_called()


def test_runpod_polling_refreshes_heartbeat_and_swallows_errors():
    client = VisionClient.__new__(VisionClient)
    client._backend = "runpod"
    client._poll_interval = 0
    client._timeout = 10
    job = MagicMock()
    job.status.side_effect = ["IN_PROGRESS", "COMPLETED"]
    job.output.return_value = {
        "status": "completed",
        "segments": [],
        "model": "qwen3-vl:8b-thinking",
        "params": {},
        "stage_metrics": {},
    }
    client._endpoint = MagicMock()
    client._endpoint.run.return_value = job
    heartbeat = MagicMock(side_effect=RuntimeError("database unavailable"))

    with (
        patch("app.services.visual_breakdown.vision_client._HEARTBEAT_INTERVAL_SECONDS", 0),
        patch("app.services.visual_breakdown.vision_client.time.sleep"),
    ):
        result = client.submit_and_wait(
            {"video_url": "x", "owner_id": "u", "meeting_id": "m"},
            on_heartbeat=heartbeat,
        )

    assert result.status == "completed"
    heartbeat.assert_called()


def test_direct_openai_requires_explicit_visual_credentials_without_summary_fallbacks():
    config = SimpleNamespace(
        OPENAI_API_KEY="summary-only-key",
        OPENAI_BASE_URL="https://summary.example/v1",
        OPENAI_MODEL="summary-model",
        VISION_CLOUD_ALLOWED=True,
        VISION_EGRESS_POLICY="allowlist",
        VISION_ALLOWED_HOSTS="summary.example",
    )

    with patch("app.services.visual_breakdown.direct_service.OpenAI") as openai:
        with pytest.raises(
            DirectVisionError,
            match="OpenAI vision API key is not configured",
        ):
            OpenAIVisionClient(config=config)

    openai.assert_not_called()


def test_direct_openai_requires_explicit_visual_base_url_without_summary_fallback():
    config = SimpleNamespace(
        VISION_OPENAI_API_KEY="vision-key",
        OPENAI_API_KEY="summary-key",
        OPENAI_BASE_URL="https://summary.example/v1",
        OPENAI_MODEL="summary-model",
        VISION_CLOUD_ALLOWED=True,
        VISION_EGRESS_POLICY="allowlist",
        VISION_ALLOWED_HOSTS="summary.example",
    )

    with patch("app.services.visual_breakdown.direct_service.OpenAI") as openai:
        with pytest.raises(
            DirectVisionError,
            match="OpenAI vision base URL is not configured",
        ):
            OpenAIVisionClient(config=config)

    openai.assert_not_called()


def test_direct_openai_initializes_with_explicit_visual_configuration():
    config = SimpleNamespace(
        VISION_OPENAI_API_KEY="vision-key",
        VISION_OPENAI_BASE_URL="https://vision.example/v1",
        VISION_OPENAI_MODEL="vision-model",
        VISION_CLOUD_ALLOWED=True,
        VISION_EGRESS_POLICY="allowlist",
        VISION_ALLOWED_HOSTS="vision.example",
    )
    fake_client = MagicMock()

    with patch(
        "app.services.visual_breakdown.direct_service.OpenAI",
        return_value=fake_client,
    ) as openai:
        client = OpenAIVisionClient(config=config)

    assert client.model == "vision-model"
    openai.assert_called_once_with(
        base_url="https://vision.example/v1",
        api_key="vision-key",
        max_retries=0,
        timeout=1800.0,
    )


def test_direct_openai_request_uses_bounded_base64_images_and_parses_structured_output():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"segments":[{"frame_index":0,"caption":"Dashboard","confidence":0.94}]}'
                )
            )
        ]
    )
    config = SimpleNamespace(
        VISION_OPENAI_MODEL="gpt-4o-mini",
        VISION_OPENAI_IMAGE_DETAIL="high",
        VISION_OPENAI_MAX_TOKENS=321,
        VISION_MAX_CANDIDATE_FRAMES=4,
        VISION_MAX_FRAME_BYTES=1000,
        VISION_MAX_TRANSCRIPT_CHARS=100,
        VISION_MAX_SEGMENTS=4,
    )
    client = OpenAIVisionClient(config=config, client=fake_client, max_retries=0)

    result = client.analyze(
        [CandidateFrame(frame_index=3, timestamp_s=1.5, image=b"jpeg-bytes")],
        [{"speaker": "A", "text": "Here is the dashboard", "start": 1.0}],
    )

    assert isinstance(result, VisualAnalysis)
    assert isinstance(result.segments[0], VisualAnalysisSegment)
    assert result.segments[0].caption == "Dashboard"
    request = fake_client.chat.completions.create.call_args.kwargs
    assert request["model"] == "gpt-4o-mini"
    assert request["max_tokens"] == 321
    assert request["response_format"] == {"type": "json_object"}
    content = request["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["detail"] == "high"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "signed" not in str(request)


def test_direct_openai_provider_errors_are_sanitized_and_retries_are_bounded():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError(
        "POST https://api.openai.com/v1/chat/completions?api_key=secret"
    )
    sleep = MagicMock()
    client = OpenAIVisionClient(
        config=SimpleNamespace(
            VISION_OPENAI_MODEL="gpt-4o-mini",
            VISION_OPENAI_IMAGE_DETAIL="low",
            VISION_OPENAI_MAX_TOKENS=100,
            VISION_MAX_CANDIDATE_FRAMES=2,
            VISION_MAX_FRAME_BYTES=1000,
        ),
        client=fake_client,
        max_retries=1,
        retry_backoff_seconds=0,
        sleep=sleep,
    )

    with pytest.raises(DirectVisionError) as exc_info:
        client.analyze([CandidateFrame(frame_index=0, timestamp_s=0.0, image=b"frame")], [])

    assert fake_client.chat.completions.create.call_count == 2
    sleep.assert_called_once_with(0)
    assert "api.openai.com" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


class _FakeVisualStorage:
    def __init__(self, *, fail_on_upload: int | None = None):
        self.downloads: list[str] = []
        self.uploads: list[tuple[bytes, str, str]] = []
        self.deleted: list[str] = []
        self.fail_on_upload = fail_on_upload

    def download_file(self, object_key: str) -> bytes:
        self.downloads.append(object_key)
        return b"media"

    def upload_file(self, file_data: bytes, object_key: str, content_type: str) -> None:
        self.uploads.append((file_data, object_key, content_type))
        if self.fail_on_upload == len(self.uploads):
            raise RuntimeError("storage write failed")

    def delete_file(self, object_key: str) -> None:
        self.deleted.append(object_key)


class _FakeVisualInference:
    def analyze(self, candidates, transcript, *, on_heartbeat=None):
        return VisualAnalysis(
            segments=[
                VisualAnalysisSegment(
                    frame_index=0,
                    caption="Application dashboard",
                    confidence=0.95,
                )
            ]
        )


def _direct_service_config():
    return SimpleNamespace(
        VISION_FPS=2,
        VISION_MAX_FRAMES=10,
        VISION_MAX_CANDIDATE_FRAMES=4,
        VISION_MAX_SEGMENTS=4,
        VISION_MAX_FRAME_BYTES=1000,
        VISION_MAX_MEDIA_BYTES=1000,
        VISION_CHANGE_THRESHOLD=0.1,
        VISION_CONFIDENCE_THRESHOLD=0.7,
        VISION_OPENAI_MODEL="gpt-4o-mini",
        VISION_MAX_TRANSCRIPT_CHARS=100,
    )


def test_direct_visual_service_downloads_media_and_persists_compatible_artifacts():
    storage = _FakeVisualStorage()
    service = DirectVisionService(
        storage_provider=storage,
        inference=_FakeVisualInference(),
        config=_direct_service_config(),
    )
    service._probe_media = MagicMock(
        return_value=SimpleNamespace(duration_s=2.0, has_video=True, video_codec="h264", media_format="mp4")
    )
    service._extract_frames = MagicMock(return_value=[b"frame-0", b"frame-1"])
    service._extract_thumbnails = MagicMock(return_value=[b"\x00" * 8, b"\xff" * 8])

    result = service.submit_and_wait(
        {
            "file_path": "users/1/meetings/7/source.mp4",
            "owner_id": "1",
            "meeting_id": "7",
            "params": {"content_type": "video/mp4"},
            "transcript": [],
        }
    )

    assert result.status == "completed"
    assert len(result.segments) == 1
    assert storage.downloads == ["users/1/meetings/7/source.mp4"]
    assert len(storage.uploads) == 2
    assert all("http" not in str(upload) for upload in storage.uploads)
    assert result.raw_output_s3_key == storage.uploads[-1][1]


def test_direct_visual_service_skips_audio_without_downloading_or_calling_inference():
    storage = _FakeVisualStorage()
    inference = MagicMock()
    service = DirectVisionService(
        storage_provider=storage,
        inference=inference,
        config=_direct_service_config(),
    )

    result = service.submit_and_wait(
        {"file_path": "audio.m4a", "params": {"content_type": "audio/mp4"}}
    )

    assert result.status == "completed"
    assert result.segments == []
    assert result.params["skip_reason"] == "audio_only"
    assert storage.downloads == []
    inference.analyze.assert_not_called()


def test_direct_visual_service_cleans_uploaded_artifacts_after_persistence_failure():
    storage = _FakeVisualStorage(fail_on_upload=2)
    service = DirectVisionService(
        storage_provider=storage,
        inference=_FakeVisualInference(),
        config=_direct_service_config(),
    )
    service._probe_media = MagicMock(
        return_value=SimpleNamespace(duration_s=2.0, has_video=True, video_codec="h264", media_format="mp4")
    )
    service._extract_frames = MagicMock(return_value=[b"frame-0", b"frame-1"])
    service._extract_thumbnails = MagicMock(return_value=[b"\x00" * 8, b"\xff" * 8])

    result = service.submit_and_wait(
        {
            "file_path": "users/1/meetings/7/source.mp4",
            "owner_id": "1",
            "meeting_id": "7",
            "params": {"content_type": "video/mp4"},
        }
    )

    assert result.status == "failed"
    assert storage.deleted == [upload[1] for upload in storage.uploads]
