# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic text canonicalization for embedding inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_text(value: Any) -> str:
    """Trim and collapse whitespace; non-strings are stringified deterministically."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return _WHITESPACE_RE.sub(" ", text.strip())


def flatten_structured_output(value: Any) -> str:
    """Flatten JSON-like structured output using sorted object keys."""
    parts: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, Mapping):
            for key in sorted(node):
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                walk(node[key], child_prefix)
            return
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for index, item in enumerate(node):
                child_prefix = f"{prefix}.{index}" if prefix else str(index)
                walk(item, child_prefix)
            return
        text = canonicalize_text(node)
        if text:
            parts.append(f"{prefix}: {text}" if prefix else text)

    walk(value, "")
    return canonicalize_text(" ".join(parts))
