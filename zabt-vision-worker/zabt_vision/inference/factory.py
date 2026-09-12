# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import ipaddress
from urllib.parse import urlsplit

from zabt_vision.inference.base import VisionInference
from zabt_vision.inference.ollama_backend import OllamaInference
from zabt_vision.settings import Settings


def _host(url: str) -> str | None:
    parsed = urlsplit(url if "://" in url else f"//{url}")
    return parsed.hostname.lower() if parsed.hostname else None


def _is_private_or_internal(host: str | None) -> bool:
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


def _validate_egress(settings: Settings) -> None:
    policy = settings.vision_egress_policy
    if policy not in {"deny", "allowlist", "allow"}:
        raise ValueError("invalid vision egress policy")
    host = _host(settings.ollama_host)
    allowed_hosts = {
        value.strip().lower()
        for value in settings.vision_allowed_hosts.split(",")
        if value.strip()
    }
    if host in allowed_hosts or _is_private_or_internal(host):
        return
    if policy == "allowlist" and host not in allowed_hosts:
        raise PermissionError("configured inference host is not in the vision allowlist")
    if policy == "allow" and settings.vision_cloud_allowed:
        return
    raise PermissionError("cloud vision egress is disabled by policy")


def make_inference(settings: Settings) -> VisionInference:
    if settings.vision_inference_backend == "ollama":
        if settings.vision_require_vision and not settings.vision_judge_model:
            raise ValueError("a vision-capable model is required")
        _validate_egress(settings)
        return OllamaInference(model=settings.vision_judge_model, host=settings.ollama_host)
    raise NotImplementedError(
        f"Inference backend {settings.vision_inference_backend} not implemented in Plan 1. "
        "Only 'ollama' is supported. See Plan 4 for transformers backend."
    )
