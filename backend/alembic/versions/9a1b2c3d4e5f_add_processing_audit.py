"""add_processing_audit

Revision ID: 9a1b2c3d4e5f
Revises: 8f0dd5fa9efd
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9a1b2c3d4e5f"
down_revision: Union[str, None] = "8f0dd5fa9efd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meetingprocessingrun",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("root_task_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("final_error", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meeting.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_run_meeting_created", "meetingprocessingrun", ["meeting_id", "created_at"], unique=False)
    op.create_index("ix_processing_run_owner_meeting", "meetingprocessingrun", ["owner_id", "meeting_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_meeting_id"), "meetingprocessingrun", ["meeting_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_owner_id"), "meetingprocessingrun", ["owner_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_root_task_id"), "meetingprocessingrun", ["root_task_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_status"), "meetingprocessingrun", ["status"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_trigger"), "meetingprocessingrun", ["trigger"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_created_at"), "meetingprocessingrun", ["created_at"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_started_at"), "meetingprocessingrun", ["started_at"], unique=False)
    op.create_index(op.f("ix_meetingprocessingrun_completed_at"), "meetingprocessingrun", ["completed_at"], unique=False)

    op.create_table(
        "meetingprocessingevent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("message", sa.String(length=1000), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meeting.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["meetingprocessingrun.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_event_run_created", "meetingprocessingevent", ["run_id", "created_at"], unique=False)
    op.create_index("ix_processing_event_meeting_created", "meetingprocessingevent", ["meeting_id", "created_at"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_run_id"), "meetingprocessingevent", ["run_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_meeting_id"), "meetingprocessingevent", ["meeting_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_stage"), "meetingprocessingevent", ["stage"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_event_type"), "meetingprocessingevent", ["event_type"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_status"), "meetingprocessingevent", ["status"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_task_id"), "meetingprocessingevent", ["task_id"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_created_at"), "meetingprocessingevent", ["created_at"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_started_at"), "meetingprocessingevent", ["started_at"], unique=False)
    op.create_index(op.f("ix_meetingprocessingevent_completed_at"), "meetingprocessingevent", ["completed_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meetingprocessingevent_completed_at"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_started_at"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_created_at"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_task_id"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_status"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_event_type"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_stage"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_meeting_id"), table_name="meetingprocessingevent")
    op.drop_index(op.f("ix_meetingprocessingevent_run_id"), table_name="meetingprocessingevent")
    op.drop_index("ix_processing_event_meeting_created", table_name="meetingprocessingevent")
    op.drop_index("ix_processing_event_run_created", table_name="meetingprocessingevent")
    op.drop_table("meetingprocessingevent")

    op.drop_index(op.f("ix_meetingprocessingrun_completed_at"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_started_at"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_created_at"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_trigger"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_status"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_root_task_id"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_owner_id"), table_name="meetingprocessingrun")
    op.drop_index(op.f("ix_meetingprocessingrun_meeting_id"), table_name="meetingprocessingrun")
    op.drop_index("ix_processing_run_owner_meeting", table_name="meetingprocessingrun")
    op.drop_index("ix_processing_run_meeting_created", table_name="meetingprocessingrun")
    op.drop_table("meetingprocessingrun")
