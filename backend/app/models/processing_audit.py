# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Durable, user-safe meeting processing audit models."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class MeetingProcessingRun(SQLModel, table=True):
    __tablename__ = "meetingprocessingrun"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    trigger: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    status: str = Field(default="queued", sa_column=Column(String(40), nullable=False, index=True))
    root_task_id: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True, index=True))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    completed_at: Optional[datetime] = Field(default=None, index=True)
    final_error: Optional[str] = Field(default=None, sa_column=Column(String(1000), nullable=True))


class MeetingProcessingEvent(SQLModel, table=True):
    __tablename__ = "meetingprocessingevent"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="meetingprocessingrun.id", index=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    stage: str = Field(sa_column=Column(String(100), nullable=False, index=True))
    event_type: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    status: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    task_id: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True, index=True))
    message: Optional[str] = Field(default=None, sa_column=Column(String(1000), nullable=True))
    error: Optional[str] = Field(default=None, sa_column=Column(String(1000), nullable=True))
    metadata_: Optional[dict[str, Any]] = Field(default=None, sa_column=Column("metadata", JSONB, nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    completed_at: Optional[datetime] = Field(default=None, index=True)


class MeetingProcessingEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    meeting_id: int
    stage: str
    event_type: str
    status: str
    task_id: Optional[str]
    message: Optional[str]
    error: Optional[str]
    metadata: Optional[dict[str, Any]]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class MeetingProcessingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    owner_id: int
    trigger: str
    status: str
    root_task_id: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    final_error: Optional[str]
    events: list[MeetingProcessingEventRead] = []


class MeetingProcessingAuditRead(BaseModel):
    meeting_id: int
    runs: list[MeetingProcessingRunRead]
