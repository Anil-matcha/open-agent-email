"""add inbox-scoped aliases to saved templates

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "alias" not in {c["name"] for c in inspector.get_columns("email_templates")}:
        with op.batch_alter_table("email_templates") as batch:
            batch.add_column(sa.Column("alias", sa.String(length=128), nullable=True))
    names = {i["name"] for i in sa.inspect(op.get_bind()).get_unique_constraints("email_templates")}
    if "uq_email_templates_agent_alias" not in names:
        with op.batch_alter_table("email_templates") as batch:
            batch.create_unique_constraint("uq_email_templates_agent_alias", ["agent_email", "alias"])


def downgrade() -> None:
    names = {i["name"] for i in sa.inspect(op.get_bind()).get_unique_constraints("email_templates")}
    if "uq_email_templates_agent_alias" in names:
        with op.batch_alter_table("email_templates") as batch:
            batch.drop_constraint("uq_email_templates_agent_alias", type_="unique")
    if "alias" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("email_templates")}:
        with op.batch_alter_table("email_templates") as batch:
            batch.drop_column("alias")
