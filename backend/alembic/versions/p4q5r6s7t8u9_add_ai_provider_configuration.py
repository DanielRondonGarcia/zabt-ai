"""Add per-user AI provider configuration.

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p4q5r6s7t8u9"
down_revision: Union[str, None] = "o3p4q5r6s7t8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aiproviderconfiguration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("use_for_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("use_for_chat", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "provider IN ('openai', 'anthropic', 'ollama')",
            name="ck_aiproviderconfiguration_provider",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_aiproviderconfiguration_user_id"),
    )
    op.create_index(
        op.f("ix_aiproviderconfiguration_user_id"),
        "aiproviderconfiguration",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_aiproviderconfiguration_enabled",
        "aiproviderconfiguration",
        ["enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_aiproviderconfiguration_enabled", table_name="aiproviderconfiguration")
    op.drop_index(
        op.f("ix_aiproviderconfiguration_user_id"),
        table_name="aiproviderconfiguration",
    )
    op.drop_table("aiproviderconfiguration")
