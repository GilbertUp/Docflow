"""text extraction: pages, recognised text and where each version stands

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TEXT_STATES = "'not_started', 'pending', 'running', 'done', 'failed', 'unsupported'"


def upgrade() -> None:
    # Versions filed before text extraction existed have not been read.
    op.add_column(
        "document_version",
        sa.Column("text_state", sa.String(length=20), nullable=False, server_default="not_started"),
    )
    op.add_column("document_version", sa.Column("text_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_document_version_text_state"),
        "document_version",
        f"text_state IN ({TEXT_STATES})",
    )
    op.create_table(
        "page",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("has_native_text", sa.Boolean(), nullable=False),
        sa.Column("is_blank", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_page_tenant_id_tenant")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_page_version_id_document_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page")),
        sa.UniqueConstraint("version_id", "number", name="uq_page_number"),
    )
    op.create_table(
        "ocr_result",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("engine", sa.String(length=40), nullable=False),
        sa.Column("engine_version", sa.String(length=60), nullable=False),
        sa.Column("language", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("boxes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["page.id"], name=op.f("fk_ocr_result_page_id_page")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_ocr_result_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ocr_result")),
        sa.UniqueConstraint("page_id", "engine", "language", name="uq_ocr_result_run"),
    )
    op.create_index(
        "ix_ocr_result_page", "ocr_result", ["tenant_id", "page_id", "created_at"], unique=False
    )

    for table in ["page", "ocr_result"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    op.drop_index("ix_ocr_result_page", table_name="ocr_result")
    op.drop_table("ocr_result")
    op.drop_table("page")
    op.drop_constraint(op.f("ck_document_version_text_state"), "document_version", type_="check")
    op.drop_column("document_version", "text_error")
    op.drop_column("document_version", "text_state")
