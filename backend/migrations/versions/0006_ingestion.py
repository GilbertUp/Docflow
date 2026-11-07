"""ingestion: ZIP imports and what happened to each file

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_job",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("cabinet_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("archive_name", sa.String(length=300), nullable=False),
        sa.Column("archive_size", sa.BigInteger(), nullable=False),
        sa.Column("archive_key", sa.String(length=300), nullable=True),
        sa.Column("archive_wrapped_key", sa.LargeBinary(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_ingestion_job_status"),
        ),
        sa.CheckConstraint("source IN ('archive')", name=op.f("ck_ingestion_job_source")),
        sa.ForeignKeyConstraint(
            ["cabinet_id"], ["cabinet.id"], name=op.f("fk_ingestion_job_cabinet_id_cabinet")
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["folder.id"], name=op.f("fk_ingestion_job_folder_id_folder")
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"], ["app_user.id"], name=op.f("fk_ingestion_job_submitted_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_ingestion_job_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_job")),
    )
    op.create_index(
        "ix_ingestion_job_submitter",
        "ingestion_job",
        ["tenant_id", "submitted_by", "created_at"],
        unique=False,
    )
    op.create_table(
        "ingestion_item",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("duplicate", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('filed', 'failed')", name=op.f("ck_ingestion_item_status")),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_ingestion_item_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["ingestion_job.id"], name=op.f("fk_ingestion_item_job_id_ingestion_job")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_ingestion_item_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_item")),
        sa.UniqueConstraint("job_id", "path", name="uq_ingestion_item_path"),
    )

    for table in ["ingestion_job", "ingestion_item"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("ingestion_item")
    op.drop_index("ix_ingestion_job_submitter", table_name="ingestion_job")
    op.drop_table("ingestion_job")
