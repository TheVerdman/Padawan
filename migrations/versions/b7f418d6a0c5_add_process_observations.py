"""Retain public observation bytes separately from privileged execution lineage.

Revision ID: b7f418d6a0c5
Revises: a84e61c39d20
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7f418d6a0c5"
down_revision: str | Sequence[str] | None = "a84e61c39d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_observations",
        sa.Column("observation_id", sa.String(192), nullable=False),
        sa.Column("rollout_id", sa.String(192), nullable=False),
        sa.Column("state_id", sa.String(192), nullable=False),
        sa.Column("execution_digest", sa.String(71), nullable=False),
        sa.Column("content_receipt_digest", sa.String(71), nullable=False),
        sa.Column("worker_id", sa.String(192), nullable=False),
        sa.Column("lease_token_digest", sa.String(71), nullable=False),
        sa.Column("authorization_sequence", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(71), nullable=False),
        sa.Column("observation_digest", sa.String(71), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["state_id"], ["process_states.state_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_executions.execution_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["content_receipt_digest"],
            ["process_content_admissions.record_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint("record_digest"),
        sa.UniqueConstraint(
            "rollout_id",
            "state_id",
            "worker_id",
            "lease_token_digest",
            "authorization_sequence",
            "policy_digest",
            name="uq_process_observation_context",
        ),
    )
    op.create_table(
        "process_observation_decisions",
        sa.Column("decision_id", sa.String(192), nullable=False),
        sa.Column("observation_id", sa.String(192), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["process_observations.observation_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_index(
        "ix_process_observation_decisions_observation_id",
        "process_observation_decisions",
        ["observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_process_observation_decisions_observation_id",
        table_name="process_observation_decisions",
    )
    op.drop_table("process_observation_decisions")
    op.drop_table("process_observations")
