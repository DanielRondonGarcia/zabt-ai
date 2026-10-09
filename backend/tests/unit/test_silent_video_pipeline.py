# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Regression coverage for video-only transcription pipeline behavior."""

import json
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

import app.worker as worker
from app.db.engine import engine
from app.models import Group, Meeting, TranscriptSegment, VisualSegment
from app.models.meeting_intelligence import MeetingHighlight
from app.services.transcription.types import ResultSegment, TranscriptionResult


@pytest.fixture
def local_video_meeting(db: Session, tmp_path, monkeypatch: pytest.MonkeyPatch):
    group = Group(owner_id=1, name="Silent video group")
    db.add(group)
    db.commit()
    db.refresh(group)

    meeting = Meeting(
        owner_id=1,
        title="Silent video",
        group_id=group.id,
        file_path="users/1/meetings/silent/video.mp4",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    monkeypatch.setattr(worker, "TEMP_DIR", str(tmp_path))
    media_path = tmp_path / f"zabt_meeting_{meeting.id}.mp4"
    media_path.write_bytes(b"synthetic media")

    yield meeting.id, media_path, group.id

    with Session(engine) as session:
        for model in (TranscriptSegment, MeetingHighlight, VisualSegment):
            for row in session.exec(select(model).where(model.meeting_id == meeting.id)).all():
                session.delete(row)
        current = session.get(Meeting, meeting.id)
        if current is not None:
            session.delete(current)
        current_group = session.get(Group, group.id)
        if current_group is not None:
            session.delete(current_group)
        session.commit()


def _probe_result(*stream_types: str, duration: str = "42.5") -> CompletedProcess:
    return CompletedProcess(
        ["ffprobe"],
        0,
        stdout=json.dumps(
            {
                "streams": [{"codec_type": stream_type} for stream_type in stream_types],
                "format": {"duration": duration},
            }
        ),
        stderr="",
    )


def _transcription_result(text: str = "spoken result") -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        language="en",
        segments=[ResultSegment(start=0.0, end=2.0, text=text, speaker="SPEAKER_00")],
        provider_name="fake-provider",
        recognition_method="test",
        audio_duration_seconds=42.5,
        estimated_cost=None,
    )


def test_video_without_audio_skips_provider_and_clears_stale_transcript(
    local_video_meeting,
    db: Session,
):
    meeting_id, media_path, group_id = local_video_meeting
    meeting = db.get(Meeting, meeting_id)
    meeting.description = "Keep this description"
    meeting.requested_language = "en"
    meeting.transcript_text = "stale transcript"
    meeting.transliterated_text = "stale transliteration"
    meeting.summary_text = "stale summary"
    meeting.original_summary_text = "stale original summary"
    meeting.summary_edited = True
    meeting.action_items_text = "stale action items"
    meeting.structured_output = {"stale": True}
    meeting.structured_output_status = "completed"
    db.add(
        TranscriptSegment(
            meeting_id=meeting_id,
            start_time=0.0,
            end_time=2.0,
            text="stale transcript",
            speaker="SPEAKER_00",
        )
    )
    db.add(
        MeetingHighlight(
            meeting_id=meeting_id,
            highlight_type="action_item",
            content="stale highlight",
            timestamp_start=0.0,
        )
    )
    db.add(
        VisualSegment(
            meeting_id=meeting_id,
            start_time=0.0,
            end_time=5.0,
            screenshot_s3_key="users/1/meetings/silent/visual/current.jpg",
            caption="current visual context",
            sequence=0,
            confidence=0.9,
        )
    )
    db.commit()

    probe_calls = []

    def fake_run(command, **kwargs):
        probe_calls.append((command, kwargs))
        return _probe_result("video")

    provider = MagicMock()
    analytics_capture = MagicMock()
    notify = MagicMock()
    with (
        patch.object(worker, "_configured_transcription_provider", return_value="openai-file"),
        patch.object(worker.subprocess, "run", side_effect=fake_run),
        patch.object(worker, "get_provider", return_value=provider) as get_provider,
        patch.object(worker.analytics, "capture", analytics_capture),
        patch.object(worker, "notify", notify),
        patch.object(worker.meeting_service, "_publish_sub_status"),
        patch.object(worker, "_run_visual_breakdown", return_value=meeting_id) as run_visual,
    ):
        assert worker.stage_transcribe.run(meeting_id) == meeting_id
        assert worker.stage_transliterate.run(meeting_id) == meeting_id
        assert worker.stage_optional_visual_breakdown.run(meeting_id) == meeting_id

    get_provider.assert_not_called()
    assert len(probe_calls) == 1
    assert probe_calls[0][1]["timeout"] <= worker._LOCAL_MEDIA_PROBE_TIMEOUT_SECONDS
    assert not media_path.exists()
    run_visual.assert_called_once_with(meeting_id, optional=True)
    assert all(
        call.args[1] != "transcription_completed"
        for call in analytics_capture.call_args_list
    )
    assert all(
        call.args[0] != "transcription_completed"
        for call in notify.call_args_list
    )

    with Session(engine) as session:
        updated = session.get(Meeting, meeting_id)
        segments = session.exec(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
        ).all()
        highlights = session.exec(
            select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
        ).all()
        visual_segments = session.exec(
            select(VisualSegment).where(VisualSegment.meeting_id == meeting_id)
        ).all()

    assert updated.status == "processing"
    assert updated.sub_status == "transcription_skipped_no_audio"
    assert updated.title == "Silent video"
    assert updated.description == "Keep this description"
    assert updated.owner_id == 1
    assert updated.requested_language == "en"
    assert updated.file_path == "users/1/meetings/silent/video.mp4"
    assert updated.transcript_text is None
    assert updated.transliterated_text is None
    assert updated.summary_text is None
    assert updated.original_summary_text is None
    assert updated.summary_edited is False
    assert updated.action_items_text is None
    assert updated.structured_output is None
    assert updated.structured_output_status == "pending"
    assert updated.duration_seconds == 42
    assert segments == []
    assert highlights == []
    assert len(visual_segments) == 1
    assert visual_segments[0].caption == "current visual context"


@pytest.mark.parametrize(
    ("probe_side_effect", "probe_result"),
    [
        (TimeoutExpired(["ffprobe"], 10), None),
        (OSError("ffprobe not found"), None),
        (None, CompletedProcess(["ffprobe"], 0, stdout="{malformed", stderr="")),
    ],
)
def test_probe_failures_are_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    probe_side_effect,
    probe_result,
):
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        MagicMock(side_effect=probe_side_effect, return_value=probe_result),
    )

    assert worker._probe_local_media("video.mp4") is None


def test_video_with_audio_keeps_existing_provider_path(local_video_meeting):
    meeting_id, media_path, _ = local_video_meeting
    provider = MagicMock()
    provider.provider_name = "openai-file"
    provider.__enter__.return_value = provider
    provider.__exit__.return_value = False
    provider.transcribe.return_value = _transcription_result()

    with (
        patch.object(worker, "_configured_transcription_provider", return_value="openai-file"),
        patch.object(worker.subprocess, "run", return_value=_probe_result("video", "audio")),
        patch.object(worker, "get_provider", return_value=provider) as get_provider,
        patch.object(worker, "_resolve_meeting_language_for_transcription", return_value=(None, set())),
        patch.object(worker, "analytics"),
        patch.object(worker, "notify"),
        patch.object(worker.meeting_service, "_publish_sub_status"),
    ):
        assert worker.stage_transcribe.run(meeting_id) == meeting_id

    get_provider.assert_called_once()
    provider.transcribe.assert_called_once()
    request = provider.transcribe.call_args.args[0]
    assert request.source.local_path is not None
    assert str(request.source.local_path) == str(media_path)


def test_runpod_without_local_file_keeps_storage_provider_path(local_video_meeting):
    meeting_id, media_path, _ = local_video_meeting
    media_path.unlink()
    provider = MagicMock()
    provider.provider_name = "runpod"
    provider.__enter__.return_value = provider
    provider.__exit__.return_value = False
    provider.transcribe.return_value = _transcription_result()

    with (
        patch.object(worker, "_configured_transcription_provider", return_value="runpod"),
        patch.object(worker.subprocess, "run") as probe,
        patch.object(worker, "get_provider", return_value=provider),
        patch.object(worker, "_resolve_meeting_language_for_transcription", return_value=(None, set())),
        patch.object(worker, "analytics"),
        patch.object(worker, "notify"),
        patch.object(worker.meeting_service, "_publish_sub_status"),
    ):
        assert worker.stage_transcribe.run(meeting_id) == meeting_id

    probe.assert_not_called()
    request = provider.transcribe.call_args.args[0]
    assert request.source.storage_key == "users/1/meetings/silent/video.mp4"
