"""visible To/Cc/Bcc/Reply-To lists and per-recipient outbound delivery state

Revision ID: 0013
Revises: 0012

Additive only. Existing outbound messages are backfilled with ``to_recipients = [recipient]`` and one
``message_recipients`` row whose state mirrors the message status. Existing inbound messages keep
NULL header lists: their visible To/Cc were never stored and are not inferred.
"""
import datetime

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

NEW_COLUMNS = ("to_recipients", "cc_recipients", "bcc_recipients", "reply_to")
# Message states that are also final per-recipient states; anything else was never submitted.
FINAL_STATES = ("accepted_by_relay", "delivered", "failed", "blocked", "delivery_unknown")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("messages")}
    for name in NEW_COLUMNS:
        if name not in columns:
            op.add_column("messages", sa.Column(name, sa.JSON(), nullable=True))

    if "message_recipients" not in inspector.get_table_names():
        op.create_table(
            "message_recipients",
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("message_id", sa.String(64), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("address", sa.String(255), nullable=False),
            sa.Column("kind", sa.String(8), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("provider_delivery_status", sa.String(32), nullable=True),
            sa.Column("provider_delivery_error", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("message_id", "address", name="uq_message_recipients_message_address"),
        )
        op.create_index("ix_message_recipients_message_id", "message_recipients", ["message_id"])
        op.create_index("ix_message_recipients_tenant_id", "message_recipients", ["tenant_id"])

    messages = sa.table(
        "messages", sa.column("id"), sa.column("tenant_id"), sa.column("direction"), sa.column("recipient"),
        sa.column("status"), sa.column("delivery_error"), sa.column("provider_delivery_status"),
        sa.column("provider_delivery_error"), sa.column("created_at"), sa.column("to_recipients", sa.JSON()),
    )
    recipients = sa.table(
        "message_recipients", sa.column("tenant_id"), sa.column("message_id"), sa.column("address"),
        sa.column("kind"), sa.column("status"), sa.column("detail"), sa.column("attempts"),
        sa.column("provider_delivery_status"), sa.column("provider_delivery_error"), sa.column("updated_at"),
    )
    existing = {row[0] for row in bind.execute(sa.select(recipients.c.message_id))}
    rows = bind.execute(sa.select(
        messages.c.id, messages.c.tenant_id, messages.c.recipient, messages.c.status, messages.c.delivery_error,
        messages.c.provider_delivery_status, messages.c.provider_delivery_error, messages.c.created_at,
    ).where(messages.c.direction == "outbound", messages.c.to_recipients.is_(None))).all()
    # Row-by-row backfill suits small mailboxes; use set-based SQL for very large archives.
    for row in rows:
        address = (row.recipient or "").strip().lower()
        bind.execute(messages.update().where(messages.c.id == row.id).values(to_recipients=[address]))
        if row.id in existing or not address:
            continue
        final = row.status in FINAL_STATES
        bind.execute(recipients.insert().values(
            tenant_id=row.tenant_id, message_id=row.id, address=address, kind="to",
            status=row.status if final else "pending", detail=row.delivery_error if final else None,
            attempts=1 if final and row.status != "blocked" else 0,
            provider_delivery_status=row.provider_delivery_status,
            provider_delivery_error=row.provider_delivery_error,
            updated_at=row.created_at or datetime.datetime.utcnow(),
        ))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "message_recipients" in inspector.get_table_names():
        op.drop_index("ix_message_recipients_tenant_id", table_name="message_recipients")
        op.drop_index("ix_message_recipients_message_id", table_name="message_recipients")
        op.drop_table("message_recipients")
    columns = {c["name"] for c in inspector.get_columns("messages")}
    with op.batch_alter_table("messages") as batch:
        for name in NEW_COLUMNS:
            if name in columns:
                batch.drop_column(name)
