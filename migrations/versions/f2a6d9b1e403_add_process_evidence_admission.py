"""Add immutable information classification and reviewed process evidence.

Revision ID: f2a6d9b1e403
Revises: e7b3d1a4c8f2
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6d9b1e403"
down_revision: str | Sequence[str] | None = "e7b3d1a4c8f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing artifacts receive no automatic classification or admission.
    op.create_table(
        "artifact_information",
        sa.Column("artifact_id", sa.String(length=96), nullable=False),
        sa.Column("information_class", sa.String(length=32), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "information_class IN ('process_candidate', 'forensic')",
            name="ck_artifact_information_class",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_table(
        "process_evidence_admissions",
        sa.Column("process_artifact_id", sa.String(length=192), nullable=False),
        sa.Column("execution_digest", sa.String(length=71), nullable=False),
        sa.Column("candidate_classification_digest", sa.String(length=71), nullable=False),
        sa.Column("policy_digest", sa.String(length=71), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_executions.execution_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_classification_digest"],
            ["artifact_information.record_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("process_artifact_id"),
        sa.UniqueConstraint("record_digest"),
    )


def downgrade() -> None:
    op.drop_table("process_evidence_admissions")
    op.drop_table("artifact_information")
