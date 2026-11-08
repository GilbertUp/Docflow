"""collaboration: comments, mentions, tasks and in-app notifications

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TABLES = ["comment", "mention", "task", "notification"]


def _fk(table: str, column: str, target: str, *, cascade: bool = False) -> sa.ForeignKeyConstraint:
    table_name, _, _ = target.partition(".")
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{table_name}"),
        ondelete="CASCADE" if cascade else None,
    )


def upgrade() -> None:
    op.create_table(
        "comment",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("comment", "tenant_id", "tenant.id"),
        _fk("comment", "document_id", "document.id", cascade=True),
        _fk("comment", "author_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment")),
    )
    op.create_index("ix_comment_document", "comment", ["tenant_id", "document_id", "created_at"])
    op.create_table(
        "mention",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _fk("mention", "tenant_id", "tenant.id"),
        _fk("mention", "comment_id", "comment.id", cascade=True),
        _fk("mention", "user_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mention")),
        sa.UniqueConstraint("comment_id", "user_id", name="uq_mention"),
    )
    op.create_table(
        "task",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Uuid(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'done', 'cancelled')", name=op.f("ck_task_status")),
        sa.CheckConstraint("priority IN ('low', 'normal', 'high')", name=op.f("ck_task_priority")),
        _fk("task", "tenant_id", "tenant.id"),
        _fk("task", "document_id", "document.id", cascade=True),
        _fk("task", "assignee_id", "app_user.id"),
        _fk("task", "created_by", "app_user.id"),
        _fk("task", "completed_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task")),
    )
    op.create_index("ix_task_document", "task", ["tenant_id", "document_id"])
    op.create_index("ix_task_assignee", "task", ["tenant_id", "assignee_id", "status"])
    op.create_table(
        "notification",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('task_assigned', 'task_done', 'mention')", name=op.f("ck_notification_kind")
        ),
        _fk("notification", "tenant_id", "tenant.id"),
        _fk("notification", "user_id", "app_user.id", cascade=True),
        _fk("notification", "document_id", "document.id", cascade=True),
        _fk("notification", "actor_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
    )
    op.create_index(
        "ix_notification_user",
        "notification",
        ["tenant_id", "user_id", "read_at", "created_at"],
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
