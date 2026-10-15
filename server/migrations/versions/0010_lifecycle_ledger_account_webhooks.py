"""message lifecycle ledger and account-level webhook endpoints

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "message_lifecycle_entries" not in inspector.get_table_names():
        op.create_table(
            "message_lifecycle_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("message_id", sa.String(64), nullable=False),
            sa.Column("code", sa.String(32), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_message_lifecycle_entries_message_id", "message_lifecycle_entries", ["message_id"])
    columns = {column["name"]: column for column in inspector.get_columns("webhook_endpoints")}
    if "inbox_emails" not in columns:
        op.add_column("webhook_endpoints", sa.Column("inbox_emails", sa.JSON(), nullable=True))
    if not columns["agent_email"]["nullable"]:
        with op.batch_alter_table("webhook_endpoints") as batch:
            batch.alter_column("agent_email", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # Account-level endpoints have no inbox, so they cannot survive the stricter column.
    op.execute(sa.text("DELETE FROM webhook_endpoints WHERE agent_email IS NULL"))
    with op.batch_alter_table("webhook_endpoints") as batch:
        batch.alter_column("agent_email", existing_type=sa.String(255), nullable=False)
        if "inbox_emails" in {column["name"] for column in inspector.get_columns("webhook_endpoints")}:
            batch.drop_column("inbox_emails")
    if "message_lifecycle_entries" in inspector.get_table_names():
        op.drop_index("ix_message_lifecycle_entries_message_id", table_name="message_lifecycle_entries")
        op.drop_table("message_lifecycle_entries")
