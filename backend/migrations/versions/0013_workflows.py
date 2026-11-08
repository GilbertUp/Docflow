"""workflows: published step lists, instances, steps, events and delegations

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLES = [
    "workflow",
    "workflow_definition",
    "workflow_instance",
    "workflow_step",
    "workflow_event",
    "delegation",
]


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
        "workflow",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_workflow_status")),
        _fk("workflow", "tenant_id", "tenant.id"),
        _fk("workflow", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_workflow_name"),
    )
    op.create_table(
        "workflow_definition",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("steps", jsonb, nullable=False),
        sa.Column("trigger", jsonb, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("workflow_definition", "tenant_id", "tenant.id"),
        _fk("workflow_definition", "workflow_id", "workflow.id"),
        _fk("workflow_definition", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_definition")),
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),
    )
    op.create_table(
        "workflow_instance",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_by", sa.Uuid(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'rejected', 'cancelled')",
            name=op.f("ck_workflow_instance_status"),
        ),
        _fk("workflow_instance", "tenant_id", "tenant.id"),
        _fk("workflow_instance", "definition_id", "workflow_definition.id"),
        _fk("workflow_instance", "document_id", "document.id", cascade=True),
        _fk("workflow_instance", "started_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_instance")),
    )
    op.create_index(
        "ix_workflow_instance_document", "workflow_instance", ["tenant_id", "document_id"]
    )
    op.create_table(
        "workflow_step",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("delegated_from_id", sa.Uuid(), nullable=True),
        sa.Column("due_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'approved', 'rejected', 'done', 'skipped')",
            name=op.f("ck_workflow_step_status"),
        ),
        sa.CheckConstraint("kind IN ('approval', 'task')", name=op.f("ck_workflow_step_kind")),
        _fk("workflow_step", "tenant_id", "tenant.id"),
        _fk("workflow_step", "instance_id", "workflow_instance.id", cascade=True),
        _fk("workflow_step", "assignee_id", "app_user.id"),
        _fk("workflow_step", "delegated_from_id", "app_user.id"),
        _fk("workflow_step", "task_id", "task.id"),
        _fk("workflow_step", "decided_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_step")),
        sa.UniqueConstraint("instance_id", "position", name="uq_workflow_step_position"),
    )
    op.create_index("ix_workflow_step_task", "workflow_step", ["tenant_id", "task_id"])
    op.create_table(
        "workflow_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("details", jsonb, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("workflow_event", "tenant_id", "tenant.id"),
        _fk("workflow_event", "instance_id", "workflow_instance.id", cascade=True),
        _fk("workflow_event", "actor_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_event")),
    )
    op.create_index(
        "ix_workflow_event_instance", "workflow_event", ["tenant_id", "instance_id", "created_at"]
    )
    op.create_table(
        "delegation",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=False),
        sa.Column("to_user_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("from_user_id <> to_user_id", name=op.f("ck_delegation_not_self")),
        sa.CheckConstraint("starts_at < ends_at", name=op.f("ck_delegation_order")),
        _fk("delegation", "tenant_id", "tenant.id"),
        _fk("delegation", "from_user_id", "app_user.id"),
        _fk("delegation", "to_user_id", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delegation")),
    )
    op.create_index("ix_delegation_from", "delegation", ["tenant_id", "from_user_id"])

    # A task made by a workflow is finished by deciding its step, not by editing it.
    op.add_column("task", sa.Column("workflow_step_id", sa.Uuid(), nullable=True))
    op.drop_constraint(op.f("ck_notification_kind"), "notification", type_="check")
    op.create_check_constraint(
        op.f("ck_notification_kind"),
        "notification",
        "kind IN ('task_assigned', 'task_done', 'mention', 'workflow_finished')",
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )
    op.execute(
        "INSERT INTO permission (code, kind, description) VALUES "
        "('workflows.manage', 'system', 'Create workflows and change their steps') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission = 'workflows.manage'")
    op.execute("DELETE FROM permission WHERE code = 'workflows.manage'")
    op.drop_constraint(op.f("ck_notification_kind"), "notification", type_="check")
    op.create_check_constraint(
        op.f("ck_notification_kind"),
        "notification",
        "kind IN ('task_assigned', 'task_done', 'mention')",
    )
    op.drop_column("task", "workflow_step_id")
    for table in reversed(TABLES):
        op.drop_table(table)
