# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from enum import Enum
from typing import Optional, List
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from langfuse.openai import OpenAI
from sqlmodel import Session, select, col

from app.core.config import settings
from app.core.logging import get_logger
from app.db.engine import engine
from app.models.meeting_intelligence import MeetingHighlight
from app.services.ai_provider import build_openai_client
from app.services.meeting_processing_audit import (
    meeting_processing_audit,
)
from app.services.meeting_recovery import transcript_fingerprint

logger = get_logger(__name__)

# Keep long transcript-only extraction requests well below the 26 KB input
# that timed out on the configured completion gateway. Short requests retain
# their original single-call behavior.
TRANSCRIPT_ANALYSIS_MAX_BYTES = 4096


def _transcript_windows(transcript_text: str) -> list[str]:
    """Partition all transcript characters into UTF-8-bounded windows.

    Prefer a recent line or word boundary, without discarding timestamps,
    whitespace, or an unusually long word. Windows have no overlap, so their
    structured results can be combined without duplicating window boundaries.
    """
    if len(transcript_text.encode("utf-8")) <= TRANSCRIPT_ANALYSIS_MAX_BYTES:
        return [transcript_text]

    windows: list[str] = []
    start = 0
    while start < len(transcript_text):
        end = start
        byte_count = 0
        last_line = start
        last_space = start
        while end < len(transcript_text):
            char = transcript_text[end]
            char_bytes = len(char.encode("utf-8"))
            if byte_count + char_bytes > TRANSCRIPT_ANALYSIS_MAX_BYTES:
                break
            byte_count += char_bytes
            end += 1
            if char == "\n":
                last_line = end
            elif char.isspace():
                last_space = end

        halfway = start + (end - start) // 2
        cut = (
            last_line if last_line > halfway
            else last_space if last_space > halfway
            else end
        )
        windows.append(transcript_text[start:cut])
        start = cut
    return windows


def _combine_structured_windows(outputs: list[dict]) -> dict:
    """Merge typed per-window fields in transcript order without dropping data."""
    combined = outputs[0].copy()
    for key, value in combined.items():
        if key == "meeting_type":
            continue
        values = [output[key] for output in outputs]
        if isinstance(value, list):
            combined[key] = [item for items in values for item in items]
        elif key == "summary":
            combined[key] = "\n\n".join(text for text in values if text)
        else:
            # Optional scalar fields (for example sprint_name) are deduplicated
            # if repeated, but distinct evidence from later windows is kept.
            combined[key] = " / ".join(dict.fromkeys(text for text in values if text)) or None
    return combined


class HighlightSaveRejected(Enum):
    STALE_OR_CANCELLED = "stale_or_cancelled"


def _build_openai_client():
    return build_openai_client(
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
        client_factory=OpenAI,
    )


_client = _build_openai_client()


# ── Pydantic models for structured output (enforced by OpenAI beta.parse) ────

class HighlightChapter(BaseModel):
    title: str = Field(description="Chapter title (3-8 words)")
    description: str = Field(default="", description="1-2 sentence description")
    timestamp_start: float = Field(description="Start time in seconds")
    timestamp_end: Optional[float] = Field(default=None, description="End time in seconds")
    tags: List[str] = Field(default_factory=list, description="Topic tags")

class HighlightActionItem(BaseModel):
    content: str = Field(description="Clear, actionable task starting with a verb")
    speaker: Optional[str] = Field(default=None, description="Speaker name")
    assignee: Optional[str] = Field(default=None, description="Person responsible")
    timestamp: float = Field(default=0.0, description="Timestamp in seconds")

class HighlightKeyQuestion(BaseModel):
    content: str = Field(description="The question that was asked")
    speaker: Optional[str] = Field(default=None, description="Who asked it")
    proposed_answer: Optional[str] = Field(default=None, description="AI-proposed answer")
    timestamp: float = Field(default=0.0, description="Timestamp in seconds")

class HighlightsExtraction(BaseModel):
    chapters: List[HighlightChapter] = Field(default_factory=list)
    action_items: List[HighlightActionItem] = Field(default_factory=list)
    key_questions: List[HighlightKeyQuestion] = Field(default_factory=list)


# ── Per-meeting-type Pydantic models ─────────────────────────────────────────

class GenericItem(BaseModel):
    title: str
    summary: str
    speakers: List[str] = Field(default_factory=list)

class GenericOutput(BaseModel):
    meeting_type: str = "generic"
    # Some compatible gateways localize this key despite the English schema.
    # Accept that observed spelling at the validation boundary; serialize as summary.
    summary: str = Field(validation_alias=AliasChoices("summary", "resumen_ejecutivo"))
    topics: List[GenericItem] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)

class GroomingStory(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = None
    acceptance_criteria: List[str] = Field(default_factory=list)
    estimate: Optional[str] = None
    status: Optional[str] = None

class GroomingOutput(BaseModel):
    meeting_type: str = "grooming"
    sprint_name: Optional[str] = None
    items: List[GroomingStory] = Field(default_factory=list)

class StandupUpdate(BaseModel):
    speaker: str
    yesterday: Optional[str] = None
    today: Optional[str] = None
    blockers: Optional[str] = None

class StandupOutput(BaseModel):
    meeting_type: str = "standup"
    items: List[StandupUpdate] = Field(default_factory=list)

class RetroActionItem(BaseModel):
    text: str
    owner: Optional[str] = None
    due: Optional[str] = None

class RetroOutput(BaseModel):
    meeting_type: str = "retro"
    went_well: List[str] = Field(default_factory=list)
    to_improve: List[str] = Field(default_factory=list)
    action_items: List[RetroActionItem] = Field(default_factory=list)

class OneOnOneFollowUp(BaseModel):
    text: str
    owner: Optional[str] = None

class OneOnOneOutput(BaseModel):
    meeting_type: str = "one_on_one"
    talking_points: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    follow_ups: List[OneOnOneFollowUp] = Field(default_factory=list)
    career_notes: List[str] = Field(default_factory=list)

# ── Highlight extraction prompt ─────────────────────────────────────────────

HIGHLIGHT_EXTRACTION_PROMPT = """\
You are a meeting analyst. Given a meeting transcript with speaker labels and timestamps, extract structured highlights.

The input is transcript-only. Do not infer or request visual evidence, and do not expose hidden reasoning.

Return a JSON object with these arrays:

{
  "chapters": [
    {
      "title": "Chapter title (3-8 words)",
      "description": "1-2 sentence description of what was discussed",
      "timestamp_start": 0.0,
      "timestamp_end": 120.5,
      "tags": ["topic1", "topic2"]
    }
  ],
  "action_items": [
    {
      "content": "Clear, actionable task starting with a verb",
      "speaker": "Speaker name or null",
      "assignee": "Person responsible or null",
      "timestamp": 45.2
    }
  ],
  "key_questions": [
    {
      "content": "The question that was asked",
      "speaker": "Who asked it",
      "proposed_answer": "AI-proposed answer based on transcript context, or null if unanswered",
      "timestamp": 78.3
    }
  ]
}

Rules:
- Timestamps are in seconds from start of meeting.
- Use speaker names exactly as they appear in the transcript.
- Chapters should cover the full meeting duration with no gaps.
- Action items must be explicitly stated or clearly implied commitments.
- Key questions are questions raised during the meeting. Include AI-proposed answers when the transcript contains enough context.
- Return valid JSON only. No markdown, no commentary.
"""

# ── Meeting type prompt templates (presets) ──────────────────────────────────

MEETING_TYPE_PROMPTS: dict = {
    "generic": {
        "prompt": "Generate a structured summary of this meeting. Include the main topics discussed, decisions made, and any follow-up items.",
        "model": GenericOutput,
        "layout_hint": "list",
    },
    "grooming": {
        "prompt": (
            "This is a sprint grooming/refinement meeting. Extract user stories discussed. "
            "Each story should follow the format 'As a [role], I want [feature] so that [benefit]'. "
            "Include acceptance criteria as bullet points and story point estimates if discussed."
        ),
        "model": GroomingOutput,
        "layout_hint": "cards",
    },
    "standup": {
        "prompt": (
            "This is a standup/daily scrum meeting. Extract each participant's update. "
            "For each speaker, capture what they did yesterday, what they're doing today, and any blockers."
        ),
        "model": StandupOutput,
        "layout_hint": "table",
    },
    "retro": {
        "prompt": (
            "This is a sprint retrospective meeting. Extract items into three categories: "
            "what went well, what needs improvement, and action items with owners."
        ),
        "model": RetroOutput,
        "layout_hint": "columns",
    },
    "one_on_one": {
        "prompt": (
            "This is a 1:1 meeting. Extract talking points discussed, decisions made, "
            "follow-up items with owners, and any career/growth notes mentioned."
        ),
        "model": OneOnOneOutput,
        "layout_hint": "list",
    },
}


class MeetingIntelligenceService:
    """Extracts structured intelligence from meeting transcripts."""

    def extract_highlights(self, transcript_text: str) -> dict:
        """Extract action items, key questions, and chapters from every window."""
        windows = _transcript_windows(transcript_text)
        combined: dict[str, list] = {"chapters": [], "action_items": [], "key_questions": []}
        for window in windows:
            prompt = HIGHLIGHT_EXTRACTION_PROMPT
            if len(windows) > 1:
                prompt += (
                    "\nAnalyze only this transcript window. Timestamps still refer to the "
                    "whole meeting. Chapters should cover this window, not invent "
                    "content in other windows.\n"
                )
            try:
                response = _client.beta.chat.completions.parse(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": window},
                    ],
                    temperature=0.2,
                    response_format=HighlightsExtraction,
                )
            except ValidationError:
                if len(windows) > 1:
                    raise  # Never save a partial meeting extraction.
                logger.warning(
                    "extract_highlights: response did not match the structured schema; "
                    "falling back to empty highlights"
                )
                return combined
            parsed = response.choices[0].message.parsed
            if parsed is None:
                if len(windows) > 1:
                    raise ValueError("highlight extraction returned no parsed window")
                logger.warning("extract_highlights: parse returned None, falling back to empty")
                return combined
            for key in combined:
                combined[key].extend(parsed.model_dump()[key])
        return combined

    def extract_structured_output(self, transcript_text: str, meeting_type: str) -> dict:
        """Call 2: Extract meeting-type-specific structured output."""
        type_config = MEETING_TYPE_PROMPTS.get(meeting_type, MEETING_TYPE_PROMPTS["generic"])
        pydantic_model = type_config["model"]
        transcript_only_prompt = (
            "The input is transcript-only. Use only explicitly supported spoken evidence; "
            "do not infer visual facts or expose hidden reasoning. "
            "Return valid JSON only, matching the supplied response schema. "
            "Do not include Markdown, headings, or commentary.\n\n"
        )

        windows = _transcript_windows(transcript_text)
        prompt = transcript_only_prompt + type_config["prompt"]
        if len(windows) > 1:
            prompt += (
                " Analyze only this transcript window. Do not infer facts from "
                "parts of the meeting that are not in this window."
            )
        outputs: list[dict] = []
        for window in windows:
            response = _client.beta.chat.completions.parse(
                model=settings.STRUCTURED_OUTPUT_MODEL or settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": window},
                ],
                temperature=0.2,
                response_format=pydantic_model,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError("structured extraction returned no parsed window")
            outputs.append(parsed.model_dump())
        return outputs[0] if len(outputs) == 1 else _combine_structured_windows(outputs)

    def save_highlights(
        self,
        meeting_id: int,
        highlights_data: dict,
        *,
        owner_id: int | None,
        processing_run_id: int | None,
        dispatch_attempt_id: str | None,
        audit_task_id: str | None = None,
    ) -> list[MeetingHighlight] | HighlightSaveRejected:
        """Replace highlights only for the exact current meeting owner/attempt.

        With ``audit_task_id`` the split highlight stage's completed resume
        marker is added to this same transaction, so a crash cannot leave
        saved highlights without their marker and repeat the extraction call
        on resume.
        """
        if owner_id is None or processing_run_id is None or not dispatch_attempt_id:
            return HighlightSaveRejected.STALE_OR_CANCELLED
        with Session(engine) as session:
            meeting, run = meeting_processing_audit._locked_current_run(
                session, processing_run_id, meeting_id, dispatch_attempt_id
            )
            if (
                meeting is None
                or run is None
                or meeting.owner_id != owner_id
                or run.owner_id != owner_id
            ):
                return HighlightSaveRejected.STALE_OR_CANCELLED

            # Delete existing highlights for this meeting
            existing = session.exec(
                select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            ).all()
            for h in existing:
                session.delete(h)

            saved = []
            sort_order = 0

            # Chapters
            for ch in highlights_data.get("chapters", []):
                highlight = MeetingHighlight(
                    meeting_id=meeting_id,
                    highlight_type="chapter",
                    content=ch["title"],
                    timestamp_start=ch.get("timestamp_start", 0.0),
                    timestamp_end=ch.get("timestamp_end"),
                    extra_metadata={
                        "description": ch.get("description", ""),
                        "tags": ch.get("tags", []),
                    },
                    sort_order=sort_order,
                )
                session.add(highlight)
                saved.append(highlight)
                sort_order += 1

            # Action items
            for ai in highlights_data.get("action_items", []):
                highlight = MeetingHighlight(
                    meeting_id=meeting_id,
                    highlight_type="action_item",
                    content=ai["content"],
                    speaker=ai.get("speaker"),
                    timestamp_start=ai.get("timestamp", 0.0),
                    extra_metadata={"assignee": ai.get("assignee")},
                    sort_order=sort_order,
                )
                session.add(highlight)
                saved.append(highlight)
                sort_order += 1

            # Key questions
            for kq in highlights_data.get("key_questions", []):
                highlight = MeetingHighlight(
                    meeting_id=meeting_id,
                    highlight_type="key_question",
                    content=kq["content"],
                    speaker=kq.get("speaker"),
                    timestamp_start=kq.get("timestamp", 0.0),
                    ai_answer=kq.get("proposed_answer"),
                    sort_order=sort_order,
                )
                session.add(highlight)
                saved.append(highlight)
                sort_order += 1

            if audit_task_id:
                meeting_processing_audit.stage_completed_event_for_output(
                    session,
                    run=run,
                    meeting_id=meeting_id,
                    attempt_id=dispatch_attempt_id,
                    stage="stage_extract_highlights",
                    task_id=audit_task_id,
                    message=f"Saved {len(saved)} meeting highlights",
                    metadata={
                        "transcript_fingerprint": transcript_fingerprint(
                            meeting.transcript_text
                        )
                    },
                )
            session.commit()
            return saved

    def get_highlights(
        self, meeting_id: int, highlight_type: Optional[str] = None
    ) -> list[MeetingHighlight]:
        """Retrieve highlights for a meeting, optionally filtered by type."""
        with Session(engine) as session:
            stmt = select(MeetingHighlight).where(MeetingHighlight.meeting_id == meeting_id)
            if highlight_type:
                stmt = stmt.where(MeetingHighlight.highlight_type == highlight_type)
            stmt = stmt.order_by(col(MeetingHighlight.sort_order))
            return list(session.exec(stmt).all())

    def get_layout_hint(self, meeting_type: str) -> str:
        """Get the layout hint for a meeting type."""
        type_config = MEETING_TYPE_PROMPTS.get(meeting_type, MEETING_TYPE_PROMPTS["generic"])
        return type_config["layout_hint"]


intelligence_service = MeetingIntelligenceService()
