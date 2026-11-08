"""compression: smaller copies of a version's file, with their measurements

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "derivative",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("binary_object_id", sa.Uuid(), nullable=True),
        sa.Column("mime", sa.String(length=120), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size", sa.BigInteger(), nullable=False),
        sa.Column("derivative_size", sa.BigInteger(), nullable=False),
        sa.Column("producer", sa.String(length=40), nullable=False),
        sa.Column("producer_version", sa.Integer(), nullable=False),
        sa.Column("params", jsonb, nullable=False),
        sa.Column("metrics", jsonb, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted', 'rejected', 'no_benefit')", name=op.f("ck_derivative_status")
        ),
        sa.CheckConstraint("kind IN ('compressed')", name=op.f("ck_derivative_kind")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_derivative_tenant_id_tenant")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_derivative_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["binary_object_id"],
            ["binary_object.id"],
            name=op.f("fk_derivative_binary_object_id_binary_object"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_derivative_created_by_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_derivative")),
    )
    op.create_index(
        "ix_derivative_version", "derivative", ["tenant_id", "version_id", "created_at"]
    )
    op.execute("ALTER TABLE derivative ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE derivative FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON derivative "
        "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("derivative")
