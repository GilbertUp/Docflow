"""admin: the right to see usage and the job monitor, and to retry failed jobs

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission (code, kind, description) VALUES "
        "('admin.operate', 'system', 'See usage and the job monitor, and retry failed jobs') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission = 'admin.operate'")
    op.execute("DELETE FROM permission WHERE code = 'admin.operate'")
