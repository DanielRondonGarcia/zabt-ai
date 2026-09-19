# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Typed contracts for the visual breakdown result shared with the worker stage."""
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class VisualSegmentResponse(BaseModel):
    id: str               # segment uuid hex (becomes part of the S3 key)
    sequence: int
    start_time: float
    end_time: float
    screenshot_s3_key: str
    caption: str
    confidence: float


class VisionWorkerResult(BaseModel):
    status: str  # "completed" | "failed"
    segments: List[VisualSegmentResponse] = Field(default_factory=list)
    raw_output_s3_key: Optional[str] = None
    model: str
    params: Dict[str, Any] = Field(default_factory=dict)
    stage_metrics: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None
    failed_stage: Optional[str] = None


@dataclass(frozen=True)
class VisualStageOutcome:
    """Immutable outcome shared by successful, skipped, and fallback paths."""

    status: Literal["completed", "skipped", "fallback"]
    reason: str | None = None
    warning_code: str | None = None
    segment_count: int = 0
    idempotency_key: str = ""
    attempts: int = 0
