"""documents: stored binaries and document versions

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "binary_object",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=120), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("ref_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_binary_object_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_binary_object")),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_binary_object_content"),
    )
    op.create_table(
        "document_version",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("binary_object_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=300), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('quarantined', 'released', 'rejected')",
            name=op.f("ck_document_version_state"),
        ),
        sa.ForeignKeyConstraint(
            ["binary_object_id"],
            ["binary_object.id"],
            name=op.f("fk_document_version_binary_object_id_binary_object"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_document_version_created_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_document_version_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_document_version_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_version")),
        sa.UniqueConstraint("document_id", "number", name="uq_document_version_number"),
    )
    op.create_index(
        "ix_document_version_binary",
        "document_version",
        ["tenant_id", "binary_object_id"],
        unique=False,
    )
    op.add_column("document", sa.Column("current_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_document_current_version_id_document_version",
        "document",
        "document_version",
        ["current_version_id"],
        ["id"],
    )

    for table in ["binary_object", "document_version"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_document_current_version_id_document_version", "document", type_="foreignkey"
    )
    op.drop_column("document", "current_version_id")
    op.drop_index("ix_document_version_binary", table_name="document_version")
    op.drop_table("document_version")
    op.drop_table("binary_object")
