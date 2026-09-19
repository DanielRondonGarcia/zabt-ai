# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused storage-service tests using fake S3 clients only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import sys
from typing import Any

from botocore.exceptions import ClientError
import pytest


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def paginate(self, **kwargs: Any):
        self.calls.append(kwargs)
        yield from self.pages


class FakeS3Client:
    def __init__(self, *, head_error: ClientError | None = None) -> None:
        self.head_error = head_error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.paginators: dict[str, FakePaginator] = {}
        self.presign_count = 0

    def head_bucket(self, **kwargs: Any) -> None:
        self.calls.append(("head_bucket", kwargs))
        if self.head_error is not None:
            raise self.head_error

    def create_bucket(self, **kwargs: Any) -> None:
        self.calls.append(("create_bucket", kwargs))

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.presign_count += 1
        self.calls.append(("generate_presigned_url", {"operation": operation, **kwargs}))
        params = kwargs.get("Params", {})
        key = params.get("Key", "no-key")
        return f"url-{self.presign_count}:{operation}:{key}:{kwargs.get('ExpiresIn')}"

    def put_object(self, **kwargs: Any) -> None:
        self.calls.append(("put_object", kwargs))

    def delete_object(self, **kwargs: Any) -> None:
        self.calls.append(("delete_object", kwargs))

    def delete_objects(self, **kwargs: Any) -> None:
        self.calls.append(("delete_objects", kwargs))

    def get_paginator(self, name: str) -> FakePaginator:
        self.calls.append(("get_paginator", {"name": name}))
        return self.paginators[name]

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("create_multipart_upload", kwargs))
        return {"UploadId": "upload-123"}

    def complete_multipart_upload(self, **kwargs: Any) -> None:
        self.calls.append(("complete_multipart_upload", kwargs))

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.calls.append(("abort_multipart_upload", kwargs))


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "HeadBucket")


def load_storage(monkeypatch: pytest.MonkeyPatch, clients: list[FakeS3Client], **overrides: Any):
    """Patch settings and boto3 before importing storage, including module global setup."""
    import boto3
    from app.core.config import settings

    defaults = {
        "STORAGE_PROVIDER": "minio",
        "MINIO_BUCKET_NAME": "minio-bucket",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_PUBLIC_ENDPOINT": "public-minio:9000",
        "MINIO_SECURE": False,
        "MINIO_ACCESS_KEY": "test-access",
        "MINIO_SECRET_KEY": "test-secret",
        "S3_BUCKET_NAME": "s3-bucket",
        "S3_ENDPOINT_URL": "https://s3.internal.example",
        "S3_PUBLIC_URL": "https://s3.public.example",
        "S3_ACCESS_KEY_ID": "s3-access",
        "S3_SECRET_ACCESS_KEY": "s3-secret",
        "S3_REGION": "us-test-1",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value, raising=False)

    created: list[dict[str, Any]] = []

    def fake_boto3_client(*args: Any, **kwargs: Any) -> FakeS3Client:
        if not clients:
            raise AssertionError("Unexpected boto3.client call")
        created.append({"args": args, "kwargs": kwargs})
        return clients.pop(0)

    monkeypatch.setattr(boto3, "client", fake_boto3_client)
    sys.modules.pop("app.services.storage", None)
    module = importlib.import_module("app.services.storage")
    module._created_boto3_clients = created
    return module


def construct_minio(monkeypatch: pytest.MonkeyPatch, **overrides: Any):
    global_internal = FakeS3Client()
    global_public = FakeS3Client()
    internal = FakeS3Client()
    public = FakeS3Client()
    module = load_storage(monkeypatch, [global_internal, global_public, internal, public], **overrides)
    return module, module.MinioStorage(), internal, public


def construct_s3(monkeypatch: pytest.MonkeyPatch, *, head_error: ClientError | None = None, **overrides: Any):
    global_internal = FakeS3Client()
    global_public = FakeS3Client()
    internal = FakeS3Client(head_error=head_error)
    public = FakeS3Client()
    module = load_storage(
        monkeypatch,
        [global_internal, global_public, internal, public],
        STORAGE_PROVIDER="minio",
        **overrides,
    )
    return module, module.S3Storage(), internal, public


def test_make_object_key_uses_user_meeting_prefix_and_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_storage(monkeypatch, [FakeS3Client(), FakeS3Client()])
    monkeypatch.setattr(module.uuid, "uuid4", lambda: type("UUID", (), {"hex": "abc123"})())

    assert module._make_object_key(42, "visit.mp3") == "users/42/meetings/abc123_visit.mp3"


def test_minio_constructs_internal_and_public_clients_and_creates_missing_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_internal = FakeS3Client()
    global_public = FakeS3Client()
    internal = FakeS3Client(head_error=client_error("NoSuchBucket"))
    public = FakeS3Client()

    module = load_storage(
        monkeypatch,
        [global_internal, global_public, internal, public],
        MINIO_ENDPOINT="internal:9000",
        MINIO_PUBLIC_ENDPOINT="https://public.example",
        MINIO_BUCKET_NAME="media",
    )
    storage = module.MinioStorage()

    assert storage.bucket == "media"
    assert module._created_boto3_clients[-2]["kwargs"]["endpoint_url"] == "http://internal:9000"
    assert module._created_boto3_clients[-1]["kwargs"]["endpoint_url"] == "https://public.example"
    assert ("head_bucket", {"Bucket": "media"}) in internal.calls
    assert ("create_bucket", {"Bucket": "media"}) in internal.calls


def test_presigned_urls_use_expected_clients_params_and_are_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    module, storage, internal, public = construct_minio(monkeypatch)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: type("UUID", (), {"hex": "fixed"})())

    upload_url, key = storage.generate_presigned_upload_url(7, "chart.png", "image/png", 99)
    download_url = storage.get_presigned_download_url("k1", 88)
    public_url = storage.get_public_presigned_download_url("k2", 77)
    fresh_url_1 = storage.get_fresh_presigned_download_url("k2", 77)
    fresh_url_2 = storage.get_fresh_presigned_download_url("k2", 77)

    assert key == "users/7/meetings/fixed_chart.png"
    assert upload_url.startswith("url-1:put_object:users/7/meetings/fixed_chart.png:99")
    assert download_url == "url-1:get_object:k1:88"
    assert public_url == "url-2:get_object:k2:77"
    assert fresh_url_1 != fresh_url_2
    assert internal.calls[-1] == (
        "generate_presigned_url",
        {
            "operation": "get_object",
            "Params": {"Bucket": "minio-bucket", "Key": "k1"},
            "ExpiresIn": 88,
        },
    )
    assert public.calls[0][1]["operation"] == "put_object"
    assert public.calls[0][1]["Params"]["ContentType"] == "image/png"


def test_put_delete_and_delete_prefix_handles_empty_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _module, storage, internal, _public = construct_minio(monkeypatch)
    paginator = FakePaginator([
        {},
        {"Contents": []},
        {"Contents": [{"Key": "p/a"}, {"Key": "p/b"}]},
    ])
    internal.paginators["list_objects_v2"] = paginator

    storage.upload_file(b"data", "p/file", "text/plain")
    storage.delete_file("p/file")
    deleted = storage.delete_prefix("p/")

    assert deleted == 2
    assert ("put_object", {"Bucket": "minio-bucket", "Key": "p/file", "Body": b"data", "ContentType": "text/plain"}) in internal.calls
    assert ("delete_object", {"Bucket": "minio-bucket", "Key": "p/file"}) in internal.calls
    assert paginator.calls == [{"Bucket": "minio-bucket", "Prefix": "p/"}]
    assert internal.calls[-1] == (
        "delete_objects",
        {"Bucket": "minio-bucket", "Delete": {"Objects": [{"Key": "p/a"}, {"Key": "p/b"}]}},
    )


def test_multipart_lifecycle_and_pending_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    module, storage, internal, public = construct_minio(monkeypatch)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: type("UUID", (), {"hex": "mp"})())
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    internal.paginators["list_multipart_uploads"] = FakePaginator([
        {},
        {"Uploads": None},
        {"Uploads": [
            {"Key": "old-key", "UploadId": "old-upload", "Initiated": old},
            {"Key": "recent-key", "UploadId": "recent-upload", "Initiated": recent},
        ]},
    ])

    upload_id, key = storage.create_multipart_upload(5, "scan.mov", "video/quicktime")
    part_url = storage.generate_part_url(key, upload_id, 3, 123)
    parts = [{"PartNumber": 1, "ETag": "etag-1"}]
    storage.complete_multipart_upload(key, upload_id, parts)
    storage.abort_multipart_upload(key, upload_id)
    pending = storage.list_pending_multipart_uploads(older_than_hours=24)

    assert upload_id == "upload-123"
    assert key == "users/5/meetings/mp_scan.mov"
    assert part_url == f"url-1:upload_part:{key}:123"
    assert pending == [{"Key": "old-key", "UploadId": "old-upload", "Initiated": old}]
    assert ("create_multipart_upload", {"Bucket": "minio-bucket", "Key": key, "ContentType": "video/quicktime"}) in internal.calls
    assert public.calls[0][1]["Params"] == {
        "Bucket": "minio-bucket",
        "Key": key,
        "UploadId": "upload-123",
        "PartNumber": 3,
    }
    assert ("complete_multipart_upload", {"Bucket": "minio-bucket", "Key": key, "UploadId": "upload-123", "MultipartUpload": {"Parts": parts}}) in internal.calls
    assert ("abort_multipart_upload", {"Bucket": "minio-bucket", "Key": key, "UploadId": "upload-123"}) in internal.calls


def test_s3_head_bucket_404_and_non_404_are_warnings_not_creates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _module, _storage, internal_404, _public = construct_s3(monkeypatch, head_error=client_error("404"))
    output_404 = capsys.readouterr().out

    _module, _storage, internal_500, _public = construct_s3(monkeypatch, head_error=client_error("500"))
    output_500 = capsys.readouterr().out

    assert "does not exist on S3 endpoint" in output_404
    assert "Could not verify bucket" in output_500
    assert not [call for call in internal_404.calls if call[0] == "create_bucket"]
    assert not [call for call in internal_500.calls if call[0] == "create_bucket"]


def test_s3_rejects_missing_endpoint_before_creating_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_storage(monkeypatch, [FakeS3Client(), FakeS3Client()], STORAGE_PROVIDER="minio")
    monkeypatch.setattr(module.settings, "S3_ENDPOINT_URL", "", raising=False)

    with pytest.raises(RuntimeError, match="S3_ENDPOINT_URL is empty"):
        module.S3Storage()


def test_factory_selects_provider_and_instances_match_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_storage(
        monkeypatch,
        [FakeS3Client(), FakeS3Client(), FakeS3Client(), FakeS3Client(), FakeS3Client(), FakeS3Client()],
        STORAGE_PROVIDER="s3",
    )

    s3_storage = module.create_storage()
    monkeypatch.setattr(module.settings, "STORAGE_PROVIDER", "minio", raising=False)
    minio_storage = module.create_storage()

    assert s3_storage.provider_name == "s3"
    assert minio_storage.provider_name == "minio"
    assert isinstance(s3_storage, module.StorageProvider)
    assert isinstance(minio_storage, module.StorageProvider)
