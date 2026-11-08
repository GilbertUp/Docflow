"""forms: definitions, published versions and submissions

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLES = ["form", "form_version", "form_submission"]


def _fk(table: str, column: str, target: str, *, cascade: bool = False) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.partition('.')[0]}"),
        ondelete="CASCADE" if cascade else None,
    )


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "form",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_form_status")),
        _fk("form", "tenant_id", "tenant.id"),
        _fk("form", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_form")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_form_name"),
    )
    op.create_table(
        "form_version",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("form_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fields", jsonb, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("form_version", "tenant_id", "tenant.id"),
        _fk("form_version", "form_id", "form.id"),
        _fk("form_version", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_form_version")),
        sa.UniqueConstraint("form_id", "version", name="uq_form_version"),
    )
    op.create_table(
        "form_submission",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("form_version_id", sa.Uuid(), nullable=False),
        sa.Column("submitter_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("data", jsonb, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("form_submission", "tenant_id", "tenant.id"),
        _fk("form_submission", "form_version_id", "form_version.id"),
        _fk("form_submission", "submitter_id", "app_user.id"),
        _fk("form_submission", "document_id", "document.id", cascade=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_form_submission")),
    )
    op.create_index(
        "ix_form_submission_form",
        "form_submission",
        ["tenant_id", "form_version_id", "created_at"],
    )
    op.create_index(
        "ix_form_submission_submitter",
        "form_submission",
        ["tenant_id", "submitter_id", "created_at"],
    )
    op.create_index("ix_form_submission_document", "form_submission", ["tenant_id", "document_id"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )
    op.execute(
        "INSERT INTO permission (code, kind, description) VALUES "
        "('forms.manage', 'system', 'Create forms and see every submission') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission = 'forms.manage'")
    op.execute("DELETE FROM permission WHERE code = 'forms.manage'")
    for table in reversed(TABLES):
        op.drop_table(table)
