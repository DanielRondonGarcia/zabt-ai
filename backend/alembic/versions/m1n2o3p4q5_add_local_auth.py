# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add first-party local authentication fields and refresh sessions.

Revision ID: m1n2o3p4q5
Revises: l0m1n2o3p4q5
Create Date: 2026-09-11
"""

import sqlalchemy as sa
from alembic import op


revision = "m1n2o3p4q5"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing Supabase-only profiles remain usable as historical records, but
    # local registrations need a nullable password hash and identity column.
    op.alter_column(
        "user",
        "supabase_id",
        existing_type=sa.String(),
        nullable=True,
    )
    op.add_column("user", sa.Column("password_hash", sa.String(), nullable=True))

    op.create_table(
        "authsession",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_authsession_user_id", "authsession", ["user_id"])
    op.create_index(
        "ix_authsession_refresh_token_hash",
        "authsession",
        ["refresh_token_hash"],
        unique=True,
    )
    op.create_index("ix_authsession_expires_at", "authsession", ["expires_at"])
    op.create_index("ix_authsession_revoked_at", "authsession", ["revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_authsession_revoked_at", table_name="authsession")
    op.drop_index("ix_authsession_expires_at", table_name="authsession")
    op.drop_index("ix_authsession_refresh_token_hash", table_name="authsession")
    op.drop_index("ix_authsession_user_id", table_name="authsession")
    op.drop_table("authsession")
    op.drop_column("user", "password_hash")

    # Do not silently destroy local identities during a downgrade. An
    # administrator can remove/convert those rows first, after which the
    # historical non-null constraint can be restored.
    connection = op.get_bind()
    has_local_users = connection.execute(
        sa.text('SELECT 1 FROM "user" WHERE supabase_id IS NULL LIMIT 1')
    ).first()
    if has_local_users:
        raise RuntimeError(
            "Cannot downgrade local auth while users without supabase_id exist"
        )
    op.alter_column(
        "user",
        "supabase_id",
        existing_type=sa.String(),
        nullable=False,
    )
