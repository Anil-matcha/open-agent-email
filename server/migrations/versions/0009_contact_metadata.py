"""add bounded flat contact metadata

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "metadata" not in {column["name"] for column in inspector.get_columns("contacts")}:
        op.add_column("contacts", sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "metadata" in {column["name"] for column in inspector.get_columns("contacts")}:
        op.drop_column("contacts", "metadata")
