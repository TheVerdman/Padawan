"""Conserved authorization-wide funding and private accounting; no legacy grants.

Revision ID: f6a8c2d4e913
Revises: d8b541c9e2a6
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a8c2d4e913"
down_revision: str | Sequence[str] | None = "d8b541c9e2a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_resource_grants",
        sa.Column("authorization_digest", sa.String(71), nullable=False),
        sa.Column("grant_id", sa.String(192), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("authorization_digest"),
        sa.UniqueConstraint("grant_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_table(
        "process_resource_accounts",
        sa.Column("authorization_digest", sa.String(71), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["process_resource_grants.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("authorization_digest"),
    )
    op.create_table(
        "process_resource_reservations",
        sa.Column("decision_id", sa.String(192), nullable=False),
        sa.Column("authorization_digest", sa.String(71), nullable=False),
        sa.Column("rollout_id", sa.String(192), nullable=False),
        sa.Column("state_digest", sa.String(71), nullable=False),
        sa.Column("lease_token_digest", sa.String(71), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["amber_admission_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["process_resource_accounts.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
        sa.UniqueConstraint(
            "rollout_id",
            "state_digest",
            "lease_token_digest",
            name="uq_process_resource_leased_action",
        ),
    )
    op.create_table(
        "process_resource_reservation_heads",
        sa.Column("decision_id", sa.String(192), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["process_resource_reservations.decision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.CheckConstraint(
            "status IN ('reserved', 'started', 'settled', 'released')",
            name="ck_process_resource_reservation_status",
        ),
    )
    op.create_table(
        "process_resource_events",
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("authorization_digest", sa.String(71), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["process_resource_grants.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("record_digest"),
        sa.UniqueConstraint(
            "authorization_digest", "sequence", name="uq_process_resource_sequence"
        ),
    )


def downgrade() -> None:
    op.drop_table("process_resource_events")
    op.drop_table("process_resource_reservation_heads")
    op.drop_table("process_resource_reservations")
    op.drop_table("process_resource_accounts")
    op.drop_table("process_resource_grants")
