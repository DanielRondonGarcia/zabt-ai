# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add per-meeting storage provider attribution."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t0u1v2w3x4y5"
down_revision: Union[str, None] = "s9t0u1v2w3x4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("meeting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "storage_provider",
                sa.String(length=32),
                nullable=False,
                server_default="minio",
            )
        )
        batch_op.create_check_constraint(
            "ck_meeting_storage_provider",
            "storage_provider IN ('minio', 'aws_s3')",
        )


def downgrade() -> None:
    with op.batch_alter_table("meeting") as batch_op:
        batch_op.drop_constraint("ck_meeting_storage_provider", type_="check")
        batch_op.drop_column("storage_provider")
