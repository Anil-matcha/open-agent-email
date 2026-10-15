"""agent soft delete

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_agents_deleted_at"), ["deleted_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agents_deleted_at"))
        batch_op.drop_column("deleted_at")
