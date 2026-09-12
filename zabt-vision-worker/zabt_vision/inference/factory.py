# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import ipaddress
from urllib.parse import urlsplit

from zabt_vision.inference.base import VisionInference
from zabt_vision.inference.ollama_backend import OllamaInference
from zabt_vision.inference.openai_backend import OpenAIInference
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


def _validate_egress(
    settings: Settings,
    endpoint_url: str | None = None,
    *,
    cloud: bool = False,
) -> None:
    policy = settings.vision_egress_policy
    if policy not in {"deny", "allowlist", "allow"}:
        raise ValueError("invalid vision egress policy")
    host = _host(endpoint_url or settings.ollama_host)
    if not host:
        raise ValueError("vision inference endpoint has no hostname")
    allowed_hosts = {
        value.strip().lower()
        for value in settings.vision_allowed_hosts.split(",")
        if value.strip()
    }

    if cloud:
        endpoint_scheme = urlsplit(endpoint_url or settings.ollama_host).scheme.lower()
        if endpoint_scheme != "https":
            raise PermissionError("cloud inference endpoint must use https")
        if not settings.vision_cloud_allowed:
            raise PermissionError("cloud vision egress is disabled by policy")
        if policy != "allowlist":
            raise PermissionError("cloud vision egress requires the allowlist policy")
        if host in allowed_hosts:
            return
        raise PermissionError("configured cloud inference host is not in the vision allowlist")

    if host in allowed_hosts or _is_private_or_internal(host):
        return
    if policy == "allowlist":
        raise PermissionError("configured inference host is not in the vision allowlist")
    if policy == "allow" and settings.vision_cloud_allowed:
        return
    raise PermissionError("cloud vision egress is disabled by policy")


def make_inference(settings: Settings) -> VisionInference:
    if settings.vision_inference_backend == "ollama":
        if settings.vision_require_vision and not settings.vision_judge_model:
            raise ValueError("a vision-capable model is required")
        _validate_egress(settings, settings.ollama_host)
        return OllamaInference(model=settings.vision_judge_model, host=settings.ollama_host)
    if settings.vision_inference_backend == "openai":
        if settings.vision_require_vision and not settings.vision_openai_model:
            raise ValueError("a vision-capable model is required")
        _validate_egress(settings, settings.vision_openai_base_url, cloud=True)
        return OpenAIInference(
            model=settings.vision_openai_model,
            api_key=settings.vision_openai_api_key,
            base_url=settings.vision_openai_base_url,
            image_detail=settings.vision_openai_image_detail,
            max_tokens=settings.vision_openai_max_tokens,
        )
    raise NotImplementedError(
        f"Inference backend {settings.vision_inference_backend} is not implemented. "
        "Supported backends are 'ollama' and 'openai'."
    )
