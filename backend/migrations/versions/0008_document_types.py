"""document types: fields in immutable schema versions, and the values documents hold

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "document_type",
    "index_schema_version",
    "index_field",
    "field_choice",
    "index_value",
]


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
    )


def upgrade() -> None:
    op.create_table(
        "document_type",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_schema_version_id", sa.Uuid(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_document_type_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_document_type_created_by_app_user")
        ),
        _tenant_fk("document_type"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_type")),
    )
    op.create_index(
        "uq_document_type_name",
        "document_type",
        ["tenant_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "index_schema_version",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_type_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_index_schema_version_created_by_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_type.id"],
            name=op.f("fk_index_schema_version_document_type_id_document_type"),
        ),
        _tenant_fk("index_schema_version"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_schema_version")),
        sa.UniqueConstraint("document_type_id", "version", name="uq_schema_version"),
    )
    op.create_foreign_key(
        "fk_document_type_current_schema",
        "document_type",
        "index_schema_version",
        ["current_schema_version_id"],
        ["id"],
    )
    op.create_table(
        "index_field",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_value", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "data_type IN ('text', 'number', 'date', 'boolean', 'choice')",
            name=op.f("ck_index_field_data_type"),
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["index_schema_version.id"],
            name=op.f("fk_index_field_schema_version_id_index_schema_version"),
        ),
        _tenant_fk("index_field"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_field")),
        sa.UniqueConstraint("schema_version_id", "key", name="uq_index_field_key"),
    )
    op.create_table(
        "field_choice",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"], ["index_field.id"], name=op.f("fk_field_choice_field_id_index_field")
        ),
        _tenant_fk("field_choice"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_choice")),
        sa.UniqueConstraint("field_id", "value", name="uq_field_choice_value"),
    )
    op.create_table(
        "index_value",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("validated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_index_value_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["field_id"], ["index_field.id"], name=op.f("fk_index_value_field_id_index_field")
        ),
        sa.ForeignKeyConstraint(
            ["validated_by"], ["app_user.id"], name=op.f("fk_index_value_validated_by_app_user")
        ),
        _tenant_fk("index_value"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_value")),
        sa.UniqueConstraint("document_id", "field_id", name="uq_index_value_field"),
    )
    op.create_index(
        "ix_index_value_field_text",
        "index_value",
        ["tenant_id", "field_id", "value_text"],
        unique=False,
    )

    op.add_column("document", sa.Column("document_type_id", sa.Uuid(), nullable=True))
    op.add_column("document", sa.Column("schema_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_document_document_type_id_document_type"),
        "document",
        "document_type",
        ["document_type_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_document_schema_version_id_index_schema_version"),
        "document",
        "index_schema_version",
        ["schema_version_id"],
        ["id"],
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )

    # A fresh install already has this row, because the first migration seeds the whole
    # catalogue from code. An existing install gets it here.
    op.execute(
        "INSERT INTO permission (code, kind, description) VALUES "
        "('document_types.manage', 'system', "
        "'Create document types and change the fields they have') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_document_schema_version_id_index_schema_version"), "document", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_document_document_type_id_document_type"), "document", type_="foreignkey"
    )
    op.drop_column("document", "schema_version_id")
    op.drop_column("document", "document_type_id")
    op.drop_index("ix_index_value_field_text", table_name="index_value")
    op.drop_table("index_value")
    op.drop_table("field_choice")
    op.drop_table("index_field")
    op.drop_constraint("fk_document_type_current_schema", "document_type", type_="foreignkey")
    op.drop_table("index_schema_version")
    op.drop_index("uq_document_type_name", table_name="document_type")
    op.drop_table("document_type")
    op.execute("DELETE FROM role_permission WHERE permission = 'document_types.manage'")
    op.execute("DELETE FROM permission WHERE code = 'document_types.manage'")
