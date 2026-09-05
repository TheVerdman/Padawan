"""Retain exact process generation inputs before dispatch; no legacy backfill.

Revision ID: d8b541c9e2a6
Revises: c3e746d2a9f1
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8b541c9e2a6"
down_revision: str | Sequence[str] | None = "c3e746d2a9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "process_worker_invocations", sa.Column("workload_digest", sa.String(71), nullable=True)
    )
    op.create_table(
        "process_generation_workloads",
        sa.Column("invocation_id", sa.String(192), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("decision_id", sa.String(192), nullable=False),
        sa.Column("observation_id", sa.String(192), nullable=False),
        sa.Column("prepared_artifact_id", sa.String(96), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"], ["process_worker_invocations.invocation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["process_observations.observation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["prepared_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
    )


def downgrade() -> None:
    op.drop_table("process_generation_workloads")
    op.drop_column("process_worker_invocations", "workload_digest")
