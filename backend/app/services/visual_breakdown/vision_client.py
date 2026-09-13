# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Client for zabt-vision-worker. Mirrors GpuTranscriptionClient shape."""
from __future__ import annotations

import logging
import ipaddress
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services.visual_breakdown.types import VisionWorkerResult

logger = logging.getLogger(__name__)


_HEARTBEAT_INTERVAL_SECONDS = 30.0
_HEARTBEAT_JOIN_TIMEOUT_SECONDS = 1.0
HeartbeatCallback = Callable[[], None]


class VisionClientError(RuntimeError):
    """Sanitized provider error that never contains response or URL payloads."""


def _endpoint_host(url: str) -> str | None:
    parsed = urlsplit(url if "://" in url else f"//{url}")
    return parsed.hostname.lower() if parsed.hostname else None


def _is_private_or_internal_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip("[]").lower().rstrip(".")
    if normalized in {"localhost", "host.docker.internal"} or "." not in normalized:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class VisionClient:
    """HTTP/RunPod client for the vision worker.

    Local mode: a single POST to /run with a long timeout (the worker is
    synchronous — it blocks until done and returns the JobResult directly).

    RunPod mode: submit + poll loop matching GpuTranscriptionClient.
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        local_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[float] = None,
    ):
        self._backend = backend or settings.VISION_BACKEND
        self._local_url = local_url or settings.VISION_LOCAL_URL
        self._timeout = timeout if timeout is not None else settings.VISION_TIMEOUT
        self._poll_interval = settings.VISION_POLL_INTERVAL
        self._max_retries = max(
            0,
            int(
                max_retries
                if max_retries is not None
                else getattr(settings, "VISION_MAX_RETRIES", 0)
            ),
        )
        self._retry_backoff_seconds = max(
            0.0,
            float(
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else getattr(settings, "VISION_RETRY_BACKOFF_SECONDS", 0.0)
            ),
        )

        if self._backend == "runpod":
            if not getattr(settings, "VISION_CLOUD_ALLOWED", False):
                raise VisionClientError("cloud vision backend is disabled")
            if not settings.VISION_RUNPOD_API_KEY or not settings.VISION_RUNPOD_ENDPOINT_ID:
                raise VisionClientError("RunPod vision credentials are not configured")
            import runpod

            runpod.api_key = settings.VISION_RUNPOD_API_KEY
            self._endpoint = runpod.Endpoint(settings.VISION_RUNPOD_ENDPOINT_ID)
            logger.info(
                "VisionClient (runpod) endpoint=%s timeout=%ds",
                settings.VISION_RUNPOD_ENDPOINT_ID, self._timeout,
            )
        elif self._backend == "local":
            self._validate_local_endpoint(self._local_url)
            logger.info(
                "VisionClient (local) host=%s timeout=%ds",
                _endpoint_host(self._local_url),
                self._timeout,
            )
        else:
            raise ValueError(f"Unknown VISION_BACKEND: {self._backend}")

    def submit_and_wait(
        self,
        payload: Dict[str, Any],
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> VisionWorkerResult:
        if self._backend == "local":
            return self._run_local(payload, on_heartbeat=on_heartbeat)
        return self._run_runpod(payload, on_heartbeat=on_heartbeat)

    def _run_local(
        self,
        payload: Dict[str, Any],
        *,
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> VisionWorkerResult:
        logger.info("vision-worker /run local meeting_id=%s", payload.get("meeting_id"))
        with httpx.Client() as client:
            for attempt in range(self._max_retries + 1):
                try:
                    with self._heartbeat_loop(
                        on_heartbeat,
                        operation="local vision request",
                    ):
                        resp = client.post(
                            f"{self._local_url}/run",
                            json=payload,
                            timeout=self._timeout,
                        )
                    status_code = getattr(resp, "status_code", None)
                    if isinstance(status_code, int) and status_code >= 500:
                        if attempt < self._max_retries:
                            self._sleep_before_retry(attempt)
                            continue
                        raise VisionClientError(
                            f"vision-worker returned HTTP {status_code}"
                        )
                    if isinstance(status_code, int) and not 200 <= status_code < 300:
                        raise VisionClientError(
                            f"vision-worker returned HTTP {status_code}"
                        )
                    if not isinstance(status_code, int):
                        resp.raise_for_status()
                    try:
                        return VisionWorkerResult.model_validate(resp.json())
                    except Exception as exc:
                        raise VisionClientError(
                            "vision-worker returned a malformed response"
                        ) from exc
                except httpx.TimeoutException:
                    if attempt < self._max_retries:
                        self._sleep_before_retry(attempt)
                        continue
                    raise httpx.TimeoutException("vision-worker request timed out") from None
                except httpx.HTTPStatusError as exc:
                    status_code = getattr(exc.response, "status_code", 0)
                    if status_code >= 500 and attempt < self._max_retries:
                        self._sleep_before_retry(attempt)
                        continue
                    raise VisionClientError(
                        f"vision-worker returned HTTP {status_code or 'unknown'}"
                    ) from exc
                except httpx.RequestError as exc:
                    if attempt < self._max_retries:
                        self._sleep_before_retry(attempt)
                        continue
                    raise VisionClientError("vision-worker request failed") from exc

    @staticmethod
    def _call_heartbeat(callback: HeartbeatCallback, *, operation: str) -> None:
        try:
            callback()
        except Exception:
            logger.warning(
                "Vision heartbeat refresh failed operation=%s",
                operation,
                exc_info=True,
            )

    def _heartbeat_worker(
        self,
        stop_event: threading.Event,
        callback: HeartbeatCallback,
        operation: str,
    ) -> None:
        while not stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            self._call_heartbeat(callback, operation=operation)

    @contextmanager
    def _heartbeat_loop(
        self,
        callback: HeartbeatCallback | None,
        *,
        operation: str,
    ) -> Iterator[None]:
        """Refresh liveness during a blocking provider request, best-effort."""
        if callback is None:
            yield
            return

        stop_event = threading.Event()
        try:
            thread = threading.Thread(
                target=self._heartbeat_worker,
                args=(stop_event, callback, operation),
                name="vision-client-heartbeat",
                daemon=True,
            )
            thread.start()
        except Exception:
            logger.warning(
                "Could not start vision heartbeat loop operation=%s",
                operation,
                exc_info=True,
            )
            yield
            return

        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=_HEARTBEAT_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning(
                    "Vision heartbeat loop did not stop before timeout operation=%s",
                    operation,
                )

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._retry_backoff_seconds * (2**attempt)
        time.sleep(delay)

    @staticmethod
    def _validate_local_endpoint(url: str) -> None:
        host = _endpoint_host(url)
        policy = str(getattr(settings, "VISION_EGRESS_POLICY", "deny")).lower()
        allowed_hosts = {
            value.strip().lower()
            for value in str(getattr(settings, "VISION_ALLOWED_HOSTS", "")).split(",")
            if value.strip()
        }
        if policy not in {"deny", "allowlist", "allow"}:
            raise VisionClientError("invalid vision egress policy")
        if host in allowed_hosts or _is_private_or_internal_host(host):
            return
        if policy == "allow" and getattr(settings, "VISION_CLOUD_ALLOWED", False):
            return
        raise VisionClientError("vision endpoint is not permitted by egress policy")

    def _run_runpod(
        self,
        payload: Dict[str, Any],
        *,
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> VisionWorkerResult:
        logger.info("vision-worker /run runpod meeting_id=%s", payload.get("meeting_id"))
        job = self._endpoint.run({"input": payload})
        deadline = time.time() + self._timeout
        last_heartbeat = time.monotonic() if on_heartbeat else None
        while time.time() < deadline:
            status = job.status()
            if on_heartbeat and last_heartbeat is not None:
                now = time.monotonic()
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                    self._call_heartbeat(on_heartbeat, operation="RunPod vision job")
                    last_heartbeat = now
            if status == "COMPLETED":
                try:
                    return VisionWorkerResult.model_validate(job.output())
                except Exception as exc:
                    raise VisionClientError(
                        "RunPod vision worker returned a malformed response"
                    ) from exc
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"RunPod vision job {status}")
            time.sleep(self._poll_interval)
        try:
            job.cancel()
        except Exception:
            pass
        raise TimeoutError(f"vision-worker timed out after {self._timeout}s")
