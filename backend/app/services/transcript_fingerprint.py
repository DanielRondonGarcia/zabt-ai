# SPDX-License-Identifier: AGPL-3.0-only
"""Stable, privacy-preserving fingerprints for transcript epochs."""

from hashlib import sha256
from typing import Any


def transcript_fingerprint(transcript_text: Any) -> str | None:
    """Return a short stable hash naming the current non-empty transcript."""
    if not isinstance(transcript_text, str) or not transcript_text:
        return None
    return sha256(transcript_text.encode("utf-8")).hexdigest()[:16]
