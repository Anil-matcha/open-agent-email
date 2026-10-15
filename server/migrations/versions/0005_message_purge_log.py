"""message purge log and trash-cancel marker

Adds the audit table for permanent message deletions and a marker on messages whose scheduled send was
canceled by trashing. Guarded so it is safe where startup `create_all` already created them.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "message_purge_log" not in inspector.get_table_names():
        op.create_table(
            "message_purge_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("agent_email", sa.String(length=255), nullable=False),
            sa.Column("message_id", sa.String(length=64), nullable=False),
            sa.Column("purged_by", sa.String(length=128), nullable=False),
            sa.Column("reason", sa.String(length=16), nullable=False),
            sa.Column("purged_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_message_purge_log_agent_email", "message_purge_log", ["agent_email"])
        op.create_index("ix_message_purge_log_message_id", "message_purge_log", ["message_id"])
        op.create_index("ix_message_purge_log_purged_at", "message_purge_log", ["purged_at"])
    if "canceled_by_trash" not in {c["name"] for c in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(sa.Column("canceled_by_trash", sa.Boolean(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "canceled_by_trash" in {c["name"] for c in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("canceled_by_trash")
    if "message_purge_log" in inspector.get_table_names():
        op.drop_table("message_purge_log")
