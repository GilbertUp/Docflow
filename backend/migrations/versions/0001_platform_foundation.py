"""Platform foundation: tenants, jobs, outbox, timers and audit, under row-level security.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "system_config",
    "processing_job",
    "outbox",
    "timer",
    "audit_event",
    "audit_checkpoint",
)


def _timestamp(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _tenant_fk() -> sa.Column:
    return sa.Column(
        "tenant_id",
        sa.Uuid(),
        sa.ForeignKey("tenant.id", name=None),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("permission_epoch", sa.BigInteger(), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="status"),
    )
    op.create_table(
        "system_config",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint("tenant_id", "key"),
    )
    op.create_table(
        "processing_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("queue", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("correlation_id", sa.String(64)),
        _timestamp("next_run_at"),
        _timestamp("dispatched_at", nullable=True),
        _timestamp("heartbeat_at", nullable=True),
        _timestamp("finished_at", nullable=True),
        _timestamp("created_at"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'dead')", name="status"
        ),
    )
    op.create_index(
        "ix_processing_job_tenant_status",
        "processing_job",
        ["tenant_id", "status", "next_run_at"],
    )
    op.create_index("ix_processing_job_running", "processing_job", ["status", "heartbeat_at"])
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("topic", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _timestamp("published_at", nullable=True),
        _timestamp("created_at"),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "timer",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        _timestamp("fire_at"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint("status IN ('pending', 'fired', 'cancelled')", name="status"),
    )
    op.create_index(
        "ix_timer_due", "timer", ["fire_at"], postgresql_where=sa.text("status = 'pending'")
    )
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        _timestamp("occurred_at"),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("object_type", sa.String(80)),
        sa.Column("object_id", sa.String(64)),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("device", sa.String(255)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "seq"),
    )
    op.create_index(
        "ix_audit_event_object", "audit_event", ["tenant_id", "object_type", "object_id"]
    )
    op.create_index("ix_audit_event_actor", "audit_event", ["tenant_id", "actor_id", "occurred_at"])
    op.create_table(
        "audit_checkpoint",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_fk(),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("head_hash", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint("tenant_id", "seq"),
    )

    # A missing or empty setting yields NULL, which matches no rows: isolation fails closed.
    op.execute(
        """
        CREATE FUNCTION current_tenant() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE AS
        $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )
    op.execute("ALTER TABLE tenant ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant "
        "USING (id = current_tenant()) WITH CHECK (id = current_tenant())"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )

    # Tenants are created and removed only by the platform role.
    op.execute("REVOKE INSERT, DELETE ON tenant FROM docuflow_app")

    # Audit is append-only for both runtime roles, and a trigger stops owners too.
    op.execute("REVOKE UPDATE, DELETE ON audit_event FROM docuflow_app, docuflow_platform")
    op.execute(
        """
        CREATE FUNCTION reject_audit_change() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'audit_event is append-only'; END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_change()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_change()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE audit_checkpoint")
    op.execute("DROP TABLE audit_event")
    op.execute("DROP FUNCTION reject_audit_change")
    op.execute("DROP TABLE timer")
    op.execute("DROP TABLE outbox")
    op.execute("DROP TABLE processing_job")
    op.execute("DROP TABLE system_config")
    op.execute("DROP TABLE tenant")
    op.execute("DROP FUNCTION current_tenant")
