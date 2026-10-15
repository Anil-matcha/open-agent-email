"""add caller-owned conversation identifier to messages

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "conversation_id" not in {c["name"] for c in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(sa.Column("conversation_id", sa.String(length=255), nullable=True))
    inspector = sa.inspect(op.get_bind())
    if "ix_messages_conversation_id" not in inspector.get_indexes("messages"):
        op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ix_messages_conversation_id" in inspector.get_indexes("messages"):
        op.drop_index("ix_messages_conversation_id", table_name="messages")
    if "conversation_id" in {c["name"] for c in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("conversation_id")
