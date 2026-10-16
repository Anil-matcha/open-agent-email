"""tenant_id on every tenant-owned table

Revision ID: 0012
Revises: 0011

Existing rows belong to the ``default`` tenant. Inbox addresses and domains stay globally unique;
``server_settings``, ``smtp_configs`` and ``provider_feedback_receipts`` stay global.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

DEFAULT_TENANT = "default"
TABLES = (
    "agents", "contacts", "contact_engagements", "contact_consent_events", "email_templates",
    "contact_import_batches", "recipient_suppressions", "idempotency_records", "unsubscribe_tokens",
    "review_link_tokens", "messages", "message_purge_log", "message_attachments", "outbound_jobs",
    "outbound_retry_events", "webhook_endpoints", "webhook_deliveries", "review_items", "api_keys",
    "security_events", "domains", "message_lifecycle_entries",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in TABLES:
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            op.add_column(table, sa.Column(
                "tenant_id", sa.String(64), nullable=False, server_default=DEFAULT_TENANT))
        index = f"ix_{table}_tenant_id"
        if index not in {i["name"] for i in inspector.get_indexes(table)}:
            op.create_index(index, table, ["tenant_id"])
        if bind.dialect.name != "sqlite":
            # New rows must name their tenant; the default only existed to backfill.
            op.alter_column(table, "tenant_id", existing_type=sa.String(64), server_default=None)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        if f"ix_{table}_tenant_id" in {i["name"] for i in inspector.get_indexes(table)}:
            op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        if "tenant_id" in {column["name"] for column in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("tenant_id")
