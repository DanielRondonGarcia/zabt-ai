# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Regression tests for backend-specific GPU transcription timeouts."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Keep this focused module independent of repository settings and the real `.env`.
fake_config = ModuleType("app.core.config")
fake_config.settings = SimpleNamespace()
sys.modules["app.core.config"] = fake_config

from app.models import TranscriptionBackend
from app.services.transcription.errors import UnsupportedCapabilityError


from app.services.transcription.gpu_client import GpuTranscriptionClient


gpu_client_module = sys.modules["app.services.transcription.gpu_client"]


def _transcription_config(storage_key: str, transcription_type: str = "general") -> SimpleNamespace:
    return SimpleNamespace(
        storage_key=storage_key,
        min_speakers=1,
        max_speakers=10,
        language=None,
        allowed_languages=None,
        transcription_type=SimpleNamespace(value=transcription_type),
    )


def test_local_backend_uses_gpu_local_timeout():
    config = SimpleNamespace(
        GPU_LOCAL_TIMEOUT=7200,
        RUNPOD_TIMEOUT=1800,
        RUNPOD_POLL_INTERVAL=5,
        GPU_SERVICE_URL="http://gpu-worker:8001",
    )

    with (
        patch.object(gpu_client_module, "settings", config),
        patch.object(gpu_client_module.httpx, "Client"),
    ):
        client = GpuTranscriptionClient(backend=TranscriptionBackend.GPU_LOCAL)

    assert client._timeout == 7200


def test_runpod_backend_retains_runpod_timeout():
    config = SimpleNamespace(
        GPU_LOCAL_TIMEOUT=7200,
        RUNPOD_TIMEOUT=1800,
        RUNPOD_POLL_INTERVAL=5,
        RUNPOD_API_KEY="test-key",
        RUNPOD_ENDPOINT_ID="test-endpoint",
    )
    runpod = MagicMock()

    with (
        patch.object(gpu_client_module, "settings", config),
        patch.dict(sys.modules, {"runpod": runpod}),
    ):
        client = GpuTranscriptionClient(backend=TranscriptionBackend.RUNPOD)

    assert client._timeout == 1800
    runpod.Endpoint.assert_called_once_with("test-endpoint")


def test_local_backend_uses_internal_presigned_download_url():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.GPU_LOCAL
    client._poll_interval = 0
    client._timeout = 60
    client._submit = MagicMock(return_value="job-1")
    client._poll = MagicMock(
        return_value=("COMPLETED", {"text": "ok", "segments": []}, None)
    )
    storage = MagicMock()
    storage.get_presigned_download_url.return_value = "http://minio:9000/internal/audio"

    with patch.dict(
        sys.modules,
        {"app.services.storage": SimpleNamespace(storage=storage)},
    ):
        client.process_audio(
            "/tmp/audio.mp4",
            config=_transcription_config("media/audio.mp4"),
        )

    storage.get_presigned_download_url.assert_called_once_with(
        "media/audio.mp4", expiration=3600
    )
    storage.get_public_presigned_download_url.assert_not_called()
    client._submit.assert_called_once_with(
        {
            "audio_url": "http://minio:9000/internal/audio",
            "min_speakers": 1,
            "max_speakers": 10,
            "transcription_type": "general",
        }
    )


def test_runpod_backend_uses_public_presigned_download_url():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.RUNPOD
    client._poll_interval = 0
    client._timeout = 60
    client._submit = MagicMock(return_value="job-1")
    client._poll = MagicMock(
        return_value=("COMPLETED", {"text": "ok", "segments": []}, None)
    )
    storage = MagicMock()
    storage.get_public_presigned_download_url.return_value = "https://public.example/audio"

    with patch.dict(
        sys.modules,
        {"app.services.storage": SimpleNamespace(storage=storage)},
    ):
        client.process_audio(
            "/tmp/audio.mp4",
            config=_transcription_config("media/audio.mp4"),
        )

    storage.get_public_presigned_download_url.assert_called_once_with(
        "media/audio.mp4", expiration=3600
    )
    storage.get_presigned_download_url.assert_not_called()
    client._submit.assert_called_once_with(
        {
            "audio_url": "https://public.example/audio",
            "min_speakers": 1,
            "max_speakers": 10,
            "transcription_type": "general",
        }
    )


def test_runpod_medical_request_preserves_mediasr_wire_type():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.RUNPOD
    client._poll_interval = 0
    client._timeout = 60
    client._submit = MagicMock(return_value="job-1")
    client._poll = MagicMock(return_value=("COMPLETED", {"text": "ok", "segments": []}, None))
    storage = MagicMock()
    storage.get_public_presigned_download_url.return_value = "https://public.example/audio"

    with patch.dict(
        sys.modules,
        {"app.services.storage": SimpleNamespace(storage=storage)},
    ):
        client.process_audio(
            "/tmp/audio.wav",
            config=_transcription_config("media/audio.wav", transcription_type="medical"),
        )

    client._submit.assert_called_once_with(
        {
            "audio_url": "https://public.example/audio",
            "min_speakers": 1,
            "max_speakers": 10,
            "transcription_type": "medical",
        }
    )


def test_gpu_realtime_remains_explicitly_unsupported():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.GPU_LOCAL
    with pytest.raises(UnsupportedCapabilityError) as raised:
        import asyncio

        asyncio.run(client.transcribe_chunk(b"audio"))
    assert raised.value.capability == "realtime"


def test_local_long_job_times_out_with_local_setting_and_preserves_polling():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.GPU_LOCAL
    client._poll_interval = 0
    client._timeout = 7200
    client._submit = MagicMock(return_value="job-1")
    client._poll = MagicMock(return_value=("IN_PROGRESS", None, None))
    client._cancel = MagicMock()
    status_changes = []
    storage = MagicMock()

    with (
        patch.dict(
            sys.modules,
            {"app.services.storage": SimpleNamespace(storage=storage)},
        ),
        patch("app.services.transcription.gpu_client.time.time", side_effect=[0, 1, 7201]),
        patch("app.services.transcription.gpu_client.time.sleep"),
    ):
        storage.get_presigned_download_url.return_value = "http://gpu-worker/audio"
        with pytest.raises(TimeoutError, match="GPU_LOCAL_TIMEOUT") as exc_info:
            client.process_audio(
                "/tmp/audio.mp4",
                config=_transcription_config("media/audio.mp4"),
                on_status_change=status_changes.append,
            )

    assert "RUNPOD_TIMEOUT" not in str(exc_info.value)
    assert client._submit.call_count == 1
    assert client._poll.call_count == 1
    client._cancel.assert_called_once_with("job-1")
    assert status_changes == ["transcribing", "diarizing"]


def test_long_running_polling_refreshes_the_processing_heartbeat():
    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.GPU_LOCAL
    client._poll_interval = 0
    client._timeout = 60
    client._submit = MagicMock(return_value="job-1")
    client._poll = MagicMock(
        side_effect=[
            ("IN_PROGRESS", None, None),
            ("IN_PROGRESS", None, None),
            ("COMPLETED", {"text": "ok", "segments": []}, None),
        ]
    )
    heartbeat = MagicMock()
    storage = MagicMock()
    storage.get_presigned_download_url.return_value = "http://gpu-worker/audio"

    with (
        patch.dict(
            sys.modules,
            {"app.services.storage": SimpleNamespace(storage=storage)},
        ),
        patch(
            "app.services.transcription.gpu_client.time.time",
            side_effect=[0, 0, 1, 1, 31, 31, 32, 32],
        ),
        patch("app.services.transcription.gpu_client.time.sleep"),
    ):
        client.process_audio(
            "/tmp/audio.mp4",
            config=_transcription_config("media/audio.mp4"),
            on_heartbeat=heartbeat,
        )

    heartbeat.assert_called_once_with()
