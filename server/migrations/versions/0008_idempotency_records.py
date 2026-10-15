"""add durable scoped idempotency receipts

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "idempotency_records" not in inspector.get_table_names():
        op.create_table(
            "idempotency_records",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("scope", sa.String(length=512), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("request_digest", sa.String(length=64), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("claim_token", sa.String(length=64), nullable=False),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.JSON(), nullable=True),
            sa.Column("response_encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("response_headers", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
        )
    if "ix_idempotency_records_expires_at" not in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("idempotency_records")
    }:
        op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "idempotency_records" in inspector.get_table_names():
        if "ix_idempotency_records_expires_at" in {
            index["name"] for index in inspector.get_indexes("idempotency_records")
        }:
            op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
        op.drop_table("idempotency_records")
