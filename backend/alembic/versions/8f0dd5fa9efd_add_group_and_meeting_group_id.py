"""add_group_and_meeting_group_id

Revision ID: 8f0dd5fa9efd
Revises: n2o3p4q5r6
Create Date: 2026-09-18 06:37:15.795007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f0dd5fa9efd'
down_revision: Union[str, None] = 'n2o3p4q5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create group table
    op.create_table(
        'group',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_group_owner_id'), 'group', ['owner_id'], unique=False)
    op.create_index(op.f('ix_group_name'), 'group', ['name'], unique=False)

    # Add group_id column to meeting table
    op.add_column('meeting', sa.Column('group_id', sa.Integer(), nullable=True))
    op.create_foreign_key(op.f('fk_meeting_group_id_group'), 'meeting', 'group', ['group_id'], ['id'])
    op.create_index(op.f('ix_meeting_group_id'), 'meeting', ['group_id'], unique=False)


def downgrade() -> None:
    # Drop meeting.group_id column first (FK dependency)
    op.drop_index(op.f('ix_meeting_group_id'), table_name='meeting')
    op.drop_constraint(op.f('fk_meeting_group_id_group'), 'meeting', type_='foreignkey')
    op.drop_column('meeting', 'group_id')

    # Drop group table
    op.drop_index(op.f('ix_group_name'), table_name='group')
    op.drop_index(op.f('ix_group_owner_id'), table_name='group')
    op.drop_table('group')
