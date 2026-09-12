# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from zabt_vision.inference.base import VisionInference
from zabt_vision.pipeline.candidates import generate_candidates
from zabt_vision.pipeline.cross_validate import JudgedKeyframe, cross_validate
from zabt_vision.pipeline.extract_frames import extract_frames
from zabt_vision.pipeline.refine_boundaries import refine_boundaries
from zabt_vision.pipeline.signals.ocr_diff import compute_ocr_signal
from zabt_vision.pipeline.signals.phash import compute_phash_signal
from zabt_vision.pipeline.signals.scene_detect import compute_scene_signal
from zabt_vision.pipeline.signals.transcript_hints import compute_transcript_hint_signal
from zabt_vision.pipeline.upload import upload_keyframe_jpg, upload_raw_output_json
from zabt_vision.pipeline.video_native import detect_screen_changes_native
from zabt_vision.settings import Settings
from zabt_vision.types import JobInput, JobResult, MediaProbe, VisualSegment

logger = logging.getLogger(__name__)


class PipelineStageError(Exception):
    """Wraps an exception raised inside a named pipeline stage so the caller
    (server.py / Celery task) can populate JobResult.failed_stage for telemetry."""

    def __init__(self, stage: str, original: Exception):
        super().__init__(f"stage={stage}: {type(original).__name__}: {original}")
        self.stage = stage
        self.original = original


def download_video(url: str, dest: Path) -> Path:
    """Download a video URL to disk via curl. Supports presigned S3 URLs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        src = Path(url[len("file://") :])
        shutil.copy2(src, dest)
        return dest
    subprocess.run(["curl", "-fsSL", "-o", str(dest), url], check=True)
    return dest


def video_duration_seconds(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe returned a non-zero status")
    try:
        return float((completed.stdout or "").strip())
    except (TypeError, ValueError) as error:
        raise RuntimeError("ffprobe returned invalid duration") from error


def probe_media(path: Path) -> MediaProbe:
    """Probe media with a fixed argv-only ffprobe command.

    The command never uses a shell and failures intentionally discard stderr so
    signed URLs, local paths, and provider payloads cannot reach API responses.
    """
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe returned a non-zero status")
    try:
        payload = json.loads(completed.stdout or "")
    except (TypeError, ValueError) as error:
        raise RuntimeError("ffprobe returned invalid metadata") from error

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    format_data = payload.get("format") or {}
    try:
        duration = float(format_data.get("duration") or 0.0)
    except (TypeError, ValueError) as error:
        raise RuntimeError("ffprobe returned an invalid duration") from error
    return MediaProbe(
        duration_s=max(0.0, duration),
        mime_type=(format_data.get("format_name") or None),
        video_codec=(video_stream or {}).get("codec_name"),
        has_video=video_stream is not None,
    )


def _safe_error(error: object) -> str:
    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}"
    else:
        message = str(error)
    message = re.sub(r"https?://\S+", "<redacted-url>", message)
    message = re.sub(
        r"(?i)(token|secret|password|api[_-]?key)=\S+",
        r"\1=<redacted>",
        message,
    )
    return re.sub(r"\s+", " ", message).strip()[:200]


def _is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _media_kind(job: JobInput) -> str | None:
    values = [
        job.media_type,
        job.params.get("media_type"),
        job.params.get("mime_type"),
        job.params.get("content_type"),
        job.params.get("media_kind"),
        job.params.get("source_type"),
    ]
    for value in values:
        if not value:
            continue
        normalized = str(value).strip().lower()
        if normalized == "youtube" or _is_youtube_url(job.video_url):
            return "youtube"
        if normalized == "audio" or normalized.startswith("audio/"):
            return "audio"
        if normalized == "video" or normalized.startswith("video/"):
            return "video"
    if _is_youtube_url(job.video_url):
        return "youtube"
    return None


def _skip_result(job: JobInput, settings: Settings, reason: str) -> JobResult:
    return JobResult(
        status="completed",
        segments=[],
        model=settings.effective_vision_model,
        params={"skip_reason": reason},
        stage_metrics={"skipped": {"reason": reason}},
    )


def _select_candidate_frames(frame_records, images, candidates):
    """Return one sampled frame per signal candidate for VLM analysis."""
    if not candidates or not frame_records:
        return [], []
    selected: list[tuple[float, int]] = []
    seen: set[int] = set()
    for candidate in candidates:
        index = min(
            range(len(frame_records)),
            key=lambda i: abs(frame_records[i].timestamp_s - candidate.timestamp_s),
        )
        if index not in seen:
            seen.add(index)
            selected.append((frame_records[index].timestamp_s, index))
    selected.sort()
    return [frame_records[index] for _timestamp, index in selected], [
        images[index] for _timestamp, index in selected
    ]


def _load_frames_as_images(frame_records) -> list[Image.Image]:
    return [Image.open(f.path).convert("RGB") for f in frame_records]


def _make_chunks(
    frame_records,
    images: list[Image.Image],
    chunk_seconds: int,
) -> list[tuple[float, float, list[Image.Image]]]:
    chunks: list[tuple[float, float, list[Image.Image]]] = []
    if not frame_records:
        return chunks
    spacing = next(
        (
            later.timestamp_s - earlier.timestamp_s
            for earlier, later in pairwise(frame_records)
            if later.timestamp_s > earlier.timestamp_s
        ),
        1.0,
    )
    start_idx = 0
    chunk_start_t = frame_records[0].timestamp_s
    for i, fr in enumerate(frame_records):
        if fr.timestamp_s - chunk_start_t >= chunk_seconds:
            chunks.append((chunk_start_t, fr.timestamp_s, images[start_idx:i]))
            start_idx = i
            chunk_start_t = fr.timestamp_s
    chunks.append((chunk_start_t, frame_records[-1].timestamp_s + spacing, images[start_idx:]))
    return chunks


def _make_rescan(video_path: Path, work_dir: Path):
    def rescan(center_s: float, window: float, fps: int):
        out_dir = work_dir / f"rescan_{center_s:.2f}"
        out_dir.mkdir(parents=True, exist_ok=True)
        start = max(0.0, center_s - window)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start}",
            "-i",
            str(video_path),
            "-t",
            f"{window * 2}",
            "-vf",
            f"fps={fps}",
            "-q:v",
            "2",
            "-loglevel",
            "error",
            str(out_dir / "frame_%06d.jpg"),
        ]
        subprocess.run(cmd, check=True)
        files = sorted(out_dir.glob("frame_*.jpg"))
        return [
            (start + i * (1.0 / fps), Image.open(p).convert("RGB")) for i, p in enumerate(files)
        ]

    return rescan


def _run_stage(name: str, fn, *args, **kwargs):
    """Run a pipeline stage, wrapping any exception in PipelineStageError so
    the caller can populate JobResult.failed_stage."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        raise PipelineStageError(stage=name, original=e) from e


def _run_pipeline(
    job: JobInput,
    settings: Settings,
    inference: VisionInference,
    s3_client,
) -> JobResult:
    """End-to-end visual breakdown pipeline. Returns a JobResult.

    Stage failures raise PipelineStageError; the caller (server.py / Celery)
    should catch it and surface `e.stage` as JobResult.failed_stage.
    """
    work_dir = Path(settings.work_dir) / job.meeting_id
    work_dir.mkdir(parents=True, exist_ok=True)
    stage_metrics: dict[str, dict] = {}

    kind = _media_kind(job)
    if kind in {"audio", "youtube"}:
        reason = "audio_only" if kind == "audio" else "youtube_audio_only"
        return _skip_result(job, settings, reason)

    # Stage 1: extract frames
    t0 = time.perf_counter()
    video_path = _run_stage("extract_frames", download_video, job.video_url, work_dir / "video.mp4")
    if video_path.exists():
        probe = _run_stage("extract_frames", probe_media, video_path)
        if not probe.has_video:
            return _skip_result(job, settings, "audio_only")
        duration = probe.duration_s
    else:
        # Unit callers may provide a mocked downloader and duration without a
        # real file.  Production downloads always exist and use the probe above.
        duration = _run_stage("extract_frames", video_duration_seconds, video_path)
        probe = MediaProbe(duration_s=duration, has_video=True)
    fps = job.params.get("fps", settings.fps)
    frame_records = _run_stage(
        "extract_frames", extract_frames, video_path, work_dir / "frames", fps=fps
    )
    images = _run_stage("extract_frames", _load_frames_as_images, frame_records)
    stage_metrics["extract_frames"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "frame_count": len(frame_records),
        "fps": fps,
        "video_duration_s": duration,
        "media_codec": probe.video_codec,
        "media_format": probe.mime_type,
    }

    # Stage 2: signals + candidates
    t0 = time.perf_counter()
    phash = _run_stage("compute_signals", compute_phash_signal, images)
    ocr = _run_stage("compute_signals", compute_ocr_signal, images, use_gpu=settings.ocr_use_gpu)
    scene = _run_stage(
        "compute_signals",
        compute_scene_signal,
        video_path,
        threshold=job.params.get("scenedetect_threshold", settings.scenedetect_threshold),
    )
    hints = _run_stage("compute_signals", compute_transcript_hint_signal, job.transcript)
    candidates = _run_stage(
        "compute_signals",
        generate_candidates,
        frame_timestamps=[f.timestamp_s for f in frame_records],
        phash_distances=phash,
        ocr_distances=ocr,
        scene_boundaries=scene,
        transcript_hints=hints,
        phash_threshold=job.params.get("phash_threshold", settings.phash_threshold),
        ocr_threshold=job.params.get("ocr_diff_threshold", settings.ocr_diff_threshold),
        min_signals=job.params.get("ensemble_min_signals", settings.ensemble_min_signals),
    )
    stage_metrics["compute_signals"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "candidate_count": len(candidates),
        "phash_candidates": sum(1 for d in phash if d >= settings.phash_threshold),
        "ocr_candidates": sum(1 for d in ocr if d >= settings.ocr_diff_threshold),
        "scenedetect_candidates": len(scene),
        "transcript_candidates": len(hints),
    }

    # Stage 3: video-native detection
    t0 = time.perf_counter()
    sampled_records, sampled_images = _select_candidate_frames(
        frame_records, images, candidates
    )
    chunks = _make_chunks(sampled_records, sampled_images, settings.chunk_seconds)
    natives = _run_stage(
        "video_native_detection", detect_screen_changes_native, chunks=chunks, inference=inference
    )
    stage_metrics["video_native_detection"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "chunks_processed": len(chunks),
        "sampled_frame_count": len(sampled_records),
        "detections_count": len(natives),
    }

    # Stage 4: cross-validate
    t0 = time.perf_counter()
    frames_by_ts = {f.timestamp_s: img for f, img in zip(frame_records, images, strict=False)}
    judged = _run_stage(
        "cross_validate",
        cross_validate,
        candidates=candidates,
        native_detections=natives,
        frames_by_timestamp=frames_by_ts,
        transcript=job.transcript,
        inference=inference,
        confidence_threshold=job.params.get("confidence_threshold", settings.confidence_threshold),
    )
    stage_metrics["cross_validate"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "candidates_evaluated": len(candidates),
        "kept_count": len(judged),
        "rejected_count": len(candidates) - len(judged),
        "mean_confidence": (sum(k.confidence for k in judged) / len(judged)) if judged else 0.0,
    }

    # Stage 5: boundary refinement
    t0 = time.perf_counter()
    rescan = _make_rescan(video_path, work_dir)
    refined = _run_stage(
        "boundary_refinement",
        refine_boundaries,
        keyframes=judged,
        rescan_window=rescan,
        window_seconds=settings.refinement_window_seconds,
        fps=settings.refinement_fps,
    )
    stage_metrics["boundary_refinement"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "boundaries_refined": len(refined),
        "mean_adjustment_ms": int(
            1000
            * (
                sum(
                    abs(r.timestamp_s - j.timestamp_s)
                    for r, j in zip(refined, judged, strict=False)
                )
                / len(refined)
            )
        )
        if refined
        else 0,
    }

    # Build VisualSegments — each segment runs from its keyframe to the next (or end-of-video)
    segments: list[VisualSegment] = []
    boundaries = sorted(refined, key=lambda k: k.timestamp_s)
    # Always start at 0.0 with the first detected screen as the opening segment
    if boundaries and boundaries[0].timestamp_s > 0.5:
        # Insert an implicit opening segment from 0 to first boundary using the first
        # detected keyframe's caption (best available approximation)
        boundaries = [
            JudgedKeyframe(
                timestamp_s=0.0,
                caption=boundaries[0].caption + " (opening)",
                confidence=boundaries[0].confidence,
                reasoning="implicit opening segment before first detected change",
            ),
            *boundaries,
        ]

    for i, kf in enumerate(boundaries):
        end_time = boundaries[i + 1].timestamp_s if i + 1 < len(boundaries) else duration
        seg_id = uuid.uuid4().hex
        # Find nearest extracted frame to the keyframe timestamp for the screenshot
        nearest = min(frames_by_ts.keys(), key=lambda t: abs(t - kf.timestamp_s))
        screenshot_key = upload_keyframe_jpg(
            client=s3_client,
            bucket=settings.s3_bucket,
            owner_id=job.owner_id,
            meeting_id=job.meeting_id,
            segment_id=seg_id,
            image=frames_by_ts[nearest],
        )
        segments.append(
            VisualSegment(
                id=seg_id,
                sequence=i,
                start_time=kf.timestamp_s,
                end_time=end_time,
                screenshot_s3_key=screenshot_key,
                caption=kf.caption,
                confidence=kf.confidence,
            )
        )

    raw_payload = {
        "segments": [s.model_dump() for s in segments],
        "stage_metrics": stage_metrics,
        "candidates": [
            {"timestamp_s": c.timestamp_s, "signals": sorted(c.signals_fired)} for c in candidates
        ],
        "natives": [{"timestamp_s": n.timestamp_s, "caption": n.caption} for n in natives],
        "judged": [
            {"timestamp_s": k.timestamp_s, "caption": k.caption, "confidence": k.confidence}
            for k in judged
        ],
        "refined": [{"timestamp_s": r.timestamp_s, "caption": r.caption} for r in refined],
    }
    raw_key = upload_raw_output_json(
        client=s3_client,
        bucket=settings.s3_bucket,
        owner_id=job.owner_id,
        meeting_id=job.meeting_id,
        payload=raw_payload,
    )

    return JobResult(
        status="completed",
        segments=segments,
        raw_output_s3_key=raw_key,
        model=settings.effective_vision_model,
        params=dict(job.params)
        | {
            "fps": fps,
            "phash_threshold": settings.phash_threshold,
            "ocr_diff_threshold": settings.ocr_diff_threshold,
            "ensemble_min_signals": settings.ensemble_min_signals,
            "confidence_threshold": settings.confidence_threshold,
        },
        stage_metrics=stage_metrics,
    )


def run_pipeline(
    job: JobInput,
    settings: Settings,
    inference: VisionInference,
    s3_client,
) -> JobResult:
    """Run the pipeline and return a sanitized bounded outcome."""
    try:
        return _run_pipeline(
            job=job,
            settings=settings,
            inference=inference,
            s3_client=s3_client,
        )
    except PipelineStageError as error:
        return JobResult(
            status="failed",
            segments=[],
            model=settings.effective_vision_model,
            params={},
            failed_stage=error.stage,
            error=_safe_error(error.original),
        )
    except Exception as error:
        return JobResult(
            status="failed",
            segments=[],
            model=settings.effective_vision_model,
            params={},
            error=_safe_error(error),
        )
