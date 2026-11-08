"""notifications: SLA policy, email preferences and delivery tracking

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ["sla_policy", "notification_preference", "email_message"]
OLD_KINDS = "kind IN ('task_assigned', 'task_done', 'mention', 'workflow_finished')"
NEW_KINDS = (
    "kind IN ('task_assigned', 'task_done', 'mention', 'workflow_finished', "
    "'task_reminder', 'task_overdue', 'task_escalated')"
)


def _fk(
    table: str, column: str, target: str, ondelete: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.partition('.')[0]}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.create_table(
        "sla_policy",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("reminder_before_hours", sa.Integer(), nullable=False),
        sa.Column("escalate_after_hours", sa.Integer(), nullable=True),
        sa.Column("escalate_to_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("sla_policy", "tenant_id", "tenant.id"),
        _fk("sla_policy", "escalate_to_user_id", "app_user.id"),
        _fk("sla_policy", "updated_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sla_policy")),
        sa.UniqueConstraint("tenant_id", name="uq_sla_policy_tenant"),
    )
    op.create_table(
        "notification_preference",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("notification_preference", "tenant_id", "tenant.id"),
        _fk("notification_preference", "user_id", "app_user.id", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_preference")),
        sa.UniqueConstraint("user_id", "kind", name="uq_notification_preference"),
    )
    op.create_table(
        "email_message",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed')", name=op.f("ck_email_message_status")
        ),
        _fk("email_message", "tenant_id", "tenant.id"),
        _fk("email_message", "user_id", "app_user.id", "CASCADE"),
        _fk("email_message", "notification_id", "notification.id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_message")),
    )
    op.create_index(
        "ix_email_message_user", "email_message", ["tenant_id", "user_id", "created_at"]
    )

    op.drop_constraint(op.f("ck_notification_kind"), "notification", type_="check")
    op.create_check_constraint(op.f("ck_notification_kind"), "notification", NEW_KINDS)

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM notification WHERE kind IN ('task_reminder', 'task_overdue', 'task_escalated')"
    )
    op.drop_constraint(op.f("ck_notification_kind"), "notification", type_="check")
    op.create_check_constraint(op.f("ck_notification_kind"), "notification", OLD_KINDS)
    for table in reversed(TABLES):
        op.drop_table(table)
