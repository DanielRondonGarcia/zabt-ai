# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
def test_validate_and_force_keeps_detection_when_in_set():
    from src.pipeline import _resolve_language_after_detect

    final = _resolve_language_after_detect(
        detected="ur", forced=None, allowed={"ur", "en"},
    )
    assert final.code == "ur"
    assert final.was_forced is False


def test_validate_and_force_forces_when_outside_set():
    from src.pipeline import _resolve_language_after_detect

    final = _resolve_language_after_detect(
        detected="hi", forced="ur", allowed={"ur", "en"},
    )
    assert final.code == "ur"
    assert final.was_forced is True


def test_validate_and_force_keeps_when_no_allowed_set():
    from src.pipeline import _resolve_language_after_detect

    final = _resolve_language_after_detect(
        detected="hi", forced=None, allowed=None,
    )
    assert final.code == "hi"
    assert final.was_forced is False


def test_validate_and_force_uses_explicit_force_over_detection():
    from src.pipeline import _resolve_language_after_detect

    final = _resolve_language_after_detect(
        detected="en", forced="ur", allowed=None,
    )
    assert final.code == "ur"
    assert final.was_forced is True


def test_medical_jobs_keep_the_medasr_pipeline_route(monkeypatch):
    from src.pipeline import PipelineConfig, run_pipeline

    called = {}

    def fake_medasr(audio_path, config, model, callback):
        called["config"] = config
        return {"segments": []}, "en"

    def fail_whisper(*args):
        raise AssertionError("medical jobs must not enter WhisperX")

    monkeypatch.setattr("src.pipeline._transcribe_medasr", fake_medasr)
    monkeypatch.setattr("src.pipeline._transcribe_whisperx", fail_whisper)
    result = run_pipeline(
        "fixture.wav",
        PipelineConfig(
            device="cpu",
            compute_type="int8",
            whisper_model_name="base",
            diarization_model_name="unused",
            hf_token="",
            transcription_type="medical",
        ),
    )

    assert result["language"] == "en"
    assert called["config"].transcription_type == "medical"
