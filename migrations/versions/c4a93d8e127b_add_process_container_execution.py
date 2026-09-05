"""Retain exact local container intent and private execution evidence; no backfill.

Revision ID: c4a93d8e127b
Revises: f6a8c2d4e913
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a93d8e127b"
down_revision: str | Sequence[str] | None = "f6a8c2d4e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_container_workloads",
        sa.Column("invocation_id", sa.String(192), nullable=False),
        sa.Column("decision_id", sa.String(192), nullable=False),
        sa.Column("rollout_id", sa.String(192), nullable=False),
        sa.Column("observation_id", sa.String(192), nullable=False),
        sa.Column("container_name", sa.String(192), nullable=False),
        sa.Column("input_artifact_id", sa.String(96), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["process_observations.observation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["input_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
        sa.UniqueConstraint("decision_id"),
        sa.UniqueConstraint("container_name"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_table(
        "process_container_heads",
        sa.Column("invocation_id", sa.String(192), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"], ["process_container_workloads.invocation_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
        sa.CheckConstraint(
            "status IN ('prepared', 'starting', 'finished', 'unknown')",
            name="ck_process_container_status",
        ),
    )
    op.create_table(
        "process_container_receipts",
        sa.Column("invocation_id", sa.String(192), nullable=False),
        sa.Column("evidence_artifact_id", sa.String(96), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"], ["process_container_workloads.invocation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
        sa.UniqueConstraint("record_digest"),
    )


def downgrade() -> None:
    op.drop_table("process_container_receipts")
    op.drop_table("process_container_heads")
    op.drop_table("process_container_workloads")
