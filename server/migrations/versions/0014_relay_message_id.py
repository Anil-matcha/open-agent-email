"""relay-assigned message id per outbound recipient

Revision ID: 0014
Revises: 0013

Additive only. Amazon SES replaces the Message-ID of every SMTP transaction with its own
"<id@REGION.amazonses.com>"; replies reference that id. The id from SES's reply to DATA is stored
per recipient so inbound threading can find the original message. Existing rows stay NULL: their
ids were never captured and are not inferred.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

INDEX = "ix_message_recipients_relay_message_id"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "relay_message_id" not in {c["name"] for c in inspector.get_columns("message_recipients")}:
        op.add_column("message_recipients", sa.Column("relay_message_id", sa.String(128), nullable=True))
    if INDEX not in {i["name"] for i in inspector.get_indexes("message_recipients")}:
        op.create_index(INDEX, "message_recipients", ["relay_message_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if INDEX in {i["name"] for i in inspector.get_indexes("message_recipients")}:
        op.drop_index(INDEX, table_name="message_recipients")
    if "relay_message_id" in {c["name"] for c in inspector.get_columns("message_recipients")}:
        with op.batch_alter_table("message_recipients") as batch:
            batch.drop_column("relay_message_id")
