# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add user-managed bearer tokens for the read-only MCP surface.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcptoken",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_mcptoken_token_hash"),
        sa.CheckConstraint("length(trim(label)) > 0", name="ck_mcptoken_label_nonempty"),
    )
    op.create_index("ix_mcptoken_user_id", "mcptoken", ["user_id"])
    op.create_index("ix_mcptoken_expires_at", "mcptoken", ["expires_at"])
    op.create_index("ix_mcptoken_revoked_at", "mcptoken", ["revoked_at"])
    op.create_index(
        "ix_mcptoken_user_lifecycle",
        "mcptoken",
        ["user_id", "revoked_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcptoken_user_lifecycle", table_name="mcptoken")
    op.drop_index("ix_mcptoken_revoked_at", table_name="mcptoken")
    op.drop_index("ix_mcptoken_expires_at", table_name="mcptoken")
    op.drop_index("ix_mcptoken_user_id", table_name="mcptoken")
    op.drop_table("mcptoken")
