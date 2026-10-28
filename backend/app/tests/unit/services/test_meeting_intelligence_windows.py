# SPDX-License-Identifier: AGPL-3.0-only
"""Public service contract for bounded transcript-only intelligence requests."""

from types import SimpleNamespace

import pytest

from app.services import meeting_intelligence


def _response(data):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: data)))]
    )


def test_long_transcript_highlights_cover_every_character_in_bounded_requests(monkeypatch):
    # A long unbroken UTF-8 token also has to remain intact across requests.
    transcript = "[0.0] Speaker: " + "ñ" * 5100 + "\n[90.0] Speaker: final question?"
    requests = []

    def parse(**kwargs):
        requests.append(kwargs)
        index = len(requests)
        return _response(
            {
                "chapters": [{"title": f"chapter {index}", "timestamp_start": float(index)}],
                "action_items": [{"content": f"action {index}", "timestamp": float(index)}],
                "key_questions": [{"content": f"question {index}", "timestamp": float(index)}],
            }
        )

    monkeypatch.setattr(
        meeting_intelligence, "_client",
        SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
    )

    result = meeting_intelligence.intelligence_service.extract_highlights(transcript)

    assert len(requests) > 1
    assert "".join(request["messages"][1]["content"] for request in requests) == transcript
    assert all(
        len(request["messages"][1]["content"].encode("utf-8")) <= 4096
        for request in requests
    )
    assert [chapter["title"] for chapter in result["chapters"]] == [
        f"chapter {index}" for index in range(1, len(requests) + 1)
    ]
    assert [action["content"] for action in result["action_items"]] == [
        f"action {index}" for index in range(1, len(requests) + 1)
    ]
    assert [question["content"] for question in result["key_questions"]] == [
        f"question {index}" for index in range(1, len(requests) + 1)
    ]


def test_long_transcript_structured_output_covers_every_character_and_aggregates(monkeypatch):
    transcript = "".join(f"[{index}.0] Speaker: sentence {index}\n" for index in range(450))
    requests = []

    def parse(**kwargs):
        requests.append(kwargs)
        index = len(requests)
        return _response(
            {
                "meeting_type": "generic",
                "summary": f"summary {index}",
                "topics": [{"title": f"topic {index}", "summary": "evidence"}],
                "decisions": [f"decision {index}"],
                "follow_ups": [f"follow-up {index}"],
            }
        )

    monkeypatch.setattr(
        meeting_intelligence, "_client",
        SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
    )

    result = meeting_intelligence.intelligence_service.extract_structured_output(transcript, "generic")

    assert len(requests) > 1
    assert "".join(request["messages"][1]["content"] for request in requests) == transcript
    assert all("only this transcript window" in request["messages"][0]["content"] for request in requests)
    assert all(
        len(request["messages"][1]["content"].encode("utf-8")) <= 4096
        for request in requests
    )
    assert [topic["title"] for topic in result["topics"]] == [
        f"topic {index}" for index in range(1, len(requests) + 1)
    ]
    assert result["decisions"] == [f"decision {index}" for index in range(1, len(requests) + 1)]
    assert result["follow_ups"] == [f"follow-up {index}" for index in range(1, len(requests) + 1)]
    assert result["summary"] == "\n\n".join(
        f"summary {index}" for index in range(1, len(requests) + 1)
    )


def test_short_transcript_keeps_existing_single_request_shape_for_both_stages(monkeypatch):
    requests = []
    expected = {"chapters": [], "action_items": [], "key_questions": []}

    def parse(**kwargs):
        requests.append(kwargs)
        return _response(expected)

    monkeypatch.setattr(
        meeting_intelligence, "_client",
        SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
    )

    assert meeting_intelligence.intelligence_service.extract_highlights("[0.0] Speaker: hello") == expected
    assert meeting_intelligence.intelligence_service.extract_structured_output("[0.0] Speaker: hello", "standup") == expected
    assert [request["messages"][1]["content"] for request in requests] == [
        "[0.0] Speaker: hello",
        "[0.0] Speaker: hello",
    ]
    assert [request["response_format"] for request in requests] == [
        meeting_intelligence.HighlightsExtraction,
        meeting_intelligence.StandupOutput,
    ]
    assert all(request["temperature"] == 0.2 for request in requests)


@pytest.mark.parametrize(
    ("meeting_type", "fields"),
    [
        ("grooming", {"sprint_name": "Sprint", "items": ["story"]}),
        ("standup", {"items": ["update"]}),
        ("retro", {"went_well": ["win"], "to_improve": ["gap"], "action_items": ["act"]}),
        (
            "one_on_one",
            {"talking_points": ["point"], "decisions": ["decision"],
             "follow_ups": ["follow-up"], "career_notes": ["note"]},
        ),
    ],
)
def test_long_transcript_preserves_typed_fields_for_existing_meeting_types(
    monkeypatch, meeting_type, fields
):
    transcript = "[0.0] Speaker: " + "text " * 1800
    requests = []

    def parse(**kwargs):
        requests.append(kwargs)
        return _response({"meeting_type": meeting_type, **fields})

    monkeypatch.setattr(
        meeting_intelligence, "_client",
        SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
    )

    output = meeting_intelligence.intelligence_service.extract_structured_output(
        transcript, meeting_type
    )

    assert len(requests) > 1
    assert "".join(request["messages"][1]["content"] for request in requests) == transcript
    assert all(len(request["messages"][1]["content"].encode()) <= 4096 for request in requests)
    for key, value in fields.items():
        assert output[key] == (value * len(requests) if isinstance(value, list) else value)


def test_failed_window_does_not_return_partial_highlights(monkeypatch):
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise TimeoutError("Request timed out.")
        return _response({"chapters": [], "action_items": [], "key_questions": []})

    monkeypatch.setattr(
        meeting_intelligence, "_client",
        SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
    )

    with pytest.raises(TimeoutError, match="Request timed out"):
        meeting_intelligence.intelligence_service.extract_highlights("[0.0] Speaker: " + "word " * 2000)
    assert len(calls) == 2
