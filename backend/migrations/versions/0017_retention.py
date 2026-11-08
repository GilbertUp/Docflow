"""retention: classes, document assignment, legal holds and the disposal record

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = [
    "retention_class",
    "document_retention",
    "legal_hold",
    "legal_hold_item",
    "disposal_event",
]


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
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "retention_class",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_retention_class_status")
        ),
        sa.CheckConstraint(
            "trigger IN ('created', 'last_version')", name=op.f("ck_retention_class_trigger")
        ),
        sa.CheckConstraint("duration_days >= 1", name=op.f("ck_retention_class_duration")),
        _fk("retention_class", "tenant_id", "tenant.id"),
        _fk("retention_class", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_class")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_retention_class_name"),
    )
    op.create_table(
        "document_retention",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        _fk("document_retention", "tenant_id", "tenant.id"),
        _fk("document_retention", "document_id", "document.id", "CASCADE"),
        _fk("document_retention", "class_id", "retention_class.id"),
        _fk("document_retention", "assigned_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_retention")),
        sa.UniqueConstraint("document_id", name="uq_document_retention"),
    )
    op.create_table(
        "legal_hold",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        sa.CheckConstraint("status IN ('active', 'released')", name=op.f("ck_legal_hold_status")),
        _fk("legal_hold", "tenant_id", "tenant.id"),
        _fk("legal_hold", "created_by", "app_user.id"),
        _fk("legal_hold", "released_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_hold")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_legal_hold_name"),
    )
    op.create_table(
        "legal_hold_item",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("hold_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        _fk("legal_hold_item", "tenant_id", "tenant.id"),
        _fk("legal_hold_item", "hold_id", "legal_hold.id"),
        _fk("legal_hold_item", "document_id", "document.id", "CASCADE"),
        _fk("legal_hold_item", "added_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_hold_item")),
        sa.UniqueConstraint("hold_id", "document_id", name="uq_legal_hold_item"),
    )
    op.create_index("ix_legal_hold_item_document", "legal_hold_item", ["tenant_id", "document_id"])
    op.create_table(
        "disposal_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("retention_class", sa.String(length=120), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_hashes", jsonb, nullable=False),
        sa.Column("approver_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        _stamp(),
        _fk("disposal_event", "tenant_id", "tenant.id"),
        _fk("disposal_event", "approver_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disposal_event")),
    )
    op.create_index("ix_disposal_event_tenant", "disposal_event", ["tenant_id", "created_at"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )
    op.execute(
        "INSERT INTO permission (code, kind, description) VALUES "
        "('retention.manage', 'system', 'Set retention classes and legal holds') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission = 'retention.manage'")
    op.execute("DELETE FROM permission WHERE code = 'retention.manage'")
    for table in reversed(TABLES):
        op.drop_table(table)
