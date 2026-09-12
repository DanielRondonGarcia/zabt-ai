# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""OpenAI Chat Completions backend for visual analysis."""

import base64
import io
import json
from typing import Any, TypeVar

import openai
from PIL import Image
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class OpenAIInferenceError(RuntimeError):
    """Sanitized OpenAI vision error safe to return from the worker."""


def _image_to_data_url(image: Image.Image) -> str:
    """Encode a PIL image as a JPEG data URL for Chat Completions."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class OpenAIInference:
    """Run vision requests through an OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        model: str,
        api_key: str | None,
        base_url: str = "https://api.openai.com/v1",
        image_detail: str = "low",
        max_tokens: int = 1024,
        client: Any | None = None,
    ) -> None:
        if client is None and not api_key:
            raise ValueError("OpenAI vision API key is not configured")

        self.model = model
        self.image_detail = image_detail
        self.max_tokens = max_tokens

        if client is not None:
            self._client = client
            return

        try:
            self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
        except Exception:
            # The SDK may include request configuration in constructor errors.
            raise OpenAIInferenceError("OpenAI vision client initialization failed") from None

    def generate(
        self,
        images: list[Image.Image],
        prompt: str,
        schema: type[T] | None = None,
    ) -> str | T:
        """Generate text or validate a structured response against ``schema``."""
        full_prompt = prompt
        request: dict[str, Any] = {}
        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
            full_prompt = f"{prompt}\n\nReturn ONLY valid JSON matching this schema:\n{schema_json}"
            # JSON mode guarantees valid JSON; Pydantic remains the application-level
            # validator for the existing structured pipeline output.
            request["response_format"] = {"type": "json_object"}

        content: list[dict[str, Any]] = [{"type": "text", "text": full_prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(image), "detail": self.image_detail},
            }
            for image in images
        )

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                **request,
            )
        except Exception as error:
            # Never surface provider text: it can contain URLs, request details, or
            # echoed credentials/payloads. The exception type is bounded and safe.
            raise OpenAIInferenceError(
                f"OpenAI vision request failed ({type(error).__name__})"
            ) from None

        try:
            content = response.choices[0].message.content
        except Exception:
            raise OpenAIInferenceError("OpenAI vision response was empty or malformed") from None

        if not isinstance(content, str) or not content.strip():
            raise OpenAIInferenceError("OpenAI vision response was empty or malformed")

        if schema is None:
            return content

        try:
            return schema.model_validate_json(content)
        except Exception:
            # Pydantic's error may include the complete model response; keep it out of
            # the worker result and logs.
            raise OpenAIInferenceError(
                "OpenAI vision response did not match the requested schema"
            ) from None
