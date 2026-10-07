# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add external identities for OIDC-backed application sign-in.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "externalidentity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "tenant_id",
            "subject",
            name="uq_externalidentity_provider_tenant_subject",
        ),
    )
    op.create_index("ix_externalidentity_user_id", "externalidentity", ["user_id"])
    op.create_index(
        "ix_externalidentity_user_provider",
        "externalidentity",
        ["user_id", "provider"],
    )
    op.create_index("ix_externalidentity_email", "externalidentity", ["email"])
    op.create_index("ix_externalidentity_tenant_id", "externalidentity", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_externalidentity_tenant_id", table_name="externalidentity")
    op.drop_index("ix_externalidentity_email", table_name="externalidentity")
    op.drop_index("ix_externalidentity_user_provider", table_name="externalidentity")
    op.drop_index("ix_externalidentity_user_id", table_name="externalidentity")
    op.drop_table("externalidentity")
