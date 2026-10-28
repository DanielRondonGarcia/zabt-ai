# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Per-window partial prompts stay evidence-only; final formatting belongs to the last call."""

from __future__ import annotations

from app.services.ai_agent import summarize_context, summarize_transcript
from app.services.multimodal_context import ContextBuildResult, ContextChunk, ContextItem

_STYLE_EXAMPLE = "STYLE_EXAMPLE_PREFERRED_LAYOUT"
_TEMPLATE_BODY = "TEMPLATE_BODY_CUSTOM_LAYOUT"
_UPLOAD_DATE = "2026-01-15"


class RecordingCompletionClient:
    """Fake ``CompletionClient`` that records every message payload it receives."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        correlation_id: str | None = None,
    ) -> str:
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "temperature": temperature,
            }
        )
        return f"Recorded result {len(self.requests)}"


def _context(*, chunk_count: int = 2) -> ContextBuildResult:
    """Bounded context with per-window spoken facts and inferred visual evidence."""
    chunks = []
    for index in range(chunk_count):
        spoken = ContextItem(
            source="spoken",
            source_id=index + 1,
            start=float(index * 60),
            end=float(index * 60 + 30),
            content=f"Spoken fact {index + 1}.",
            relevance="high",
            provenance="observed",
            evidence_ref=f"spoken:{index + 1}",
            speaker=f"SPEAKER_0{index}",
        )
        visual = ContextItem(
            source="visual",
            source_id=index + 1,
            start=float(index * 60 + 30),
            end=float((index + 1) * 60),
            content="Chart trending upward.",
            relevance="medium",
            provenance="inferred",
            evidence_ref=f"visual:{index + 1}",
            confidence=0.6,
        )
        chunks.append(
            ContextChunk(
                index=index,
                start=float(index * 60),
                end=float((index + 1) * 60),
                items=(spoken, visual),
                estimated_input_tokens=20,
                complete=True,
            )
        )
    return ContextBuildResult(
        chunks=tuple(chunks),
        source_item_count=chunk_count * 2,
        assigned_item_count=chunk_count * 2,
        unassigned_items=(),
        completeness="complete",
        warning_codes=(),
    )


def _system(request: dict) -> str:
    return request["messages"][0]["content"]


def test_partial_window_prompts_exclude_the_final_report_prompt_and_formatting():
    client = RecordingCompletionClient()

    result = summarize_transcript(
        "Transcript text",
        context=_context(chunk_count=2),
        style_examples=[_STYLE_EXAMPLE],
        template_body=_TEMPLATE_BODY,
        upload_date=_UPLOAD_DATE,
        output_language="es",
        completion_client=client,
    )

    assert result == "Recorded result 3"
    assert len(client.requests) == 3
    for window, request in enumerate(client.requests[:2], start=1):
        system_prompt = _system(request)
        # The full final-report system prompt never amplifies a per-window call.
        assert "expert meeting note-taker" not in system_prompt
        assert "## Output format" not in system_prompt
        assert "-- EXAMPLE 1 ---" not in system_prompt
        assert _STYLE_EXAMPLE not in system_prompt
        # The selected template is only applied at synthesis.
        assert _TEMPLATE_BODY not in system_prompt
        # Evidence rules, source references, spoken-vs-visual labels stay.
        assert "## Evidence rules" in system_prompt
        user_content = request["messages"][1]["content"]
        assert "SPOKEN CONTENT" in user_content
        assert "VISUAL CONTEXT" in user_content
        assert "INFERENCE/UNCERTAINTY" in user_content
        assert "Spoken fact" in user_content
        assert "[source=spoken:" in user_content
        assert "[source=visual:" in user_content


def test_partial_window_prompts_keep_compact_output_and_language_directives():
    client = RecordingCompletionClient()

    summarize_transcript(
        "Transcript text",
        context=_context(chunk_count=3),
        output_language="es",
        completion_client=client,
    )

    for request in client.requests[:3]:
        system_prompt = _system(request)
        # Explicit compact output instruction, not the final report structure.
        assert "concise, source-grounded" in system_prompt
        assert "final report headings" in system_prompt or "report headings" in system_prompt
        assert "Do not repeat" in system_prompt
        # Requested output language is preserved on every window call.
        assert "MANDATORY OUTPUT LANGUAGE" in system_prompt
        assert "Spanish" in system_prompt


def test_partial_window_prompts_keep_the_upload_date():
    client = RecordingCompletionClient()

    summarize_transcript(
        "Transcript text",
        context=_context(chunk_count=2),
        upload_date=_UPLOAD_DATE,
        completion_client=client,
    )

    for request in client.requests[:2]:
        assert _UPLOAD_DATE in _system(request)


def test_final_synthesis_prompt_keeps_styles_template_date_language_and_windows():
    client = RecordingCompletionClient()

    result = summarize_transcript(
        "Transcript text",
        context=_context(chunk_count=2),
        style_examples=[_STYLE_EXAMPLE],
        template_body=_TEMPLATE_BODY,
        upload_date=_UPLOAD_DATE,
        output_language="es",
        completion_client=client,
    )

    synthesis_system = _system(client.requests[-1])
    assert _TEMPLATE_BODY in synthesis_system
    assert _STYLE_EXAMPLE in synthesis_system
    assert _UPLOAD_DATE in synthesis_system
    assert "MANDATORY OUTPUT LANGUAGE" in synthesis_system
    synthesis_user = client.requests[-1]["messages"][1]["content"]
    assert "PARTIAL WINDOW 1" in synthesis_user
    assert "PARTIAL WINDOW 2" in synthesis_user
    assert result == "Recorded result 3"


def test_final_synthesis_only_request_carries_no_selected_template_or_style():
    client = RecordingCompletionClient()

    summarize_transcript(
        "Transcript text",
        context=_context(chunk_count=2),
        completion_client=client,
    )

    synthesis_system = _system(client.requests[-1])
    # Without a selection, no style reference or format instruction is invented.
    assert "Style reference" not in synthesis_system
    assert "FORMAT INSTRUCTION" not in synthesis_system


def test_short_single_context_path_keeps_final_formatting():
    client = RecordingCompletionClient()

    result = summarize_context(
        _context(chunk_count=1),
        style_examples=[_STYLE_EXAMPLE],
        template_body=_TEMPLATE_BODY,
        upload_date=_UPLOAD_DATE,
        output_language="es",
        completion_client=client,
    )

    assert result == "Recorded result 1"
    assert len(client.requests) == 1
    system_prompt = _system(client.requests[0])
    assert "expert meeting note-taker" in system_prompt
    assert _TEMPLATE_BODY in system_prompt
    assert _STYLE_EXAMPLE in system_prompt
    assert _UPLOAD_DATE in system_prompt
    assert "MANDATORY OUTPUT LANGUAGE" in system_prompt
    user_content = client.requests[0]["messages"][1]["content"]
    assert "SPOKEN CONTENT" in user_content
    assert "[source=spoken:" in user_content


def test_transcript_only_path_keeps_final_formatting_and_language():
    client = RecordingCompletionClient()

    result = summarize_transcript(
        "Transcript text",
        template_body=_TEMPLATE_BODY,
        upload_date=_UPLOAD_DATE,
        output_language="es",
        completion_client=client,
    )

    assert result == "Recorded result 1"
    assert len(client.requests) == 1
    system_prompt = _system(client.requests[0])
    assert "expert meeting note-taker" in system_prompt
    assert _TEMPLATE_BODY in system_prompt
    assert _UPLOAD_DATE in system_prompt
    assert "MANDATORY OUTPUT LANGUAGE" in system_prompt


def test_bounded_warnings_stay_on_the_synthesis_not_on_window_prompts():
    client = RecordingCompletionClient()
    context = ContextBuildResult(
        chunks=_context(chunk_count=2).chunks,
        source_item_count=4,
        assigned_item_count=4,
        unassigned_items=(),
        completeness="incomplete",
        warning_codes=("WARNING_EVIDENCE_UNASSIGNED",),
    )

    summarize_transcript(
        "Transcript text",
        context=context,
        completion_client=client,
    )

    for request in client.requests[:2]:
        assert "EXPLICIT BOUNDED WARNINGS" not in request["messages"][1]["content"]
    assert "EXPLICIT BOUNDED WARNINGS" in client.requests[-1]["messages"][1]["content"]