# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""add current meeting processing generation"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r8s9t0u1v2w3"
down_revision: Union[str, None] = "q5r6s7t8u9v0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meeting", sa.Column("current_processing_run_id", sa.Integer(), nullable=True))
    op.add_column(
        "meetingprocessingrun",
        sa.Column("dispatch_attempt_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "meetingprocessingevent",
        sa.Column("dispatch_attempt_id", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_meetingprocessingrun_dispatch_attempt_id"),
        "meetingprocessingrun",
        ["dispatch_attempt_id"],
        unique=True,
    )
    op.create_index(
        "uq_meetingprocessingevent_transliteration_claim",
        "meetingprocessingevent",
        ["run_id", "dispatch_attempt_id", "stage"],
        unique=True,
        postgresql_where=sa.text(
            "event_type = 'started' AND stage = 'stage_transliterate' "
            "AND dispatch_attempt_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_meetingprocessingevent_summary_claim",
        "meetingprocessingevent",
        ["run_id", "dispatch_attempt_id", "stage"],
        unique=True,
        postgresql_where=sa.text(
            "event_type = 'started' AND stage = 'stage_summarize' "
            "AND dispatch_attempt_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_meetingprocessingevent_intelligence_claim",
        "meetingprocessingevent",
        ["run_id", "dispatch_attempt_id", "stage"],
        unique=True,
        postgresql_where=sa.text(
            "event_type = 'started' AND stage = 'stage_extract_intelligence' "
            "AND dispatch_attempt_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_meetingprocessingevent_youtube_claim",
        "meetingprocessingevent",
        ["run_id", "dispatch_attempt_id", "stage"],
        unique=True,
        postgresql_where=sa.text(
            "event_type = 'started' AND stage = 'stage_youtube_download' "
            "AND dispatch_attempt_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_meetingprocessingevent_youtube_claim",
        table_name="meetingprocessingevent",
    )
    op.drop_index(
        "uq_meetingprocessingevent_intelligence_claim",
        table_name="meetingprocessingevent",
    )
    op.drop_index(
        "uq_meetingprocessingevent_summary_claim",
        table_name="meetingprocessingevent",
    )
    op.drop_index(
        "uq_meetingprocessingevent_transliteration_claim",
        table_name="meetingprocessingevent",
    )
    op.drop_column("meetingprocessingevent", "dispatch_attempt_id")
    op.drop_index(
        op.f("ix_meetingprocessingrun_dispatch_attempt_id"),
        table_name="meetingprocessingrun",
    )
    op.drop_column("meetingprocessingrun", "dispatch_attempt_id")
    op.drop_column("meeting", "current_processing_run_id")
