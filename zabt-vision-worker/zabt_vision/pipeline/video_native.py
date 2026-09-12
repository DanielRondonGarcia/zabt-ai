# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import json
import logging
import re
from dataclasses import dataclass

from PIL import Image

from zabt_vision.inference.base import VisionInference

logger = logging.getLogger(__name__)


def _safe_error(error: object) -> str:
    message = f"{type(error).__name__}: {error}"
    message = re.sub(r"https?://\S+", "<redacted-url>", message)
    message = re.sub(r"(?i)(token|secret|password|api[_-]?key)=\S+", r"\1=<redacted>", message)
    return re.sub(r"\s+", " ", message).strip()[:160]


@dataclass(frozen=True)
class NativeDetection:
    timestamp_s: float
    caption: str
    reasoning: str


_PROMPT = """You are analyzing a chunk of a screen-recording / product-demo video.
The chunk shows a sequence of frames sampled from a longer video.

Identify every meaningful screen change in this chunk — moments where the visible
application, page, or content meaningfully shifts. Ignore:
- cursor movement
- minor scrolling within the same page
- text being typed into an existing field
- transient UI like tooltips, hover states

For each detected change, output:
- timestamp_ms: integer milliseconds RELATIVE TO THIS CHUNK START (0 to chunk duration)
- caption: <10 words describing what is now on screen, be specific
- reasoning: why this is a meaningful change

Output ONLY valid JSON of this shape:
{"detections": [{"timestamp_ms": <int>, "caption": "<str>", "reasoning": "<str>"}, ...]}"""


def detect_screen_changes_native(
    chunks: list[tuple[float, float, list[Image.Image]]],
    inference: VisionInference,
) -> list[NativeDetection]:
    """Run video-native screen-change detection over chunks of a video.

    `chunks` is a list of (start_s, end_s, frames) tuples where frames is a sampled
    sequence of PIL images representing the chunk. The caller decides sampling rate.

    Each detection's timestamp is rebased into the global video timeline.
    """
    detections: list[NativeDetection] = []
    for chunk_start, chunk_end, frames in chunks:
        try:
            raw = inference.generate(images=frames, prompt=_PROMPT)
            payload = json.loads(raw if isinstance(raw, str) else str(raw))
            detections_payload = payload.get("detections", [])
            if not isinstance(detections_payload, list):
                raise ValueError("detections must be a list")
            for d in detections_payload:
                if not isinstance(d, dict):
                    continue
                ts_ms = int(d["timestamp_ms"])
                relative_s = max(0.0, min(ts_ms / 1000.0, max(0.0, chunk_end - chunk_start)))
                caption = str(d["caption"]).strip()
                if not caption:
                    continue
                detections.append(
                    NativeDetection(
                        timestamp_s=chunk_start + relative_s,
                        caption=caption[:500],
                        reasoning=str(d.get("reasoning", ""))[:500],
                    )
                )
        except Exception as error:
            logger.warning(
                "video-native detection failed for chunk @ %ss: %s",
                chunk_start,
                _safe_error(error),
            )
            continue
    return detections
