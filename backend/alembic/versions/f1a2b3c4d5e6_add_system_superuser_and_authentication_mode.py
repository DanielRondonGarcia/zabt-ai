"""Add the system superuser role and global user login mode.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("user", "is_superuser", server_default=None)
    op.create_index("ix_user_is_superuser", "user", ["is_superuser"])

    op.create_table(
        "authenticationconfiguration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_login_mode",
            sa.String(length=32),
            nullable=False,
            server_default="local",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "id = 1",
            name="ck_authenticationconfiguration_singleton_id",
        ),
        sa.CheckConstraint(
            "user_login_mode IN ('local', 'microsoft_oidc')",
            name="ck_authenticationconfiguration_user_login_mode",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authenticationconfiguration_updated_by",
        "authenticationconfiguration",
        ["updated_by"],
    )
    op.execute(
        sa.text(
            "INSERT INTO authenticationconfiguration "
            "(id, user_login_mode, updated_at, updated_by) "
            "VALUES (1, 'local', CURRENT_TIMESTAMP, NULL)"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authenticationconfiguration_updated_by",
        table_name="authenticationconfiguration",
    )
    op.drop_table("authenticationconfiguration")
    op.drop_index("ix_user_is_superuser", table_name="user")
    op.drop_column("user", "is_superuser")
