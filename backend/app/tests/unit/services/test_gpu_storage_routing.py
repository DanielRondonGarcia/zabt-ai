"""Isolated tests for meeting-scoped GPU and RunPod storage signing."""

from types import SimpleNamespace
from typing import Any

import pytest


class FakeStorage:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, str, int]] = []

    def get_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        self.calls.append(("private", object_key, expiration))
        return f"https://{self.name}/private/{object_key}"

    def get_public_presigned_download_url(
        self, object_key: str, expiration: int = 3600
    ) -> str:
        self.calls.append(("public", object_key, expiration))
        return f"https://{self.name}/public/{object_key}"


@pytest.fixture
def gpu_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    import app.services.storage as storage_module
    from app.services.transcription import registry
    from app.services.transcription.gpu_client import GpuTranscriptionClient
    from app.services.transcription.types import TranscriptionConfig

    return {
        "storage": storage_module,
        "registry": registry,
        "GpuTranscriptionClient": GpuTranscriptionClient,
        "TranscriptionConfig": TranscriptionConfig,
    }


@pytest.mark.parametrize(
    ("backend", "storage_provider"),
    [
        ("gpu-local", None),
        ("runpod", None),
        ("gpu-local", "minio"),
        ("runpod", "minio"),
        ("gpu-local", "aws_s3"),
        ("runpod", "aws_s3"),
    ],
)
def test_gpu_client_signs_audio_with_meeting_storage_context(
    gpu_modules,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    storage_provider: str | None,
) -> None:
    from app.models import TranscriptionBackend

    storage_module = gpu_modules["storage"]
    global_storage = FakeStorage("global")
    minio = FakeStorage("minio")
    aws = FakeStorage("s3")
    resolved_providers: list[str] = []

    def resolve_storage(provider: str) -> FakeStorage:
        resolved_providers.append(provider)
        return aws if provider == "aws_s3" else minio

    monkeypatch.setattr(storage_module, "storage", global_storage)
    monkeypatch.setattr(storage_module, "get_storage_for_provider", resolve_storage)

    client = object.__new__(gpu_modules["GpuTranscriptionClient"])
    client._backend = (
        TranscriptionBackend.GPU_LOCAL
        if backend == "gpu-local"
        else TranscriptionBackend.RUNPOD
    )
    client._timeout = 1
    client._poll_interval = 0
    jobs: list[dict[str, Any]] = []
    result = SimpleNamespace(text="transcribed")
    client._submit = lambda job_input: jobs.append(job_input) or "job-1"
    client._poll = lambda _job_id: ("COMPLETED", {}, None)
    client._parse_response = lambda _output: result

    config_kwargs: dict[str, Any] = {
        "storage_key": "users/91/meetings/audio.mp3",
    }
    if storage_provider is not None:
        config_kwargs["storage_provider"] = storage_provider
    config = gpu_modules["TranscriptionConfig"](**config_kwargs)
    assert client.process_audio("unused-local-path", config) is result

    selected_storage = (
        global_storage
        if storage_provider is None
        else aws if storage_provider == "aws_s3" else minio
    )
    method = "private" if backend == "gpu-local" else "public"
    expected_url = f"https://{selected_storage.name}/{method}/users/91/meetings/audio.mp3"
    assert jobs[0]["audio_url"] == expected_url
    assert selected_storage.calls == [(method, "users/91/meetings/audio.mp3", 3600)]
    assert resolved_providers == ([] if storage_provider is None else [storage_provider])
    if storage_provider is None:
        assert minio.calls == []
        assert aws.calls == []
    else:
        assert global_storage.calls == []
        assert (minio if storage_provider == "aws_s3" else aws).calls == []


@pytest.mark.parametrize("storage_provider", [None, "aws_s3"])
def test_scoped_gpu_provider_forwards_storage_context_to_client(
    gpu_modules,
    monkeypatch: pytest.MonkeyPatch,
    storage_provider: str | None,
) -> None:
    from app.services.transcription.contracts import AudioSource, BatchTranscriptionRequest

    registry = gpu_modules["registry"]
    monkeypatch.setattr(registry, "validate_batch_request", lambda *_args, **_kwargs: None)

    class CapturingClient:
        config = None

        def process_audio(self, _audio_path, config, *_callbacks):
            self.config = config
            return SimpleNamespace(text="transcribed")

    provider = object.__new__(registry.ScopedGpuProvider)
    provider.provider_name = "gpu-local"
    provider.model = None
    provider.capabilities = registry.GPU_CAPABILITIES
    provider._client = CapturingClient()

    request_kwargs: dict[str, Any] = {
        "source": AudioSource.from_storage_key("users/91/meetings/audio.mp3"),
    }
    if storage_provider is not None:
        request_kwargs["storage_provider"] = storage_provider
    provider.transcribe(BatchTranscriptionRequest(**request_kwargs))

    assert provider._client.config.storage_provider == storage_provider
