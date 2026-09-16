# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Credential-gated conformance checks for external OpenAI audio fixtures."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from app.services.transcription.contracts import AudioSource, BatchTranscriptionRequest, TimestampMode
from app.services.transcription.openai_provider import OpenAIFileProvider


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def test_openai_candidates_conform_on_external_wav_and_mp3_fixtures():
    if os.getenv("TRANSCRIPTION_CONFORMANCE", "").casefold() not in {"1", "true", "yes"}:
        pytest.skip("set TRANSCRIPTION_CONFORMANCE=1 to enable live conformance")

    missing = [
        name for name in (
            "TRANSCRIPTION_API_KEY",
            "TRANSCRIPTION_CONFORMANCE_WAV",
            "TRANSCRIPTION_CONFORMANCE_MP3",
            "TRANSCRIPTION_CONFORMANCE_ORACLE_TEXT",
        ) if not os.getenv(name)
    ]
    if missing:
        pytest.skip(f"conformance prerequisites unavailable: {', '.join(missing)}")

    fixtures = {
        "wav": Path(os.environ["TRANSCRIPTION_CONFORMANCE_WAV"]),
        "mp3": Path(os.environ["TRANSCRIPTION_CONFORMANCE_MP3"]),
    }
    absent = [kind for kind, path in fixtures.items() if not path.is_file()]
    if absent:
        pytest.skip(f"external audio oracle unavailable for: {', '.join(absent)}")

    models = (
        "gpt-transcribe",
        os.getenv("TRANSCRIPTION_CONFORMANCE_COMPARISON_MODEL", "gpt-4o-mini-transcribe"),
    )
    records = []
    for model in models:
        for audio_format, path in fixtures.items():
            provider = OpenAIFileProvider(
                type("Config", (), {
                    "TRANSCRIPTION_API_KEY": os.environ["TRANSCRIPTION_API_KEY"],
                    "TRANSCRIPTION_MODEL": model,
                })()
            )
            with provider:
                result = provider.transcribe(
                    BatchTranscriptionRequest(
                        source=AudioSource.from_local_path(path),
                        language=os.getenv("TRANSCRIPTION_CONFORMANCE_LANGUAGE", "en"),
                        timestamp_mode=TimestampMode.SEGMENT,
                        response_format="verbose_json",
                    )
                )
            records.append({
                "model": model,
                "audio_format": audio_format,
                "response_format": "verbose_json",
                "text": result.text,
                "language": result.language,
                "timing": [{"start": s.start, "end": s.end} for s in result.segments],
                "speaker_gaps": list(result.capability_gaps),
                "duration_seconds": result.audio_duration_seconds,
                "metadata": dict(result.metadata),
                "usage_original_units": asdict(result.usage) if result.usage else None,
            })
            assert _normalized(result.text) == _normalized(os.environ["TRANSCRIPTION_CONFORMANCE_ORACLE_TEXT"])

    print("TRANSCRIPTION_CONFORMANCE_RECORD=" + json.dumps(records, sort_keys=True))
