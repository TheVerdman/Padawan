"""Finite task ownership before rollout creation; no historical enrollment.

Revision ID: b819f0a35d62
Revises: a7c82e41d906
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b819f0a35d62"
down_revision: str | Sequence[str] | None = "a7c82e41d906"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_task_plans",
        sa.Column("authorization_digest", sa.String(71), primary_key=True),
        sa.Column("plan_id", sa.String(192), nullable=False, unique=True),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
    )
    op.add_column("process_rollouts", sa.Column("task_plan_digest", sa.String(71)))


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(sa.text("SELECT 1 FROM process_task_plans LIMIT 1")).first()
        or connection.execute(
            sa.text("SELECT 1 FROM process_rollouts WHERE task_plan_digest IS NOT NULL LIMIT 1")
        ).first()
    ):
        raise RuntimeError("cannot downgrade populated task ownership")
    op.drop_column("process_rollouts", "task_plan_digest")
    op.drop_table("process_task_plans")
