from types import SimpleNamespace

import httpx
import pytest
from app.services import meeting_intelligence
from openai import APIStatusError


def _fake_highlights_provider(monkeypatch, failing_status, *, persistent=False):
    windows = []
    request = httpx.Request("POST", "https://provider.example/chat/completions")
    error = APIStatusError(
        f"Error code: {failing_status}",
        response=httpx.Response(failing_status, request=request),
        body=None,
    )

    def parse(messages, response_format, **kwargs):
        window = messages[1]["content"]
        windows.append(window)
        if "second" in window and (persistent or windows.count(window) == 1):
            raise error
        parsed = response_format(key_questions=[{"content": window.split()[2]}])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )

    monkeypatch.setattr(
        meeting_intelligence,
        "_client",
        SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
            )
        ),
    )
    return windows, error


def _long_meeting_segments():
    return [
        SimpleNamespace(
            start_time=start, end_time=end, speaker="Speaker", text=label + " x" * 1500
        )
        for label, start, end in [
            ("first", 0, 60), ("second", 3600, 3660), ("third", 7380, 7440)
        ]
    ]


def test_highlights_retries_only_the_failed_window_of_a_long_meeting(monkeypatch):
    windows, _ = _fake_highlights_provider(monkeypatch, 502)

    result = meeting_intelligence.intelligence_service.extract_highlights(
        "Transcript text", _long_meeting_segments()
    )

    assert [window.split()[2] for window in windows] == ["first", "second", "second", "third"]
    assert windows[1] == windows[2]
    assert [question["content"] for question in result["key_questions"]] == [
        "first", "second", "third"
    ]
    assert result["chapters"] == result["action_items"] == []


@pytest.mark.parametrize(("status", "expected_windows"), [
    (502, ["first", "second", "second"]),
    (400, ["first", "second"]),
])
def test_highlights_propagates_provider_errors_when_a_window_cannot_recover(
    monkeypatch, status, expected_windows
):
    windows, error = _fake_highlights_provider(monkeypatch, status, persistent=True)

    with pytest.raises(APIStatusError) as raised:
        meeting_intelligence.intelligence_service.extract_highlights(
            "Transcript text", _long_meeting_segments()
        )

    assert raised.value is error
    assert [window.split()[2] for window in windows] == expected_windows


def test_highlights_falls_back_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    markdown_response = "# Resumen de la Reunión\nNo es JSON."

    class FakeCompletions:
        def parse(self, **kwargs):
            kwargs["response_format"].model_validate_json(markdown_response)

    fake_client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    monkeypatch.setattr(meeting_intelligence, "_client", fake_client)

    result = meeting_intelligence.intelligence_service.extract_highlights("Transcript text")

    assert result == {"chapters": [], "action_items": [], "key_questions": []}
    assert markdown_response not in caplog.text


def test_structured_output_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    markdown_response = "# Resumen de la Reunión\nNo es JSON."

    class FakeCompletions:
        def parse(self, **kwargs):
            kwargs["response_format"].model_validate_json(markdown_response)

    fake_client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    monkeypatch.setattr(meeting_intelligence, "_client", fake_client)

    with pytest.raises(ValueError, match="Invalid JSON"):
        meeting_intelligence.intelligence_service.extract_structured_output(
            "Transcript text", "generic"
        )
    assert markdown_response not in caplog.text


def test_structured_extraction_still_propagates_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletions:
        def parse(self, **kwargs):
            raise RuntimeError("provider unavailable")

    fake_client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    monkeypatch.setattr(meeting_intelligence, "_client", fake_client)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        meeting_intelligence.intelligence_service.extract_structured_output(
            transcript_text="Transcript text",
            meeting_type="generic",
        )


def test_structured_output_prompt_requires_json_without_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_calls = []

    class FakeCompletions:
        def parse(self, **kwargs):
            parse_calls.append(kwargs)
            parsed = meeting_intelligence.GenericOutput(summary="A summary")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
            )

    fake_client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    monkeypatch.setattr(meeting_intelligence, "_client", fake_client)

    result = meeting_intelligence.intelligence_service.extract_structured_output(
        transcript_text="Transcript text",
        meeting_type="generic",
    )

    system_prompt = parse_calls[0]["messages"][0]["content"]
    assert "Return valid JSON only" in system_prompt
    assert "Do not include Markdown" in system_prompt
    assert result["summary"] == "A summary"
