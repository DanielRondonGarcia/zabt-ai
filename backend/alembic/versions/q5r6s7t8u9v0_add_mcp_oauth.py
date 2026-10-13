# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add public-client MCP OAuth registrations, grants, and opaque tokens.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9, n2o3p4q5r6
Create Date: 2026-10-09
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "q5r6s7t8u9v0"
down_revision: tuple[str, str] = ("p4q5r6s7t8u9", "n2o3p4q5r6")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcpclientregistration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("client_name", sa.String(length=100), nullable=False),
        sa.Column("redirect_uris_json", sa.Text(), nullable=False),
        sa.Column("grant_types_json", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("length(trim(client_name)) > 0", name="ck_mcpclientregistration_name"),
        sa.CheckConstraint(
            "length(trim(redirect_uris_json)) > 2",
            name="ck_mcpclientregistration_redirects",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_mcpclientregistration_client_id"),
    )
    op.create_index(
        "ix_mcpclientregistration_client_id",
        "mcpclientregistration",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcpclientregistration_revoked_at",
        "mcpclientregistration",
        ["revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_mcpclientregistration_active",
        "mcpclientregistration",
        ["revoked_at", "created_at"],
        unique=False,
    )

    op.create_table(
        "mcpgrant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_registration_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("resource", sa.String(length=2048), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_registration_id"],
            ["mcpclientregistration.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_registration_id",
            "user_id",
            "resource",
            name="uq_mcpgrant_client_user_resource",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_mcpgrant_generation_nonnegative"),
    )
    op.create_index("ix_mcpgrant_client_registration_id", "mcpgrant", ["client_registration_id"])
    op.create_index("ix_mcpgrant_user_id", "mcpgrant", ["user_id"])
    op.create_index("ix_mcpgrant_revoked_at", "mcpgrant", ["revoked_at"])
    op.create_index(
        "ix_mcpgrant_user_lifecycle",
        "mcpgrant",
        ["user_id", "revoked_at", "created_at"],
    )
    op.create_index(
        "ix_mcpgrant_client_lifecycle",
        "mcpgrant",
        ["client_registration_id", "revoked_at"],
    )

    op.create_table(
        "mcpaccesstoken",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["grant_id"], ["mcpgrant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_mcpaccesstoken_token_hash"),
    )
    op.create_index("ix_mcpaccesstoken_grant_id", "mcpaccesstoken", ["grant_id"])
    op.create_index("ix_mcpaccesstoken_expires_at", "mcpaccesstoken", ["expires_at"])
    op.create_index("ix_mcpaccesstoken_revoked_at", "mcpaccesstoken", ["revoked_at"])
    op.create_index(
        "ix_mcpaccesstoken_lifecycle",
        "mcpaccesstoken",
        ["grant_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "mcprefreshtoken",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["grant_id"], ["mcpgrant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_mcprefreshtoken_token_hash"),
    )
    op.create_index("ix_mcprefreshtoken_grant_id", "mcprefreshtoken", ["grant_id"])
    op.create_index("ix_mcprefreshtoken_expires_at", "mcprefreshtoken", ["expires_at"])
    op.create_index("ix_mcprefreshtoken_used_at", "mcprefreshtoken", ["used_at"])
    op.create_index("ix_mcprefreshtoken_revoked_at", "mcprefreshtoken", ["revoked_at"])
    op.create_index(
        "ix_mcprefreshtoken_lifecycle",
        "mcprefreshtoken",
        ["grant_id", "revoked_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcprefreshtoken_lifecycle", table_name="mcprefreshtoken")
    op.drop_index("ix_mcprefreshtoken_revoked_at", table_name="mcprefreshtoken")
    op.drop_index("ix_mcprefreshtoken_used_at", table_name="mcprefreshtoken")
    op.drop_index("ix_mcprefreshtoken_expires_at", table_name="mcprefreshtoken")
    op.drop_index("ix_mcprefreshtoken_grant_id", table_name="mcprefreshtoken")
    op.drop_table("mcprefreshtoken")

    op.drop_index("ix_mcpaccesstoken_lifecycle", table_name="mcpaccesstoken")
    op.drop_index("ix_mcpaccesstoken_revoked_at", table_name="mcpaccesstoken")
    op.drop_index("ix_mcpaccesstoken_expires_at", table_name="mcpaccesstoken")
    op.drop_index("ix_mcpaccesstoken_grant_id", table_name="mcpaccesstoken")
    op.drop_table("mcpaccesstoken")

    op.drop_index("ix_mcpgrant_client_lifecycle", table_name="mcpgrant")
    op.drop_index("ix_mcpgrant_user_lifecycle", table_name="mcpgrant")
    op.drop_index("ix_mcpgrant_revoked_at", table_name="mcpgrant")
    op.drop_index("ix_mcpgrant_user_id", table_name="mcpgrant")
    op.drop_index("ix_mcpgrant_client_registration_id", table_name="mcpgrant")
    op.drop_table("mcpgrant")

    op.drop_index("ix_mcpclientregistration_active", table_name="mcpclientregistration")
    op.drop_index("ix_mcpclientregistration_revoked_at", table_name="mcpclientregistration")
    op.drop_index("ix_mcpclientregistration_client_id", table_name="mcpclientregistration")
    op.drop_table("mcpclientregistration")
