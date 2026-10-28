# SPDX-License-Identifier: AGPL-3.0-only
"""Timed transcript-segment evidence for highlight extraction.

Coverage: real times and speakers reach the provider through the same bounded
windows, oversized segments fragment losslessly with repeated original
metadata, malformed source evidence fails before provider I/O, and null
provider responses still fail on the timed path instead of returning an
invented empty result. The legacy one-positional transcript-only contract is
preserved exactly.
"""

from math import inf, nan
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services import meeting_intelligence

TRANSCRIPT = "spoken words"

OVERSIZED_HEADER = "[0.5-1.0] María: "


def _segment(start, end, text, speaker=None, identifier=None):
    return SimpleNamespace(
        start_time=start, end_time=end, text=text, speaker=speaker, id=identifier
    )


def _response(parsed):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
    )


def _sdk_parse(monkeypatch, requests, validate_or_fail):
    """Fake SDK ``parse`` that validates ``response_format`` like beta.parse."""

    def parse(**kwargs):
        requests.append(kwargs)
        parsed = validate_or_fail(kwargs)
        return _response(parsed)

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )


def _validated(payload):
    def validate(kwargs):
        parsed = kwargs["response_format"].model_validate(payload)
        return parsed

    return validate


def _validated_dump(payload):
    return meeting_intelligence.HighlightsExtraction.model_validate(payload).model_dump()


TIMED_PAYLOAD = {
    "chapters": [
        {"title": "Open", "timestamp_start": 0.149, "timestamp_end": 10.0, "tags": ["intro"]}
    ],
    "action_items": [{"content": "Send notes", "timestamp": 20.5}],
    "key_questions": [],
}


def test_timed_evidence_reaches_provider_with_real_offsets_and_speakers(monkeypatch):
    segments = [
        _segment(5.0, 10.0, "closing thought", "Alice", identifier=3),
        _segment(0.149, 1.5, "buen trabajo", "Bob", identifier=1),
        _segment(2.0, 30.75, "gracias", None, identifier=2),
    ]
    requests = []
    _sdk_parse(monkeypatch, requests, _validated(TIMED_PAYLOAD))

    result = meeting_intelligence.intelligence_service.extract_highlights(
        "[0.149] flat text", segments
    )

    (request,) = requests
    assert request["messages"][1]["content"] == (
        "[0.149-1.5] Bob: buen trabajo\n"
        "[2.0-30.75] gracias\n"
        "[5.0-10.0] Alice: closing thought"
    )
    assert request["messages"][0]["content"].startswith(
        meeting_intelligence.HIGHLIGHT_EXTRACTION_PROMPT
    )
    assert "seconds" in request["messages"][0]["content"]
    # Single timed window keeps the base prompt plus real-offset guidance only;
    # the multiwindow scope instruction is not yet attached.
    assert "timed transcript segment rendered as" in request["messages"][0]["content"]
    assert "Analyze only this transcript window" not in request["messages"][0]["content"]
    assert request["response_format"] is meeting_intelligence.HighlightsExtraction
    assert request["temperature"] == 0.2
    # A validated numeric payload passes through unchanged; nothing was
    # coerced or invented.
    assert result == _validated_dump(TIMED_PAYLOAD)


def test_timed_payload_schema_rejects_null_timestamp(monkeypatch):
    """The observed production failure shape must not validate or be coerced."""
    requests = []

    def reject_null(kwargs):
        kwargs["response_format"].model_validate(
            {
                "chapters": [
                    {
                        "title": "Open",
                        "description": "",
                        "timestamp_start": None,
                        "timestamp_end": None,
                        "tags": [],
                    }
                ],
                "action_items": [],
                "key_questions": [],
            }
        )
        pytest.fail("null output timestamps must not validate")

    _sdk_parse(monkeypatch, requests, reject_null)

    with pytest.raises(ValidationError):
        meeting_intelligence.intelligence_service.extract_highlights(
            TRANSCRIPT, [_segment(0.0, 1.0, "hello", "Alice")]
        )
    assert len(requests) == 1


def test_null_parsed_response_fails_on_timed_path_even_for_one_window(monkeypatch):
    requests = []

    def parse(**kwargs):
        requests.append(kwargs)
        kwargs["response_format"].model_validate(TIMED_PAYLOAD)
        return _response(None)

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )

    with pytest.raises(ValueError, match="highlight extraction returned no parsed window"):
        meeting_intelligence.intelligence_service.extract_highlights(
            TRANSCRIPT, [_segment(0.0, 1.0, "hello", "Alice")]
        )
    assert len(requests) == 1


def test_oversized_segment_fragments_losslessly_repeating_original_metadata(monkeypatch):
    requests = []
    _sdk_parse(monkeypatch, requests, _validated(TIMED_PAYLOAD))
    # Larger than one window budget and mixed with multibyte characters.
    oversized = "á" * 3000 + "z" * 3001
    segments = [
        _segment(0.5, 1.0, oversized, "María", identifier=1),
        _segment(2.0, 3.0, "after the gap", "Luis", identifier=2),
    ]

    result = meeting_intelligence.intelligence_service.extract_highlights("ignored", segments)

    lines = [
        line
        for request in requests
        for line in request["messages"][1]["content"].split("\n")
    ]
    assert len(requests) > 1
    assert all(
        len(request["messages"][1]["content"].encode("utf-8")) <= 4096
        for request in requests
    )
    gap_lines = [line for line in lines if "after the gap" in line]
    assert gap_lines == ["[2.0-3.0] Luis: after the gap"]
    oversized_lines = [line for line in lines if line not in gap_lines]
    assert oversized_lines
    assert len(oversized_lines) > 1
    # Each fragment repeats the ORIGINAL source time and speaker.
    assert all(line.startswith(OVERSIZED_HEADER) for line in oversized_lines)
    reconstructed = "".join(line[len(OVERSIZED_HEADER):] for line in oversized_lines)
    assert reconstructed == oversized
    # Per-window results combine in transcript order, as in the legacy path.
    dump = _validated_dump(TIMED_PAYLOAD)
    assert result == {key: value * len(requests) if isinstance(value, list) else value for key, value in dump.items()}


def test_zero_length_segment_boundary_is_valid_data(monkeypatch):
    """Original zero-length segment boundaries are real data, not defaults."""
    requests = []
    _sdk_parse(monkeypatch, requests, _validated(TIMED_PAYLOAD))

    result = meeting_intelligence.intelligence_service.extract_highlights(
        TRANSCRIPT, [_segment(4.5, 4.5, "boundary", None)]
    )

    (request,) = requests
    assert request["messages"][1]["content"] == "[4.5-4.5] boundary"
    assert result == _validated_dump(TIMED_PAYLOAD)


def test_empty_record_is_skipped_and_documented(monkeypatch):
    """Only explicitly empty segment records are dropped (no characters lost)."""
    requests = []
    _sdk_parse(monkeypatch, requests, _validated(TIMED_PAYLOAD))

    meeting_intelligence.intelligence_service.extract_highlights(
        TRANSCRIPT, [_segment(0.0, 1.0, "", "Alice"), _segment(2.0, 3.0, "kept", "Bob")]
    )

    (request,) = requests
    assert request["messages"][1]["content"] == "[2.0-3.0] Bob: kept"


def test_timed_multiwindow_prompt_keeps_window_scope_with_real_offsets(monkeypatch):
    """Timed multiwindow requests must keep the existing only-this-window
    instruction together with the real-offset timed guidance; the combined
    base prompt must not claim whole-meeting no-gap coverage per window."""
    requests = []
    _sdk_parse(monkeypatch, requests, _validated(TIMED_PAYLOAD))
    segments = [
        _segment(
            index * 10.0,
            index * 10.0 + 1.0,
            "á" * 1600 + "z" * 1600,
            "S",
            identifier=index,
        )
        for index in range(4)
    ]

    meeting_intelligence.intelligence_service.extract_highlights("ignored", segments)

    assert len(requests) > 1
    for request in requests:
        system_prompt = request["messages"][0]["content"]
        assert "timed transcript segment rendered as" in system_prompt
        assert "real seconds" in system_prompt
        assert "Analyze only this transcript window" in system_prompt
        assert "not invent content in other windows" in system_prompt


def test_timed_evidence_without_any_serializable_record_fails_without_provider(monkeypatch):
    """Dropping every record on the timed path must stay a safe failure."""
    def parse(**kwargs):
        pytest.fail("no provider request must result from wholly empty segments")

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )

    with pytest.raises(ValueError, match="timed segment evidence produced no extraction windows"):
        meeting_intelligence.intelligence_service.extract_highlights(
            TRANSCRIPT, [_segment(0.0, 1.0, "")]
        )


@pytest.mark.parametrize(
    "segment",
    [
        _segment(None, 1.0, "text"),  # missing start
        _segment(nan, 1.0, "text"),  # NaN start
        _segment(0.0, inf, "text"),  # infinite end
        _segment(-1.0, 0.0, "text"),  # negative start
        _segment(2.0, 1.0, "text"),  # end before start
        _segment(0.0, 1.0, None),  # missing text
        _segment(True, 2.0, "text"),  # boolean never becomes 1.0
        _segment(0.0, 1.0, "text", speaker=3),  # non-string speaker
    ],
)
def test_malformed_source_evidence_fails_before_provider_io(monkeypatch, segment):
    def parse(**kwargs):
        pytest.fail("malformed source evidence must fail before provider I/O")

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )

    with pytest.raises(ValueError):
        meeting_intelligence.intelligence_service.extract_highlights(TRANSCRIPT, [segment])


def test_legacy_callers_are_byte_identical_with_and_without_segment_list(monkeypatch):
    """None and [] both keep the existing plain-transcript contract exactly."""
    single_response = SimpleNamespace(
        model_dump=lambda: {"chapters": [], "action_items": [], "key_questions": []}
    )
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return _response(single_response)

    def client():
        return SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        )

    legacy_result = {"chapters": [], "action_items": [], "key_questions": []}
    expected_call = (
        meeting_intelligence.HIGHLIGHT_EXTRACTION_PROMPT,
        TRANSCRIPT,
        meeting_intelligence.HighlightsExtraction,
        0.2,
    )

    # One-positional plain-transcript caller.
    monkeypatch.setattr(meeting_intelligence, "_client", client())
    assert (
        meeting_intelligence.intelligence_service.extract_highlights(TRANSCRIPT)
        == legacy_result
    )

    # Empty eager-loaded segment list: identical request shape.
    assert (
        meeting_intelligence.intelligence_service.extract_highlights(TRANSCRIPT, [])
        == legacy_result
    )

    assert [
        (
            call["messages"][0]["content"],
            call["messages"][1]["content"],
            call["response_format"],
            call["temperature"],
        )
        for call in calls
    ] == [expected_call, expected_call]


def test_short_legacy_null_response_still_falls_back_to_empty(monkeypatch):
    """Existing short legacy fallback is not silently changed for old callers."""
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return _response(None)

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )

    assert meeting_intelligence.intelligence_service.extract_highlights(TRANSCRIPT) == {
        "chapters": [], "action_items": [], "key_questions": [],
    }
    assert len(calls) == 1


def test_long_legacy_transcript_still_fails_without_partials(monkeypatch):
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            return _response(None)
        kwargs["response_format"].model_validate(
            {"chapters": [], "action_items": [], "key_questions": []}
        )
        return _response(
            SimpleNamespace(
                model_dump=lambda: {"chapters": [], "action_items": [], "key_questions": []}
            )
        )

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        ),
    )

    with pytest.raises(ValueError, match="highlight extraction returned no parsed window"):
        meeting_intelligence.intelligence_service.extract_highlights(
            "[0.0] Speaker: " + "palabra " * 900
        )
    assert len(calls) == 2
