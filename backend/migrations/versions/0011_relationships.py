"""relationships: typed links between documents

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_relationship",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("from_document_id", sa.Uuid(), nullable=False),
        sa.Column("to_document_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_document_id <> to_document_id",
            name=op.f("ck_document_relationship_not_self"),
        ),
        sa.CheckConstraint(
            "relation_type IN ('related', 'supersedes', 'responds_to', 'attached_to')",
            name=op.f("ck_document_relationship_relation_type"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_document_relationship_created_by_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["from_document_id"],
            ["document.id"],
            name=op.f("fk_document_relationship_from_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_document_id"],
            ["document.id"],
            name=op.f("fk_document_relationship_to_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_document_relationship_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_relationship")),
        sa.UniqueConstraint(
            "from_document_id", "to_document_id", "relation_type", name="uq_document_relationship"
        ),
    )
    op.create_index(
        "ix_document_relationship_to", "document_relationship", ["tenant_id", "to_document_id"]
    )
    op.execute("ALTER TABLE document_relationship ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_relationship FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON document_relationship "
        "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("document_relationship")
