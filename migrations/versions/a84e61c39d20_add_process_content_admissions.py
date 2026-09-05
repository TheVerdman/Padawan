"""Retain exact content-boundary policy receipts without admitting legacy rows.

Revision ID: a84e61c39d20
Revises: f2a6d9b1e403
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a84e61c39d20"
down_revision: str | Sequence[str] | None = "f2a6d9b1e403"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No old state/event receives automatic content admission.
    op.create_table(
        "process_content_admissions",
        sa.Column("record_kind", sa.String(length=16), nullable=False),
        sa.Column("record_id", sa.String(length=192), nullable=False),
        sa.Column("source_digest", sa.String(length=71), nullable=False),
        sa.Column("execution_digest", sa.String(length=71), nullable=False),
        sa.Column("policy_digest", sa.String(length=71), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("record_kind IN ('state', 'event')", name="ck_process_content_kind"),
        sa.ForeignKeyConstraint(
            ["execution_digest"], ["process_executions.execution_digest"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("record_kind", "record_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_index(
        "ix_process_content_execution",
        "process_content_admissions",
        ["execution_digest", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_process_content_execution", table_name="process_content_admissions")
    op.drop_table("process_content_admissions")
