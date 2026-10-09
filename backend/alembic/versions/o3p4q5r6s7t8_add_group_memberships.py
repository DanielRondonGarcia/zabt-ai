"""add direct group memberships

Revision ID: o3p4q5r6s7t8
Revises: f1a2b3c4d5e6
Create Date: 2026-10-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "o3p4q5r6s7t8"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "groupmembership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('viewer', 'editor')",
            name="ck_groupmembership_role",
        ),
        sa.ForeignKeyConstraint(["group_id"], ["group.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_groupmembership_group_user",
        ),
    )
    op.create_index(
        op.f("ix_groupmembership_group_id"),
        "groupmembership",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_groupmembership_user_id"),
        "groupmembership",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_groupmembership_group_role",
        "groupmembership",
        ["group_id", "role"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_groupmembership_group_role", table_name="groupmembership")
    op.drop_index(op.f("ix_groupmembership_user_id"), table_name="groupmembership")
    op.drop_index(op.f("ix_groupmembership_group_id"), table_name="groupmembership")
    op.drop_table("groupmembership")
