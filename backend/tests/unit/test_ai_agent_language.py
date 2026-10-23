# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import ai_agent
from app.services.multimodal_context import ContextBuildResult, ContextChunk, ContextItem


def _mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Generated notes"))]
    )
    monkeypatch.setattr(ai_agent, "_client", client)
    return client


def _context(*, chunk_count: int = 1) -> ContextBuildResult:
    chunks = tuple(
        ContextChunk(
            index=index,
            start=float(index * 60),
            end=float((index + 1) * 60),
            items=(
                ContextItem(
                    source="spoken",
                    source_id=index + 1,
                    start=float(index * 60),
                    end=float((index + 1) * 60),
                    content="The team agreed to review the proposal.",
                    relevance="high",
                    provenance="observed",
                    evidence_ref=f"spoken:{index + 1}",
                ),
            ),
            estimated_input_tokens=10,
            complete=True,
        )
        for index in range(chunk_count)
    )
    return ContextBuildResult(
        chunks=chunks,
        source_item_count=chunk_count,
        assigned_item_count=chunk_count,
        unassigned_items=(),
        completeness="complete",
        warning_codes=(),
    )


def _system_prompts(client: MagicMock) -> list[str]:
    return [
        call.kwargs["messages"][0]["content"]
        for call in client.chat.completions.create.call_args_list
    ]


_ALLOWED_PROGRESS_PHASES = {
    "single_chunk_summary",
    "partial_chunk_summary",
    "summary_synthesis",
    "transcript_summary",
}
_ALLOWED_PROGRESS_OUTCOMES = {"started", "completed", "failed"}
_ALLOWED_PROGRESS_KEYS = {
    "phase",
    "block_index",
    "block_total",
    "outcome",
    "duration_ms",
    "error_class",
}


def _progress_recorder() -> tuple[object, list[dict]]:
    events: list[dict] = []

    def record(**kwargs) -> None:
        events.append(kwargs)

    return record, events


def _client_with_failing_call(index: int, error_message: str = "boom"):
    """Build a mock client whose *index*-th completion call raises AIProviderError."""
    client = MagicMock()
    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == index:
            raise ai_agent.AIProviderError(error_message)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    client.chat.completions.create.side_effect = _side_effect
    return client


def test_spanish_instruction_is_added_to_context_and_transcript_prompts(
    monkeypatch: pytest.MonkeyPatch,
):
    context_client = _mock_client(monkeypatch)
    ai_agent.summarize_transcript("Transcript", context=_context(), output_language="es")
    context_prompt = _system_prompts(context_client)[0]

    transcript_client = _mock_client(monkeypatch)
    ai_agent.summarize_transcript("Transcript", output_language="es")
    transcript_prompt = _system_prompts(transcript_client)[0]

    for prompt in (context_prompt, transcript_prompt):
        assert "MANDATORY OUTPUT LANGUAGE" in prompt
        assert "Spanish" in prompt
        assert "selected language code: es" in prompt
        assert "Do not answer in another language" in prompt


def test_hierarchical_calls_keep_the_language_instruction_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _mock_client(monkeypatch)

    ai_agent.summarize_transcript(
        "Transcript",
        context=_context(chunk_count=2),
        output_language="es",
    )

    prompts = _system_prompts(client)
    assert len(prompts) == 3
    assert all("MANDATORY OUTPUT LANGUAGE" in prompt for prompt in prompts)
    assert all("Spanish" in prompt for prompt in prompts)


def test_unknown_language_code_is_explicit_instead_of_falling_back_to_english(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _mock_client(monkeypatch)

    ai_agent.summarize_transcript("Transcript", output_language="xx-YY")

    prompt = _system_prompts(client)[0]
    assert "language identified by code 'xx-YY'" in prompt
    assert "selected language code: xx-YY" in prompt
    assert "English" not in prompt


def test_inferred_title_uses_the_selected_language(monkeypatch: pytest.MonkeyPatch):
    client = _mock_client(monkeypatch)

    ai_agent.infer_title("Meeting summary", output_language="es")

    prompt = _system_prompts(client)[0]
    assert "MANDATORY OUTPUT LANGUAGE" in prompt
    assert "Spanish" in prompt
    assert "selected language code: es" in prompt


def test_no_language_keeps_prompts_free_of_a_language_directive(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _mock_client(monkeypatch)

    ai_agent.summarize_transcript("Transcript")
    ai_agent.infer_title("Meeting summary")

    prompts = _system_prompts(client)
    assert len(prompts) == 2
    assert all("MANDATORY OUTPUT LANGUAGE" not in prompt for prompt in prompts)
    assert all("selected language code" not in prompt for prompt in prompts)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("es", "Spanish"), ("es-ES", "Spanish (es-ES)"), ("en", "English")],
)
def test_language_label_supports_catalog_and_regional_codes(code: str, expected: str):
    assert ai_agent._language_label(code) == expected


def _phase_index_total(event: dict) -> tuple[str, int, int]:
    return (event["phase"], event["block_index"], event["block_total"])


def _sequence(events: list[dict]) -> list[tuple[str, int, int, str]]:
    return [(*_phase_index_total(event), event["outcome"]) for event in events]


def test_single_block_context_reports_started_then_completed(monkeypatch):
    _mock_client(monkeypatch)
    record, events = _progress_recorder()

    ai_agent.summarize_context(_context(), progress_callback=record)

    assert _sequence(events) == [
        ("single_chunk_summary", 1, 1, "started"),
        ("single_chunk_summary", 1, 1, "completed"),
    ]
    assert events[0]["duration_ms"] is None
    assert events[1]["duration_ms"] >= 0


def test_each_window_and_synthesis_report_started_then_terminal(monkeypatch):
    # Two successes, then window 2 fails, then the synthesis succeeds:
    # exactly the omit-one-window flow.
    client = _client_with_failing_call(2)
    monkeypatch.setattr(ai_agent, "_client", client)
    record, events = _progress_recorder()

    summary = ai_agent.summarize_context(_context(chunk_count=3), progress_callback=record)

    assert summary
    assert _sequence(events) == [
        ("partial_chunk_summary", 1, 3, "started"),
        ("partial_chunk_summary", 1, 3, "completed"),
        ("partial_chunk_summary", 2, 3, "started"),
        ("partial_chunk_summary", 2, 3, "failed"),
        ("partial_chunk_summary", 3, 3, "started"),
        ("partial_chunk_summary", 3, 3, "completed"),
        ("summary_synthesis", 4, 4, "started"),
        ("summary_synthesis", 4, 4, "completed"),
    ]
    failed = events[3]
    assert failed["error_class"] == "AIProviderError"
    assert failed["duration_ms"] >= 0
    # No synthesis completed duration without synthesis completing.
    assert events[6]["duration_ms"] is None


def test_failed_partial_window_keeps_later_windows_and_synthesis_running(monkeypatch):
    # Window 2 fails but the summary still completes: exactly one failed window.
    client = _client_with_failing_call(2)
    monkeypatch.setattr(ai_agent, "_client", client)
    record, events = _progress_recorder()

    ai_agent.summarize_context(_context(chunk_count=3), progress_callback=record)

    failed_outcomes = [event["outcome"] for event in events if event["outcome"] == "failed"]
    assert len(failed_outcomes) == 1
    assert events[-1]["outcome"] == "completed"


def test_all_windows_failing_raises_without_synthesis_progress(monkeypatch):
    # Every window fails: the summary fails and no synthesis event is reported.
    client = MagicMock()

    def _always_fail(*args, **kwargs):
        raise ai_agent.AIProviderError("boom")

    client.chat.completions.create.side_effect = _always_fail
    monkeypatch.setattr(ai_agent, "_client", client)
    record, events = _progress_recorder()

    with pytest.raises(ai_agent.AIProviderError):
        ai_agent.summarize_context(_context(chunk_count=2), progress_callback=record)

    assert _sequence(events) == [
        ("partial_chunk_summary", 1, 2, "started"),
        ("partial_chunk_summary", 1, 2, "failed"),
        ("partial_chunk_summary", 2, 2, "started"),
        ("partial_chunk_summary", 2, 2, "failed"),
    ]


def test_failed_synthesis_propagates_and_reports_the_failure(monkeypatch):
    # Both windows succeed, the synthesis call fails: the failure must surface.
    client = _client_with_failing_call(3)
    monkeypatch.setattr(ai_agent, "_client", client)
    record, events = _progress_recorder()

    with pytest.raises(ai_agent.AIProviderError):
        ai_agent.summarize_context(_context(chunk_count=2), progress_callback=record)

    assert events[-1]["phase"] == "summary_synthesis"
    assert events[-1]["outcome"] == "failed"
    assert events[-1]["error_class"] == "AIProviderError"
    synthesis_events = [event for event in events if event["phase"] == "summary_synthesis"]
    assert [event["outcome"] for event in synthesis_events] == ["started", "failed"]


def test_transcript_only_path_reports_a_single_block(monkeypatch):
    _mock_client(monkeypatch)
    record, events = _progress_recorder()

    ai_agent.summarize_transcript("Transcript", progress_callback=record)

    assert _sequence(events) == [
        ("transcript_summary", 1, 1, "started"),
        ("transcript_summary", 1, 1, "completed"),
    ]


def test_context_path_forwards_the_progress_callback(monkeypatch):
    _mock_client(monkeypatch)
    record, events = _progress_recorder()

    ai_agent.summarize_transcript("Transcript", context=_context(), progress_callback=record)

    assert [event["phase"] for event in events] == ["single_chunk_summary", "single_chunk_summary"]


def test_progress_payload_never_carries_content_urls_or_messages(monkeypatch):
    transcript_content = "The team agreed to review the proposal."
    client = _client_with_failing_call(2)
    monkeypatch.setattr(ai_agent, "_client", client)
    record, events = _progress_recorder()

    ai_agent.summarize_context(_context(chunk_count=3), progress_callback=record)

    assert events
    for event in events:
        assert set(event.keys()) == _ALLOWED_PROGRESS_KEYS
        assert event["phase"] in _ALLOWED_PROGRESS_PHASES
        assert event["outcome"] in _ALLOWED_PROGRESS_OUTCOMES
        assert isinstance(event["block_index"], int) and event["block_index"] >= 1
        assert isinstance(event["block_total"], int) and event["block_total"] >= 1
        assert event["duration_ms"] is None or isinstance(event["duration_ms"], int)
        error_class = event["error_class"]
        if error_class is not None:
            assert isinstance(error_class, str)
            # A sanitized error class is a bare identifier, never a message.
            assert error_class.strip() == error_class
            assert not any(ch.isspace() for ch in error_class)
            assert len(error_class) <= 64
        for key, value in event.items():
            if isinstance(value, str):
                assert transcript_content not in value
                assert "http" not in value
                assert "sk-" not in value
