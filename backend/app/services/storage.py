# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import Any, Protocol, runtime_checkable
import hashlib
from threading import RLock
import uuid

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings


@runtime_checkable
class StorageProvider(Protocol):
    """Abstract storage interface — all storage backends must satisfy this."""

    @property
    def provider_name(self) -> str: ...

    def generate_presigned_upload_url(
        self, user_id: int, filename: str, content_type: str, expiration: int = 3600
    ) -> tuple[str, str]: ...

    def get_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str: ...

    def get_public_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str: ...

    def get_fresh_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str: ...

    def upload_file(self, file_data: bytes, object_key: str, content_type: str) -> None: ...

    def download_file(self, object_key: str) -> bytes: ...

    def delete_file(self, object_key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`. Returns the count
        deleted. Used for cascading cleanup (e.g., when a meeting is deleted)."""
        ...

    def create_multipart_upload(
        self, user_id: int, filename: str, content_type: str
    ) -> tuple[str, str]:
        """Start an S3 multipart upload. Returns (upload_id, object_key)."""
        ...

    def generate_part_url(
        self, object_key: str, upload_id: str, part_number: int, expiration: int = 3600
    ) -> str:
        """Return a presigned URL for uploading a single part."""
        ...

    def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[dict]
    ) -> None:
        """Finalize a multipart upload. `parts` is a list of {PartNumber, ETag} dicts."""
        ...

    def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        """Abort an in-progress multipart upload (releases all stored parts)."""
        ...

    def list_pending_multipart_uploads(self, older_than_hours: int = 24) -> list[dict]:
        """Return list of {Key, UploadId, Initiated} for uploads older than given hours."""
        ...


def _make_object_key(
    user_id: int, filename: str, storage_kind: str = "minio"
) -> str:
    if storage_kind not in {"minio", "aws_s3"}:
        raise ValueError("Unsupported storage provider for object key.")
    safe_name = f"{uuid.uuid4().hex}_{filename}"
    return f"users/{user_id}/meetings/storage-v1/{storage_kind}/{safe_name}"


def storage_provider_from_object_key(object_key: str):
    """Resolve versioned upload keys; legacy keys belong to MinIO."""
    from app.models.storage_configuration import StorageProvider as StorageKind

    parts = object_key.split("/")
    if len(parts) >= 6 and parts[0] == "users" and parts[2] == "meetings" and parts[3] == "storage-v1":
        try:
            return StorageKind(parts[4])
        except ValueError:
            raise ValueError("Unsupported storage provider marker in object key.") from None
    return StorageKind.MINIO


class MinioStorage:
    """Storage backend using MinIO (local development)."""

    provider_name = "minio"
    storage_kind = "minio"

    def __init__(self) -> None:
        self.bucket = settings.MINIO_BUCKET_NAME
        scheme = "https" if settings.MINIO_SECURE else "http"
        raw_public = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
        if raw_public.startswith("http://") or raw_public.startswith("https://"):
            public_endpoint_url = raw_public
        else:
            public_endpoint_url = f"{scheme}://{raw_public}"

        # Internal client — server-to-server operations
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )

        # Public client — presigned URLs for the browser
        self._public_client = boto3.client(
            "s3",
            endpoint_url=public_endpoint_url,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )

        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.s3_client.create_bucket(Bucket=self.bucket)
            except ClientError as e:
                print(f"Warning: Could not create bucket {self.bucket}: {e}")

    def generate_presigned_upload_url(
        self, user_id: int, filename: str, content_type: str, expiration: int = 3600
    ) -> tuple[str, str]:
        object_key = _make_object_key(user_id, filename, self.storage_kind)
        response = self._public_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expiration,
        )
        return response, object_key

    def get_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expiration,
        )

    def get_public_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        """Presigned URL reachable from outside Docker (for RunPod, browser, etc.)."""
        return self.get_fresh_presigned_download_url(object_key, expiration)

    def get_fresh_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        """Generate a new public URL on every call; never reuse a signed URL."""
        return self._public_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expiration,
        )

    def upload_file(self, file_data: bytes, object_key: str, content_type: str) -> None:
        self.s3_client.put_object(
            Bucket=self.bucket, Key=object_key, Body=file_data, ContentType=content_type
        )

    def download_file(self, object_key: str) -> bytes:
        response = self.s3_client.get_object(Bucket=self.bucket, Key=object_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def delete_file(self, object_key: str) -> None:
        self.s3_client.delete_object(Bucket=self.bucket, Key=object_key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`.

        Returns count deleted. Uses the list_objects_v2 paginator which chunks
        the listing automatically; delete_objects is bounded to 1000 keys/call
        so we ship one batch per page."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if not objs:
                continue
            self.s3_client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
            deleted += len(objs)
        return deleted

    def create_multipart_upload(
        self, user_id: int, filename: str, content_type: str
    ) -> tuple[str, str]:
        object_key = _make_object_key(user_id, filename)
        response = self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            ContentType=content_type,
        )
        return response["UploadId"], object_key

    def generate_part_url(
        self, object_key: str, upload_id: str, part_number: int, expiration: int = 3600
    ) -> str:
        return self._public_client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expiration,
        )

    def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[dict]
    ) -> None:
        self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        self.s3_client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
        )

    def list_pending_multipart_uploads(self, older_than_hours: int = 24) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        pending: list[dict] = []
        paginator = self.s3_client.get_paginator("list_multipart_uploads")
        for page in paginator.paginate(Bucket=self.bucket):
            for upload in page.get("Uploads", []) or []:
                if upload["Initiated"] < cutoff:
                    pending.append({
                        "Key": upload["Key"],
                        "UploadId": upload["UploadId"],
                        "Initiated": upload["Initiated"],
                    })
        return pending


class S3Storage:
    """Storage backend using S3-compatible cloud providers (R2, AWS S3, etc.)."""

    provider_name = "s3"
    storage_kind = "aws_s3"

    def __init__(self, runtime_configuration: Any | None = None) -> None:
        if runtime_configuration is None:
            endpoint_url = settings.S3_ENDPOINT_URL
            if not endpoint_url:
                raise RuntimeError(
                    "STORAGE_PROVIDER is set to 's3' but S3_ENDPOINT_URL is empty. "
                    "Set S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY."
                )
            bucket = settings.S3_BUCKET_NAME
            region = settings.S3_REGION
            access_key_id = settings.S3_ACCESS_KEY_ID
            secret_access_key = settings.S3_SECRET_ACCESS_KEY
            public_url = settings.S3_PUBLIC_URL or endpoint_url
            use_path_style = False
        else:
            from app.models.storage_configuration import StorageProvider as StorageKind

            if runtime_configuration.provider != StorageKind.AWS_S3:
                raise ValueError("S3Storage requires an AWS S3 runtime configuration.")
            bucket = runtime_configuration.aws_bucket
            region = runtime_configuration.aws_region
            access_key_id = runtime_configuration.aws_access_key_id
            secret_access_key = runtime_configuration.aws_secret_access_key
            endpoint_url = runtime_configuration.s3_endpoint_url
            public_url = endpoint_url
            use_path_style = bool(endpoint_url)
            if not all((bucket, region, access_key_id, secret_access_key)):
                raise RuntimeError("AWS S3 runtime configuration is incomplete.")

        self.bucket = bucket
        client_config = (
            Config(signature_version="s3v4", s3={"addressing_style": "path"})
            if use_path_style
            else Config(signature_version="s3v4")
        )

        # Internal client — server-to-server operations
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=client_config,
        )

        # Public client — presigned URLs for the browser
        self._public_client = boto3.client(
            "s3",
            endpoint_url=public_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=client_config,
        )

        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                print(f"Warning: Bucket '{self.bucket}' does not exist on S3 endpoint.")
            else:
                print(f"Warning: Could not verify bucket '{self.bucket}': {e}")

    def generate_presigned_upload_url(
        self, user_id: int, filename: str, content_type: str, expiration: int = 3600
    ) -> tuple[str, str]:
        object_key = _make_object_key(user_id, filename, self.storage_kind)
        response = self._public_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expiration,
        )
        return response, object_key

    def get_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expiration,
        )

    def get_public_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        """Presigned URL reachable from outside Docker (for RunPod, browser, etc.)."""
        return self.get_fresh_presigned_download_url(object_key, expiration)

    def get_fresh_presigned_download_url(self, object_key: str, expiration: int = 3600) -> str:
        """Generate a new public URL on every call; never reuse a signed URL."""
        return self._public_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expiration,
        )

    def upload_file(self, file_data: bytes, object_key: str, content_type: str) -> None:
        self.s3_client.put_object(
            Bucket=self.bucket, Key=object_key, Body=file_data, ContentType=content_type
        )

    def download_file(self, object_key: str) -> bytes:
        response = self.s3_client.get_object(Bucket=self.bucket, Key=object_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def delete_file(self, object_key: str) -> None:
        self.s3_client.delete_object(Bucket=self.bucket, Key=object_key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`.

        Returns count deleted. Uses the list_objects_v2 paginator which chunks
        the listing automatically; delete_objects is bounded to 1000 keys/call
        so we ship one batch per page."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if not objs:
                continue
            self.s3_client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
            deleted += len(objs)
        return deleted

    def create_multipart_upload(
        self, user_id: int, filename: str, content_type: str
    ) -> tuple[str, str]:
        object_key = _make_object_key(user_id, filename, self.storage_kind)
        response = self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            ContentType=content_type,
        )
        return response["UploadId"], object_key

    def generate_part_url(
        self, object_key: str, upload_id: str, part_number: int, expiration: int = 3600
    ) -> str:
        return self._public_client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expiration,
        )

    def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[dict]
    ) -> None:
        self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        self.s3_client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
        )

    def list_pending_multipart_uploads(self, older_than_hours: int = 24) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        pending: list[dict] = []
        paginator = self.s3_client.get_paginator("list_multipart_uploads")
        for page in paginator.paginate(Bucket=self.bucket):
            for upload in page.get("Uploads", []) or []:
                if upload["Initiated"] < cutoff:
                    pending.append({
                        "Key": upload["Key"],
                        "UploadId": upload["UploadId"],
                        "Initiated": upload["Initiated"],
                    })
        return pending


def create_storage() -> StorageProvider:
    """Factory: returns MinioStorage or S3Storage based on STORAGE_PROVIDER setting."""
    provider = settings.STORAGE_PROVIDER.lower()
    if provider == "s3":
        return S3Storage()
    return MinioStorage()


storage: StorageProvider = create_storage()

_storage_cache_lock = RLock()
_minio_storage_cache: MinioStorage | None = None
_runtime_s3_cache_key: tuple[str, ...] | None = None
_runtime_s3_storage_cache: S3Storage | None = None


def _get_minio_storage() -> MinioStorage:
    global _minio_storage_cache
    with _storage_cache_lock:
        if _minio_storage_cache is None:
            current_storage = globals().get("storage")
            _minio_storage_cache = (
                current_storage
                if isinstance(current_storage, MinioStorage)
                else MinioStorage()
            )
        return _minio_storage_cache


def create_storage_from_runtime(runtime_configuration: Any) -> StorageProvider:
    """Build or reuse a storage client from persisted admin settings."""
    from app.models.storage_configuration import StorageProvider as StorageKind

    if runtime_configuration.provider == StorageKind.MINIO:
        return _get_minio_storage()
    if runtime_configuration.provider != StorageKind.AWS_S3:
        raise ValueError("Unsupported runtime storage provider.")

    bucket = runtime_configuration.aws_bucket
    region = runtime_configuration.aws_region
    access_key_id = runtime_configuration.aws_access_key_id
    secret_access_key = runtime_configuration.aws_secret_access_key
    if not all((bucket, region, access_key_id, secret_access_key)):
        raise RuntimeError("AWS S3 runtime configuration is incomplete.")

    secret_digest = hashlib.sha256(secret_access_key.encode("utf-8")).hexdigest()
    endpoint_url = runtime_configuration.s3_endpoint_url
    cache_key = ("aws_s3", bucket, region, access_key_id, secret_digest, endpoint_url)
    global _runtime_s3_cache_key, _runtime_s3_storage_cache
    with _storage_cache_lock:
        if cache_key != _runtime_s3_cache_key:
            _runtime_s3_storage_cache = S3Storage(runtime_configuration)
            _runtime_s3_cache_key = cache_key
        return _runtime_s3_storage_cache


def get_storage_for_provider(provider: str, session: Any | None = None) -> StorageProvider:
    """Resolve a meeting's storage backend without using the active provider."""
    from app.models.storage_configuration import StorageProvider as StorageKind

    requested_provider = StorageKind(provider)
    if requested_provider == StorageKind.MINIO:
        return _get_minio_storage()

    from app.services.storage_configuration import storage_configuration_service

    if session is not None:
        runtime_configuration = storage_configuration_service.get_runtime_configuration(
            session, provider=requested_provider
        )
    else:
        from sqlmodel import Session

        from app.db.engine import engine

        with Session(engine) as database_session:
            runtime_configuration = storage_configuration_service.get_runtime_configuration(
                database_session, provider=requested_provider
            )
    return create_storage_from_runtime(runtime_configuration)


def get_current_storage(session: Any | None = None) -> StorageProvider:
    """Resolve the globally selected provider for a new upload."""
    from app.services.storage_configuration import storage_configuration_service

    if session is not None:
        runtime_configuration = storage_configuration_service.get_runtime_configuration(session)
    else:
        from sqlmodel import Session

        from app.db.engine import engine

        with Session(engine) as database_session:
            runtime_configuration = storage_configuration_service.get_runtime_configuration(
                database_session
            )
    return create_storage_from_runtime(runtime_configuration)


def get_current_storage_provider(session: Any | None = None) -> str:
    """Return the validated globally selected provider for a new meeting."""
    from app.services.storage_configuration import storage_configuration_service

    if session is not None:
        runtime_configuration = storage_configuration_service.get_runtime_configuration(session)
    else:
        from sqlmodel import Session

        from app.db.engine import engine

        with Session(engine) as database_session:
            runtime_configuration = storage_configuration_service.get_runtime_configuration(
                database_session
            )
    return runtime_configuration.provider.value
