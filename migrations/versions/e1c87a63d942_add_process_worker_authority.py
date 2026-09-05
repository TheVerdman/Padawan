"""Explicit worker enrollment, issuance and one-action request lineage; no backfill.

Revision ID: e1c87a63d942
Revises: c4a93d8e127b
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1c87a63d942"
down_revision: str | Sequence[str] | None = "c4a93d8e127b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_worker_scopes",
        sa.Column("execution_digest", sa.String(71), primary_key=True),
        sa.Column("authorization_digest", sa.String(71), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_executions.execution_digest"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "process_worker_registrations",
        sa.Column("worker_id", sa.String(192), primary_key=True),
        sa.Column("execution_digest", sa.String(71), nullable=False),
        sa.Column("role_id", sa.String(192), nullable=False),
        sa.Column("credential_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_worker_scopes.execution_digest"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "process_worker_revocations",
        sa.Column("worker_id", sa.String(192), primary_key=True),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["process_worker_registrations.worker_id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "process_worker_heads",
        sa.Column("worker_id", sa.String(192), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["process_worker_registrations.worker_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_process_worker_status"),
    )
    op.create_table(
        "process_worker_lease_assignments",
        sa.Column("assignment_id", sa.String(192), primary_key=True),
        sa.Column("worker_id", sa.String(192), nullable=False),
        sa.Column("rollout_id", sa.String(192), nullable=False),
        sa.Column("lease_token_digest", sa.String(71), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["process_worker_registrations.worker_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("rollout_id", "lease_token_digest"),
    )
    op.create_table(
        "process_worker_decision_bindings",
        sa.Column("decision_id", sa.String(192), primary_key=True),
        sa.Column("assignment_id", sa.String(192), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["process_worker_lease_assignments.assignment_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "process_worker_requests",
        sa.Column("receipt_id", sa.String(192), primary_key=True),
        sa.Column("worker_id", sa.String(192), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("assignment_id", sa.String(192), nullable=False),
        sa.Column("observation_id", sa.String(192), nullable=False),
        sa.Column("decision_id", sa.String(192), nullable=True, unique=True),
        sa.Column("record_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["process_worker_registrations.worker_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["process_worker_lease_assignments.assignment_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["process_observations.observation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("worker_id", "request_id"),
    )


def downgrade() -> None:
    # Never erase an enrolled ledger to regain the legacy unauthenticated path.
    if op.get_bind().execute(sa.text("SELECT 1 FROM process_worker_scopes LIMIT 1")).first():
        raise RuntimeError("cannot downgrade a populated worker authority ledger")
    for name in (
        "process_worker_requests",
        "process_worker_decision_bindings",
        "process_worker_lease_assignments",
        "process_worker_heads",
        "process_worker_revocations",
        "process_worker_registrations",
        "process_worker_scopes",
    ):
        op.drop_table(name)
