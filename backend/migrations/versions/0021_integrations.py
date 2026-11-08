"""integrations: webhook subscriptions and deliveries, and idempotency records

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TENANT_TABLES = ["webhook_subscription", "webhook_delivery"]


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
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "webhook_subscription",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("events", jsonb, nullable=False),
        sa.Column("secret_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_webhook_subscription_status")
        ),
        _fk("webhook_subscription", "tenant_id", "tenant.id"),
        _fk("webhook_subscription", "created_by", "app_user.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_subscription")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_webhook_subscription_name"),
    )
    op.create_table(
        "webhook_delivery",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redelivery_of", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')", name=op.f("ck_webhook_delivery_status")
        ),
        _fk("webhook_delivery", "tenant_id", "tenant.id"),
        _fk("webhook_delivery", "subscription_id", "webhook_subscription.id", "CASCADE"),
        _fk("webhook_delivery", "redelivery_of", "webhook_delivery.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_delivery")),
    )
    op.create_index(
        "ix_webhook_delivery_subscription",
        "webhook_delivery",
        ["tenant_id", "subscription_id", "created_at"],
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
        )

    op.create_table(
        "idempotency_record",
        sa.Column("caller_hash", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'complete')", name=op.f("ck_idempotency_record_state")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_record")),
        sa.UniqueConstraint("caller_hash", "key", name="uq_idempotency_caller_key"),
    )
    op.create_index("ix_idempotency_created", "idempotency_record", ["created_at"])
    # It holds stored answers across organizations, so only the platform role may touch it.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docuflow_app') THEN "
        "REVOKE ALL ON idempotency_record FROM docuflow_app; END IF; END $$"
    )


def downgrade() -> None:
    op.drop_table("idempotency_record")
    op.drop_table("webhook_delivery")
    op.drop_table("webhook_subscription")
