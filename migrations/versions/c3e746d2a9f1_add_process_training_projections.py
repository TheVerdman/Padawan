"""Separate public PPRL learning payloads from private archive/projection lineage.

Revision ID: c3e746d2a9f1
Revises: b7f418d6a0c5
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e746d2a9f1"
down_revision: str | Sequence[str] | None = "b7f418d6a0c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_training_projections",
        sa.Column("projection_id", sa.String(192), nullable=False),
        sa.Column("source_bundle_id", sa.String(128), nullable=False),
        sa.Column("policy_digest", sa.String(71), nullable=False),
        sa.Column("record_digest", sa.String(71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_bundle_id"], ["training_bundles.bundle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("projection_id"),
        sa.UniqueConstraint("record_digest"),
    )


def downgrade() -> None:
    op.drop_table("process_training_projections")
