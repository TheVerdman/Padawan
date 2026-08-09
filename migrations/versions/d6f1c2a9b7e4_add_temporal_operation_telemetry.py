"""add temporal telemetry, duration profiles, and authored demonstrations

Revision ID: d6f1c2a9b7e4
Revises: b41d3f0c92aa
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f1c2a9b7e4"
down_revision: str | Sequence[str] | None = "b41d3f0c92aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_spans",
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("parent_operation_id", sa.String(length=192), nullable=True),
        sa.Column("run_id", sa.String(length=96), nullable=True),
        sa.Column("source_ref", sa.String(length=192), nullable=True),
        sa.Column("operation_type", sa.String(length=128), nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("workload_class", sa.String(length=128), nullable=False),
        sa.Column("workload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_operation_span_status",
        ),
        sa.ForeignKeyConstraint(
            ["parent_operation_id"], ["operation_spans.operation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    with op.batch_alter_table("operation_spans") as batch:
        batch.create_index(
            "ix_operation_span_profile",
            [
                "operation_type",
                "environment_fingerprint",
                "workload_class",
                "completed_at",
            ],
            unique=False,
        )
        batch.create_index("ix_operation_span_active", ["status", "updated_at"], unique=False)

    op.create_table(
        "operation_span_events",
        sa.Column("event_id", sa.String(length=192), nullable=False),
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_operation_span_event_status",
        ),
        sa.CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1)",
            name="ck_operation_span_event_progress",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["operation_spans.operation_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("operation_id", "sequence", name="uq_operation_span_event_sequence"),
    )

    op.create_table(
        "duration_profiles",
        sa.Column("profile_id", sa.String(length=192), nullable=False),
        sa.Column("operation_type", sa.String(length=128), nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("workload_class", sa.String(length=128), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint("record_digest"),
    )
    with op.batch_alter_table("duration_profiles") as batch:
        batch.create_index(
            "ix_duration_profile_lookup",
            ["operation_type", "environment_fingerprint", "workload_class", "as_of"],
            unique=False,
        )

    op.create_table(
        "authored_demonstrations",
        sa.Column("demonstration_id", sa.String(length=192), nullable=False),
        sa.Column("domain_id", sa.String(length=160), nullable=False),
        sa.Column("competency_id", sa.String(length=128), nullable=False),
        sa.Column("source_item_id", sa.String(length=192), nullable=False),
        sa.Column("verifier_result_id", sa.String(length=128), nullable=False),
        sa.Column("rights_digest", sa.String(length=71), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["competencies.competency_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["corpus_items.item_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["verifier_result_id"],
            ["verifier_results.result_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("demonstration_id"),
        sa.UniqueConstraint("record_digest"),
    )
    with op.batch_alter_table("authored_demonstrations") as batch:
        batch.create_index(
            "ix_authored_demonstration_domain",
            ["domain_id", "competency_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("authored_demonstrations")
    op.drop_table("duration_profiles")
    op.drop_table("operation_span_events")
    op.drop_table("operation_spans")
