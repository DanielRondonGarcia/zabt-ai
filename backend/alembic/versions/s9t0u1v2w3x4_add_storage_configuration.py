# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add singleton global object-storage settings."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s9t0u1v2w3x4"
down_revision: Union[str, None] = "r8s9t0u1v2w3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storageconfiguration",
        sa.Column("id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="minio",
        ),
        sa.Column("aws_region", sa.String(length=64), nullable=True),
        sa.Column("aws_bucket", sa.String(length=255), nullable=True),
        sa.Column("aws_access_key_id", sa.String(length=256), nullable=True),
        sa.Column("encrypted_aws_secret_access_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_storageconfiguration_singleton"),
        sa.CheckConstraint(
            "provider IN ('minio', 'aws_s3')",
            name="ck_storageconfiguration_provider",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO storageconfiguration (id, provider) VALUES (1, 'minio')"
        )
    )


def downgrade() -> None:
    op.drop_table("storageconfiguration")
