"""Private reviewed recovery history, with no permissive legacy backfill.

Revision ID: f4d63b18a920
Revises: e1c87a63d942
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4d63b18a920"
down_revision: str | Sequence[str] | None = "e1c87a63d942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_recoveries",
        sa.Column("recovery_id", sa.String(192), primary_key=True),
        sa.Column("rollout_id", sa.String(192), nullable=False),
        sa.Column("execution_digest", sa.String(71), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_executions.execution_digest"], ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM process_recoveries LIMIT 1")).first():
        raise RuntimeError("cannot downgrade a populated recovery ledger")
    op.drop_table("process_recoveries")
