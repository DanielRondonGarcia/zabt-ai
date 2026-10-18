"""Isolated tests for video-only transcription handling."""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

# Worker import builds configured clients; these placeholders satisfy local test
# settings only. No network requests are made by this unit test.
os.environ.setdefault("EMBEDDING_API_KEY", "test-only-not-a-credential")
os.environ.setdefault("OPENAI_API_KEY", "test-only-not-a-credential")

import app.worker as worker  # noqa: E402


def _ffprobe_result(streams: list[dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"streams": streams, "format": {"duration": "12.5"}}),
        stderr="",
    )


def test_probe_marks_video_with_digital_silence_as_silent(monkeypatch) -> None:
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        Mock(
            side_effect=[
                _ffprobe_result(
                    [
                        {"codec_type": "video"},
                        {"codec_type": "audio"},
                    ]
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr="[Parsed_volumedetect_0] mean_volume: -inf dB\n"
                    "[Parsed_volumedetect_0] max_volume: -inf dB\n",
                ),
            ]
        ),
    )

    probe = worker._probe_local_media("silent-screen-recording.mp4")

    assert probe is not None
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.audio_is_silent is True


def test_probe_keeps_video_with_audible_audio_on_transcription_path(monkeypatch) -> None:
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        Mock(
            side_effect=[
                _ffprobe_result(
                    [
                        {"codec_type": "video"},
                        {"codec_type": "audio"},
                        {"codec_type": "audio"},
                    ]
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr="[Parsed_volumedetect_0] mean_volume: -inf dB\n"
                    "[Parsed_volumedetect_0] max_volume: -inf dB\n"
                    "[Parsed_volumedetect_1] mean_volume: -30.0 dB\n"
                    "[Parsed_volumedetect_1] max_volume: -8.0 dB\n",
                ),
            ]
        ),
    )

    probe = worker._probe_local_media("meeting-with-speech.mp4")

    assert probe is not None
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.audio_is_silent is False


def test_probe_failure_does_not_claim_audio_is_silent(monkeypatch) -> None:
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        Mock(
            side_effect=[
                _ffprobe_result(
                    [
                        {"codec_type": "video"},
                        {"codec_type": "audio"},
                    ]
                ),
                FileNotFoundError("ffmpeg unavailable"),
            ]
        ),
    )

    probe = worker._probe_local_media("unprobed-video.mp4")

    assert probe is not None
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.audio_is_silent is None


class _FakeSession:
    def __init__(self, meeting: SimpleNamespace, user: SimpleNamespace) -> None:
        self.meeting = meeting
        self.user = user
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, model, object_id):
        if model is worker.Meeting and object_id == self.meeting.id:
            return self.meeting
        if model is worker.User and object_id == self.meeting.owner_id:
            return self.user
        return None

    def add(self, _value) -> None:
        return None

    def commit(self) -> None:
        self.commit_count += 1

    def exec(self, _statement):
        raise AssertionError("video-only handling must preserve existing transcript rows")


@pytest.mark.parametrize(
    ("has_audio", "audio_is_silent"),
    [(False, None), (True, True)],
    ids=["no-audio-stream", "silent-audio-stream"],
)
def test_transcribe_skips_video_without_audio_and_preserves_prior_outputs(
    monkeypatch, tmp_path, has_audio, audio_is_silent
) -> None:
    meeting_id = 41
    run_id = 17
    attempt_id = "attempt-silent-video"
    media_path = tmp_path / "silent-video.mp4"
    media_path.write_bytes(b"test media")
    meeting = SimpleNamespace(
        id=meeting_id,
        owner_id=5,
        file_path="users/5/meetings/silent-video.mp4",
        storage_provider="minio",
        transcription_type=None,
        status="processing",
        sub_status="transcribing",
        processing_heartbeat_at=None,
        duration_seconds=77,
        transcript_text="previous transcript",
        transliterated_text="previous transliteration",
        summary_text="previous summary",
        original_summary_text="previous original summary",
        summary_edited=True,
        action_items_text="previous actions",
        structured_output={"kept": True},
        structured_output_status="completed",
    )
    user = SimpleNamespace(tier="free")
    session = _FakeSession(meeting, user)
    provider_factory = Mock()
    publish_status = Mock()

    monkeypatch.setattr(worker, "Session", lambda _engine: session)
    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "_locked_current_run",
        lambda *_args, **_kwargs: (meeting, SimpleNamespace(id=run_id)),
    )
    monkeypatch.setattr(
        worker, "_required_processing_identity", lambda _task: (run_id, attempt_id)
    )
    monkeypatch.setattr(worker, "_configured_transcription_provider", lambda: "openai")
    monkeypatch.setattr(
        worker, "_temp_path_for", lambda *_args, **_kwargs: str(media_path)
    )
    monkeypatch.setattr(
        worker,
        "_probe_local_media",
        lambda _path: worker._LocalMediaProbe(
            has_video=True,
            has_audio=has_audio,
            duration_seconds=12.5,
            audio_is_silent=audio_is_silent,
        ),
    )
    monkeypatch.setattr(worker.meeting_service, "_publish_sub_status", publish_status)
    monkeypatch.setattr(worker, "get_provider", provider_factory)

    assert worker.stage_transcribe.run(meeting_id) == meeting_id

    provider_factory.assert_not_called()
    publish_status.assert_has_calls(
        [
            call(meeting_id, "transcribing"),
            call(meeting_id, "transcription_skipped_no_audio"),
        ]
    )
    assert publish_status.call_count == 2
    assert meeting.sub_status == "transcription_skipped_no_audio"
    assert meeting.duration_seconds == 12
    assert meeting.transcript_text == "previous transcript"
    assert meeting.transliterated_text == "previous transliteration"
    assert meeting.summary_text == "previous summary"
    assert meeting.original_summary_text == "previous original summary"
    assert meeting.summary_edited is True
    assert meeting.action_items_text == "previous actions"
    assert meeting.structured_output == {"kept": True}
    assert meeting.structured_output_status == "completed"
    assert session.commit_count == 2
    assert not media_path.exists()


def test_pipeline_keeps_visual_breakdown_after_transcription(monkeypatch) -> None:
    run = SimpleNamespace(id=23, dispatch_attempt_id="attempt-visual")
    captured_stages: list[object] = []
    root_task = SimpleNamespace(id="root-task")

    monkeypatch.setattr(
        worker.meeting_processing_audit,
        "get_or_create_pending_run",
        lambda *_args, **_kwargs: run,
    )

    def capture_signatures(*args):
        captured_stages.extend(args[1])
        return args[1]

    class _FakeChain:
        def apply_async(self):
            return root_task

    monkeypatch.setattr(worker, "_build_pipeline_signatures", capture_signatures)
    monkeypatch.setattr(worker, "chain", lambda *_tasks: _FakeChain())
    set_root_task = Mock()
    monkeypatch.setattr(worker.meeting_processing_audit, "set_root_task", set_root_task)

    worker.dispatch_pipeline(41)

    assert captured_stages.index(worker.stage_transcribe) < captured_stages.index(
        worker.stage_optional_visual_breakdown
    )
    set_root_task.assert_called_once_with(23, "root-task", attempt_id="attempt-visual")
