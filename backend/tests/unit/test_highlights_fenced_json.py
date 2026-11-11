from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.services import meeting_intelligence as intelligence


def _client(parse):
    return SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(parse=parse)
    )))


def _invalid_json_response(raw):
    def parse(**kwargs):
        return intelligence.HighlightsExtraction.model_validate_json(raw)
    return parse


def test_fenced_json_is_validated_for_timed_highlights(monkeypatch):
    raw = '```json\n{"chapters": [], "action_items": [], "key_questions": []}\n```'
    monkeypatch.setattr(intelligence, "_client", _client(_invalid_json_response(raw)))
    monkeypatch.setattr(intelligence, "_timed_evidence_windows", lambda segments: ["timed"])
    assert intelligence.MeetingIntelligenceService().extract_highlights("", segments=[object()]) == {
        "chapters": [], "action_items": [], "key_questions": []
    }


def test_existing_parsed_response_is_preserved(monkeypatch):
    parsed = intelligence.HighlightsExtraction(chapters=[{"title": "Topic", "timestamp_start": 0}])
    parse = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]))
    monkeypatch.setattr(intelligence, "_client", _client(parse))
    assert intelligence.MeetingIntelligenceService().extract_highlights("transcript") == parsed.model_dump()
    parse.assert_called_once()


def test_invalid_fenced_schema_does_not_return_partial_timed_results(monkeypatch):
    raw = '```json\n{"chapters": [{"title": "Missing timestamp"}]}\n```'
    monkeypatch.setattr(intelligence, "_client", _client(_invalid_json_response(raw)))
    monkeypatch.setattr(intelligence, "_timed_evidence_windows", lambda segments: ["timed"])
    with pytest.raises(ValidationError):
        intelligence.MeetingIntelligenceService().extract_highlights("", segments=[object()])
