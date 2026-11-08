"""assistant: searchable passages, per-organization settings, and the record of questions

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TABLES = ["search_chunk", "assistant_setting", "assistant_query"]


def _fk(
    table: str, column: str, target: str, ondelete: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.partition('.')[0]}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "search_chunk",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        _fk("search_chunk", "tenant_id", "tenant.id"),
        _fk("search_chunk", "document_id", "document.id", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_chunk")),
    )
    op.create_index("ix_search_chunk_tsv", "search_chunk", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_search_chunk_document", "search_chunk", ["tenant_id", "document_id", "ordinal"]
    )
    op.create_table(
        "assistant_setting",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("generation_enabled", sa.Boolean(), nullable=False),
        sa.Column("excluded_cabinet_ids", jsonb, nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("assistant_setting", "tenant_id", "tenant.id"),
        _fk("assistant_setting", "updated_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_setting")),
        sa.UniqueConstraint("tenant_id", name="uq_assistant_setting_tenant"),
    )
    op.create_table(
        "assistant_query",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.String(length=500), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", jsonb, nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("best_coverage", sa.Float(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('generated', 'extracts', 'insufficient_evidence')",
            name=op.f("ck_assistant_query_mode"),
        ),
        _fk("assistant_query", "tenant_id", "tenant.id"),
        _fk("assistant_query", "user_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_query")),
    )
    op.create_index(
        "ix_assistant_query_user", "assistant_query", ["tenant_id", "user_id", "created_at"]
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
