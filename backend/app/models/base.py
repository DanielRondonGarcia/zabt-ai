# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from datetime import datetime
from typing import Any, Optional, List, Literal
from enum import Enum
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB


class TranscriptionType(str, Enum):
    GENERAL = "general"
    MEDICAL = "medical"


class TranscriptionBackend(str, Enum):
    """Compatibility enum for the existing local and RunPod transports."""

    RUNPOD = "runpod"
    GPU_LOCAL = "gpu-local"


class MeetingType(str, Enum):
    GENERIC = "generic"
    GROOMING = "grooming"
    STANDUP = "standup"
    RETRO = "retro"
    ONE_ON_ONE = "one_on_one"


class SummaryTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    body: str
    template_type: str = Field(default="custom")  # "built_in" | "custom"
    is_system_default: bool = Field(default=False)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Meeting type support
    meeting_type: Optional[str] = Field(default=None)
    output_schema: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    layout_hint: Optional[str] = Field(default=None)  # "cards", "table", "columns", "list"


class Group(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Relationship to meetings
    meetings: List["Meeting"] = Relationship(
        back_populates="group",
        sa_relationship_kwargs={"passive_deletes": True},
    )


class SummaryTemplateRead(SQLModel):
    id: int
    name: str
    body: str
    template_type: str
    is_system_default: bool
    owner_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class SummaryTemplateListItem(SQLModel):
    id: int
    name: str
    template_type: str
    is_system_default: bool


class GroupRead(SQLModel):
    id: int
    name: str
    description: Optional[str]
    owner_id: int
    created_at: datetime
    updated_at: datetime


class GroupCreate(SQLModel):
    name: str
    description: Optional[str] = None


class GroupUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None


class LanguageEntry(SQLModel, table=True):
    __tablename__ = "language_entry"
    code: str = Field(primary_key=True)
    display_name: str
    whisper_lang: str
    script: str
    transliterate_from: Optional[str] = None
    is_default: bool = Field(default=False)


class LanguageEntryRead(SQLModel):
    code: str
    display_name: str
    whisper_lang: str
    script: str
    transliterate_from: Optional[str] = None
    is_default: bool


class UserTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class UserBase(SQLModel):
    email: str = Field(unique=True, index=True)
    full_name: Optional[str] = None
    picture: Optional[str] = None
    tier: UserTier = Field(default=UserTier.FREE)
    is_active: bool = True
    minutes_used_this_month: int = Field(default=0)
    # Kept nullable for compatibility with data created by the former Supabase
    # integration. New local users intentionally leave this field unset.
    supabase_id: Optional[str] = Field(default=None, unique=True, index=True)
    password_hash: Optional[str] = Field(default=None)

class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    default_template_id: Optional[int] = Field(default=None, foreign_key="summarytemplate.id")
    language_preferences: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSONB)
    )

    meetings: List["Meeting"] = Relationship(back_populates="owner")


class AuthSession(SQLModel, table=True):
    """A revocable database-backed refresh session.

    Only the SHA-256 digest of the refresh token is stored. The raw token is
    returned once to the client and is never persisted or logged by the API.
    """

    __tablename__ = "authsession"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    refresh_token_hash: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    last_used_at: Optional[datetime] = None

class MeetingBase(SQLModel):
    title: str
    description: Optional[str] = None
    file_path: Optional[str] = None # Optional now as real-time might not have file immediately
    duration_seconds: Optional[int] = None

class Meeting(MeetingBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    owner: Optional[User] = Relationship(back_populates="meetings")
    # Group assignment - nullable FK to Group
    group_id: Optional[int] = Field(default=None, foreign_key="group.id", index=True)
    group: Optional[Group] = Relationship(back_populates="meetings")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="pending_upload") # pending_upload, queued, processing, completed, failed
    sub_status: Optional[str] = None
    processing_heartbeat_at: Optional[datetime] = None
    transcript_text: Optional[str] = None
    summary_text: Optional[str] = None
    original_summary_text: Optional[str] = None
    summary_edited: bool = Field(default=False)
    action_items_text: Optional[str] = None
    template_id: Optional[int] = Field(default=None, foreign_key="summarytemplate.id")
    template_name: Optional[str] = None

    # Transcription model selection
    transcription_type: TranscriptionType = Field(default=TranscriptionType.GENERAL)

    # Meeting intelligence
    meeting_type: str = Field(default="generic")
    structured_output: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    structured_output_status: str = Field(default="pending")  # pending, processing, completed, failed

    # YouTube ingestion fields
    source_type: str = Field(default="upload")  # "upload" or "youtube"
    source_url: Optional[str] = None  # Original YouTube URL
    youtube_title: Optional[str] = None
    youtube_duration_seconds: Optional[int] = None
    youtube_thumbnail_url: Optional[str] = None
    youtube_channel: Optional[str] = None
    requested_language: Optional[str] = None
    transliterated_text: Optional[str] = None

    segments: List["TranscriptSegment"] = Relationship(back_populates="meeting")
    visual_segments: List["VisualSegment"] = Relationship(
        back_populates="meeting",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    # Visual breakdown (reuses the existing Plan 2 columns; no media-kind or
    # fused-context migration).  Status values are queued, processing,
    # completed, skipped, or fallback.  Run epochs and one-time side-effect
    # markers live in visual_breakdown_params.
    visual_breakdown_status: Optional[str] = None
    visual_breakdown_error: Optional[str] = None
    visual_breakdown_completed_at: Optional[datetime] = None
    visual_raw_output_s3_key: Optional[str] = None
    visual_breakdown_model: Optional[str] = None
    visual_breakdown_params: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    visual_breakdown_run_count: int = Field(default=0)

class TranscriptSegment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id", index=True)
    meeting: Optional[Meeting] = Relationship(back_populates="segments")
    start_time: float
    end_time: float
    text: str
    # Provider normalizers use SPEAKER_UNKNOWN when labels are unavailable;
    # nullable storage remains compatible with existing transcript rows.
    speaker: Optional[str] = None
    words: List[dict] = Field(default_factory=list, sa_column=Column(JSONB))

class VisualSegment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id", index=True)
    start_time: float
    end_time: float
    screenshot_s3_key: str
    caption: str
    sequence: int
    confidence: float
    created_at: datetime = Field(default_factory=datetime.utcnow)

    meeting: Optional["Meeting"] = Relationship(back_populates="visual_segments")

class StyleProfileBase(SQLModel):
    name: str = Field(index=True)
    description: Optional[str] = None
    prompt_template: str

class StyleProfile(StyleProfileBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    # owner: Optional[User] = Relationship(back_populates="styles") # Optional: add styles rel to User

class StyleProfileCreate(StyleProfileBase):
    pass

class StyleProfileRead(StyleProfileBase):
    id: int
    owner_id: int

class SubscriptionTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class Subscription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    stripe_custer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    plan: SubscriptionTier = Field(default=SubscriptionTier.FREE)
    is_active: bool = True
    current_period_end: Optional[float] = None

class MeetingCreate(MeetingBase):
    pass

class TranscriptWordRead(SQLModel):
    word: str
    start: float
    end: float

class TranscriptSegmentRead(SQLModel):
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: List[TranscriptWordRead] = []

class SpeakerBreakdown(SQLModel):
    percentage: float
    name: str

class MeetingRead(MeetingBase):
    id: int
    owner_id: int
    group_id: Optional[int] = None
    created_at: datetime
    status: str
    sub_status: Optional[str] = None
    transcript_text: Optional[str] = None
    summary_text: Optional[str] = None
    original_summary_text: Optional[str] = None
    summary_edited: bool = False
    action_items_text: Optional[str] = None
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    transcription_type: TranscriptionType = TranscriptionType.GENERAL
    segments: List[TranscriptSegmentRead] = []
    speakers: Optional[dict] = None
    # YouTube ingestion fields
    source_type: str = "upload"
    source_url: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_duration_seconds: Optional[int] = None
    youtube_thumbnail_url: Optional[str] = None
    youtube_channel: Optional[str] = None
    requested_language: Optional[str] = None
    transliterated_text: Optional[str] = None
    # Meeting intelligence fields
    meeting_type: str = "generic"
    structured_output: Optional[Any] = None
    structured_output_status: str = "pending"
    highlights: List[dict] = []
    layout_hint: Optional[str] = None
    audio_url: Optional[str] = None
    media_type: Optional[Literal["audio", "video"]] = None
    # Visual breakdown (Plan 2 — frontend uses this to drive the tab state machine)
    visual_breakdown_status: Optional[str] = None
    visual_breakdown_error: Optional[str] = None
    visual_breakdown_completed_at: Optional[datetime] = None


class MeetingSummaryUpdate(SQLModel):
    summary_text: str
