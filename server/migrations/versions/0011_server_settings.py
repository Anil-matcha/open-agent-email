"""server settings saved from the dashboard

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "server_settings" not in inspector.get_table_names():
        op.create_table(
            "server_settings",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by", sa.String(64), nullable=False, server_default="admin"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "server_settings" in inspector.get_table_names():
        op.drop_table("server_settings")
