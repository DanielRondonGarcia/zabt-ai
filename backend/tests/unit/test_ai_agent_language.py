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
