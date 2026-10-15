"""review links and expiry

Adds the hashed review-link capability table and per-inbox review notification/expiry settings.
Guarded so it is safe on databases where startup `create_all` already created the new table.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "review_link_tokens" not in inspector.get_table_names():
        op.create_table(
            "review_link_tokens",
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("review_id", sa.String(length=64), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("reviewer_email", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["review_id"], ["review_items.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("token_hash"),
        )
        with op.batch_alter_table("review_link_tokens", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_review_link_tokens_expires_at"), ["expires_at"], unique=False)
            batch_op.create_index(batch_op.f("ix_review_link_tokens_review_id"), ["review_id"], unique=False)

    existing = {column["name"] for column in inspector.get_columns("agents")}
    wanted = {
        "review_notify_email": sa.Column("review_notify_email", sa.String(length=100), nullable=True),
        "review_expiry_hours": sa.Column("review_expiry_hours", sa.Integer(), nullable=True),
        "review_expiry_action": sa.Column("review_expiry_action", sa.String(length=16), nullable=True),
    }
    missing = [column for name, column in wanted.items() if name not in existing]
    if missing:
        with op.batch_alter_table("agents", schema=None) as batch_op:
            for column in missing:
                batch_op.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("review_expiry_action")
        batch_op.drop_column("review_expiry_hours")
        batch_op.drop_column("review_notify_email")

    with op.batch_alter_table("review_link_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_link_tokens_review_id"))
        batch_op.drop_index(batch_op.f("ix_review_link_tokens_expires_at"))

    op.drop_table("review_link_tokens")
