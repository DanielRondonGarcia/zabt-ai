from types import SimpleNamespace

import pytest
from app.services import meeting_intelligence


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
