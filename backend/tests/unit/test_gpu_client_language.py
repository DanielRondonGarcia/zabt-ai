# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from app.services.transcription.types import TranscriptionConfig


def test_job_input_includes_language_and_allowed_languages():
    from app.services.transcription.gpu_client import GpuTranscriptionClient
    from app.models import TranscriptionBackend

    client = GpuTranscriptionClient.__new__(GpuTranscriptionClient)
    client._backend = TranscriptionBackend.GPU_LOCAL
    client._http = None
    client._base_url = "http://test"
    client._poll_interval = 0
    client._timeout = 60

    captured = {}

    def fake_submit(input_):
        captured.update(input_)
        return "job-1"

    client._submit = fake_submit
    client._poll = lambda jid: ("COMPLETED", {
        "text": "hi", "language": "ur", "segments": [],
        "provider_name": "x", "recognition_method": "y",
        "audio_duration_seconds": 1.0, "estimated_cost": 0.0,
    }, None)

    storage = MagicMock()
    with patch.dict(
        sys.modules,
        {"app.services.storage": SimpleNamespace(storage=storage)},
    ):
        storage.get_public_presigned_download_url.return_value = "https://x"
        cfg = TranscriptionConfig(
            storage_key="k", language="ur", allowed_languages={"ur", "en"},
        )
        client.process_audio("/tmp/foo", config=cfg)

    assert captured["language"] == "ur"
    assert sorted(captured["allowed_languages"]) == ["en", "ur"]
