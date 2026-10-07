"""Add global public Microsoft OIDC configuration and admin bootstrap.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing installations need one bootstrap administrator. The minimum
    # user id is deterministic and all other users retain the false default.
    op.add_column(
        "user",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        sa.text(
            'UPDATE "user" SET is_admin = true '
            'WHERE id = (SELECT MIN(id) FROM "user")'
        )
    )
    op.alter_column("user", "is_admin", server_default=None)
    op.create_index("ix_user_is_admin", "user", ["is_admin"])

    op.create_table(
        "microsoftoidcconfiguration",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            name="uq_microsoftoidcconfiguration_provider",
        ),
    )
    op.create_index(
        "ix_microsoftoidcconfiguration_enabled",
        "microsoftoidcconfiguration",
        ["enabled"],
    )
    op.create_index(
        "ix_microsoftoidcconfiguration_updated_by",
        "microsoftoidcconfiguration",
        ["updated_by"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_microsoftoidcconfiguration_updated_by",
        table_name="microsoftoidcconfiguration",
    )
    op.drop_index(
        "ix_microsoftoidcconfiguration_enabled",
        table_name="microsoftoidcconfiguration",
    )
    op.drop_table("microsoftoidcconfiguration")
    op.drop_index("ix_user_is_admin", table_name="user")
    op.drop_column("user", "is_admin")
