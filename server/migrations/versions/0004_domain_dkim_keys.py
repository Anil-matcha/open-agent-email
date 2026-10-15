"""domain dkim keys

Adds the encrypted DKIM private key and last-check timestamp to registered domains.
Guarded so it is safe on databases where the columns already exist.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("domains")}


def upgrade() -> None:
    existing = _columns()
    with op.batch_alter_table("domains") as batch:
        if "dkim_private_key" not in existing:
            batch.add_column(sa.Column("dkim_private_key", sa.Text(), nullable=True))
        if "last_checked_at" not in existing:
            batch.add_column(sa.Column("last_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    existing = _columns()
    with op.batch_alter_table("domains") as batch:
        if "last_checked_at" in existing:
            batch.drop_column("last_checked_at")
        if "dkim_private_key" in existing:
            batch.drop_column("dkim_private_key")
