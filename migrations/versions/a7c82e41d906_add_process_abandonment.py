"""Private terminal abandonment with no state rewrite or legacy backfill.

Revision ID: a7c82e41d906
Revises: f4d63b18a920
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c82e41d906"
down_revision: str | Sequence[str] | None = "f4d63b18a920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A logical marker avoids a circular FK with the immutable disposition's rollout FK.
    # All consumers check both directions; a missing receipt never reopens a rollout.
    op.add_column("process_rollouts", sa.Column("terminal_abandonment_id", sa.String(192)))
    op.create_index(
        "uq_process_terminal_abandonment",
        "process_rollouts",
        ["terminal_abandonment_id"],
        unique=True,
    )
    op.create_table(
        "process_abandonments",
        sa.Column("abandonment_id", sa.String(192), primary_key=True),
        sa.Column("rollout_id", sa.String(192), nullable=False, unique=True),
        sa.Column("recovery_id", sa.String(192), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recovery_id"], ["process_recoveries.recovery_id"], ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(sa.text("SELECT 1 FROM process_abandonments LIMIT 1")).first()
        or connection.execute(
            sa.text(
                "SELECT 1 FROM process_rollouts WHERE terminal_abandonment_id IS NOT NULL LIMIT 1"
            )
        ).first()
    ):
        raise RuntimeError("cannot downgrade a populated abandonment ledger")
    op.drop_table("process_abandonments")
    op.drop_index("uq_process_terminal_abandonment", table_name="process_rollouts")
    op.drop_column("process_rollouts", "terminal_abandonment_id")
