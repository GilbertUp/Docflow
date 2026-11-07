"""scan_engine: record which scanner cleared each document version

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Versions filed before scanning existed were not scanned.
    op.add_column(
        "document_version",
        sa.Column("scan_engine", sa.String(length=20), nullable=False, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("document_version", "scan_engine")
