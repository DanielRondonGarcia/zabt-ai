# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""add meeting processing heartbeat

Revision ID: n2o3p4q5r6
Revises: m1n2o3p4q5
Create Date: 2026-09-12
"""

import sqlalchemy as sa
from alembic import op


revision = "n2o3p4q5r6"
down_revision = "m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting",
        sa.Column("processing_heartbeat_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting", "processing_heartbeat_at")
