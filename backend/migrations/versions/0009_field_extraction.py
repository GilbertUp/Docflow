"""field extraction: rules, suggested values with their source, and corrections

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

NEW_TABLES = ["classification_result", "extracted_field", "extraction_feedback"]


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
    )


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column(
        "document_type",
        sa.Column("keywords", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "document_type",
        sa.Column("auto_accept_threshold", sa.Float(), nullable=False, server_default="0.9"),
    )
    op.add_column(
        "document_type",
        sa.Column("review_threshold", sa.Float(), nullable=False, server_default="0.6"),
    )
    op.add_column(
        "index_field",
        sa.Column("extraction", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "classification_result",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_type_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("scores", jsonb, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_type.id"],
            name=op.f("fk_classification_result_document_type_id_document_type"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_classification_result_version_id_document_version"),
        ),
        _tenant_fk("classification_result"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_result")),
        sa.UniqueConstraint("version_id", name="uq_classification_version"),
    )
    op.create_table(
        "extracted_field",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("bbox", jsonb, nullable=False),
        sa.Column("validation_state", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "validation_state IN ('valid', 'invalid')",
            name=op.f("ck_extracted_field_validation_state"),
        ),
        sa.CheckConstraint(
            "state IN ('suggested', 'accepted', 'corrected', 'rejected')",
            name=op.f("ck_extracted_field_state"),
        ),
        sa.ForeignKeyConstraint(
            ["field_id"], ["index_field.id"], name=op.f("fk_extracted_field_field_id_index_field")
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["page.id"], name=op.f("fk_extracted_field_page_id_page")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_extracted_field_version_id_document_version"),
        ),
        _tenant_fk("extracted_field"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_field")),
        sa.UniqueConstraint("version_id", "field_id", name="uq_extracted_field"),
    )
    op.create_table(
        "extraction_feedback",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("extracted_field_id", sa.Uuid(), nullable=False),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["corrected_by"],
            ["app_user.id"],
            name=op.f("fk_extraction_feedback_corrected_by_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["extracted_field_id"],
            ["extracted_field.id"],
            name=op.f("fk_extraction_feedback_extracted_field_id_extracted_field"),
        ),
        _tenant_fk("extraction_feedback"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_feedback")),
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("extraction_feedback")
    op.drop_table("extracted_field")
    op.drop_table("classification_result")
    op.drop_column("index_field", "extraction")
    op.drop_column("document_type", "review_threshold")
    op.drop_column("document_type", "auto_accept_threshold")
    op.drop_column("document_type", "keywords")
