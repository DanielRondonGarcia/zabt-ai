"""add_ai_chat_conversations

Revision ID: b7c8d9e0f1a2
Revises: 9a1b2c3d4e5f
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "9a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aichatconversation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["group.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aichatconversation_owner_id"), "aichatconversation", ["owner_id"], unique=False)
    op.create_index(op.f("ix_aichatconversation_group_id"), "aichatconversation", ["group_id"], unique=False)
    op.create_index(op.f("ix_aichatconversation_updated_at"), "aichatconversation", ["updated_at"], unique=False)
    op.create_index(
        "ix_aichatconversation_owner_group_updated",
        "aichatconversation",
        ["owner_id", "group_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "aichatmessage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["aichatconversation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aichatmessage_conversation_id"), "aichatmessage", ["conversation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_aichatmessage_conversation_id"), table_name="aichatmessage")
    op.drop_table("aichatmessage")

    op.drop_index("ix_aichatconversation_owner_group_updated", table_name="aichatconversation")
    op.drop_index(op.f("ix_aichatconversation_updated_at"), table_name="aichatconversation")
    op.drop_index(op.f("ix_aichatconversation_group_id"), table_name="aichatconversation")
    op.drop_index(op.f("ix_aichatconversation_owner_id"), table_name="aichatconversation")
    op.drop_table("aichatconversation")
