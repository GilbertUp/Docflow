"""signatures: approvals, signatures and stamps bound to a version

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TABLES = ["approval", "signature", "stamp", "stamp_application"]


def _fk(
    table: str, column: str, target: str, ondelete: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.partition('.')[0]}"),
        ondelete=ondelete,
    )


def _stamp() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "approval",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("version_hash", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=True),
        sa.Column("decider_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        sa.CheckConstraint("decision IN ('approve', 'reject')", name=op.f("ck_approval_decision")),
        _fk("approval", "tenant_id", "tenant.id"),
        _fk("approval", "document_id", "document.id", "CASCADE"),
        _fk("approval", "version_id", "document_version.id"),
        _fk("approval", "step_id", "workflow_step.id"),
        _fk("approval", "decider_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
    )
    op.create_index("ix_approval_document", "approval", ["tenant_id", "document_id", "created_at"])
    op.create_table(
        "signature",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("signer_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("statement", sa.String(length=500), nullable=False),
        sa.Column("bound_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("provider_reference", sa.String(length=200), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        sa.CheckConstraint(
            "kind IN ('internal_attestation', 'external_esignature')",
            name=op.f("ck_signature_kind"),
        ),
        sa.CheckConstraint("status IN ('valid', 'revoked')", name=op.f("ck_signature_status")),
        _fk("signature", "tenant_id", "tenant.id"),
        _fk("signature", "document_id", "document.id", "CASCADE"),
        _fk("signature", "version_id", "document_version.id"),
        _fk("signature", "signer_id", "app_user.id"),
        _fk("signature", "approval_id", "approval.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signature")),
    )
    op.create_index(
        "ix_signature_document", "signature", ["tenant_id", "document_id", "created_at"]
    )
    op.create_table(
        "stamp",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("text", sa.String(length=30), nullable=False),
        sa.Column("color", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_stamp_status")),
        sa.CheckConstraint(
            "color IN ('red', 'green', 'blue', 'black')", name=op.f("ck_stamp_color")
        ),
        _fk("stamp", "tenant_id", "tenant.id"),
        _fk("stamp", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stamp")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_stamp_name"),
    )
    op.create_table(
        "stamp_application",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("stamp_id", sa.Uuid(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("text", sa.String(length=30), nullable=False),
        sa.Column("color", sa.String(length=10), nullable=False),
        sa.Column("applied_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        _fk("stamp_application", "tenant_id", "tenant.id"),
        _fk("stamp_application", "document_id", "document.id", "CASCADE"),
        _fk("stamp_application", "version_id", "document_version.id"),
        _fk("stamp_application", "stamp_id", "stamp.id"),
        _fk("stamp_application", "applied_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stamp_application")),
    )
    op.create_index(
        "ix_stamp_application_version",
        "stamp_application",
        ["tenant_id", "version_id", "page"],
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
