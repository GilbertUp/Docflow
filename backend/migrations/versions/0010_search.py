"""search: one searchable row per document, with details and text kept apart

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_entry",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "meta_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', meta)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "body_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', body)", persisted=True),
            nullable=False,
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_search_entry_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_search_entry_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_entry")),
        sa.UniqueConstraint("document_id", name="uq_search_entry_document"),
    )
    op.create_index("ix_search_entry_meta", "search_entry", ["meta_tsv"], postgresql_using="gin")
    op.create_index("ix_search_entry_body", "search_entry", ["body_tsv"], postgresql_using="gin")
    op.create_index("ix_search_entry_tenant", "search_entry", ["tenant_id"])
    op.execute("ALTER TABLE search_entry ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE search_entry FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON search_entry "
        "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("search_entry")
