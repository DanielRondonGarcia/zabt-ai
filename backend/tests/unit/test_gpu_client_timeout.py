# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Regression tests for backend-specific GPU transcription timeouts."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models import TranscriptionBackend
from app.services.transcription.types import TranscriptionConfig


def test_local_backend_uses_gpu_local_timeout():
    config = SimpleNamespace(
        GPU_LOCAL_TIMEOUT=7200,
        RUNPOD_TIMEOUT=1800,
        RUNPOD_POLL_INTERVAL=5,
        GPU_SERVICE_URL="http://gpu-worker:8001",
    )

    with (
        patch("app.services.transcription.gpu_client.settings", config),
        patch("app.services.transcription.gpu_client.httpx.Client"),
    ):
        from app.services.transcription.gpu_client import GpuTranscriptionClient

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
        patch("app.services.transcription.gpu_client.settings", config),
        patch.dict(sys.modules, {"runpod": runpod}),
    ):
        from app.services.transcription.gpu_client import GpuTranscriptionClient

        client = GpuTranscriptionClient(backend=TranscriptionBackend.RUNPOD)

    assert client._timeout == 1800
    runpod.Endpoint.assert_called_once_with("test-endpoint")


def test_local_long_job_times_out_with_local_setting_and_preserves_polling():
    from app.services.transcription.gpu_client import GpuTranscriptionClient

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
        storage.get_public_presigned_download_url.return_value = "https://example.test/audio"
        with pytest.raises(TimeoutError, match="GPU_LOCAL_TIMEOUT") as exc_info:
            client.process_audio(
                "/tmp/audio.mp4",
                config=TranscriptionConfig(storage_key="media/audio.mp4"),
                on_status_change=status_changes.append,
            )

    assert "RUNPOD_TIMEOUT" not in str(exc_info.value)
    assert client._submit.call_count == 1
    assert client._poll.call_count == 1
    client._cancel.assert_called_once_with("job-1")
    assert status_changes == ["transcribing", "diarizing"]
