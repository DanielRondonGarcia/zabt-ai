# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Add an optional custom endpoint for admin-managed S3 storage."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "t0u1v2w3x4y5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "storageconfiguration",
        sa.Column("s3_endpoint_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("storageconfiguration", "s3_endpoint_url")
