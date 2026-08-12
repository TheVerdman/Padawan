"""add Padawan Interaction Lab records and invocation ownership

Revision ID: f91c2a7d4e30
Revises: c9e8f4a1d2b3
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f91c2a7d4e30"
down_revision: str | Sequence[str] | None = "c9e8f4a1d2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interaction_sessions",
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("current_consent_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "ix_interaction_session_activity",
        "interaction_sessions",
        ["last_activity_at"],
        unique=False,
    )
    op.create_table(
        "interaction_consent_events",
        sa.Column("consent_event_id", sa.String(length=96), nullable=False),
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("research_trace_consent", sa.Boolean(), nullable=False),
        sa.Column("retention_classification", sa.String(length=64), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_interaction_consent_positive_sequence"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interaction_sessions.session_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("consent_event_id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_interaction_consent_sequence"),
    )
    op.create_table(
        "interaction_messages",
        sa.Column("message_id", sa.String(length=96), nullable=False),
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("parent_message_id", sa.String(length=96), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_interaction_message_role"),
        sa.ForeignKeyConstraint(
            ["parent_message_id"], ["interaction_messages.message_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interaction_sessions.session_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_interaction_message_parent",
        "interaction_messages",
        ["parent_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_message_session",
        "interaction_messages",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "interaction_turns",
        sa.Column("turn_id", sa.String(length=96), nullable=False),
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("parent_turn_id", sa.String(length=96), nullable=True),
        sa.Column("source_turn_id", sa.String(length=96), nullable=True),
        sa.Column("user_message_id", sa.String(length=96), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=96), nullable=True),
        sa.Column("trace_id", sa.String(length=96), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('message', 'branch', 'retry', 'replay')",
            name="ck_interaction_turn_mode",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="ck_interaction_turn_status",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["interaction_messages.message_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_turn_id"], ["interaction_turns.turn_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interaction_sessions.session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id"], ["interaction_turns.turn_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"], ["interaction_messages.message_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.UniqueConstraint("assistant_message_id", name="uq_interaction_turn_assistant_message"),
        sa.UniqueConstraint("trace_id"),
        sa.UniqueConstraint("user_message_id", name="uq_interaction_turn_user_message"),
    )
    op.create_index(
        "ix_interaction_turn_parent",
        "interaction_turns",
        ["parent_turn_id"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_turn_session",
        "interaction_turns",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "interaction_traces",
        sa.Column("trace_id", sa.String(length=96), nullable=False),
        sa.Column("source_session_id", sa.String(length=96), nullable=False),
        sa.Column("source_turn_id", sa.String(length=96), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("evidence_class", sa.String(length=64), nullable=False),
        sa.Column("retention_classification", sa.String(length=64), nullable=False),
        sa.Column("controlled_benchmark_eligible", sa.Boolean(), nullable=False),
        sa.Column("memory_synthesis_status", sa.String(length=32), nullable=False),
        sa.Column("training_candidate_status", sa.String(length=32), nullable=False),
        sa.Column("selected_history_artifact_id", sa.String(length=96), nullable=False),
        sa.Column("generation_request_artifact_id", sa.String(length=96), nullable=False),
        sa.Column("wire_request_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("raw_events_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("public_response_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("private_reasoning_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("generation_result_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_metadata", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("timing", sa.JSON(), nullable=False),
        sa.Column("raw_event_summary", sa.JSON(), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "controlled_benchmark_eligible = false",
            name="ck_interaction_trace_not_benchmark",
        ),
        sa.CheckConstraint(
            "evidence_class = 'exploratory_not_controlled_benchmark'",
            name="ck_interaction_trace_exploratory",
        ),
        sa.CheckConstraint(
            "memory_synthesis_status = 'not_implemented'",
            name="ck_interaction_trace_memory_deferred",
        ),
        sa.CheckConstraint(
            "retention_classification IN ('personal_deletable', 'consented_research_evidence')",
            name="ck_interaction_trace_retention",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="ck_interaction_trace_status",
        ),
        sa.CheckConstraint(
            "training_candidate_status = 'not_admitted'",
            name="ck_interaction_trace_training_not_admitted",
        ),
        sa.ForeignKeyConstraint(
            ["generation_request_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_result_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["private_reasoning_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["public_response_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_events_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["selected_history_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["wire_request_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("trace_id"),
        sa.UniqueConstraint("manifest_digest"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("source_turn_id"),
    )
    op.create_index(
        "ix_interaction_trace_retention",
        "interaction_traces",
        ["retention_classification", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_trace_source",
        "interaction_traces",
        ["source_session_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "interaction_feedback",
        sa.Column("feedback_id", sa.String(length=96), nullable=False),
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("turn_id", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("consent_event_id", sa.String(length=96), nullable=False),
        sa.Column("retention_classification", sa.String(length=64), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('positive', 'negative', 'correction', 'note')",
            name="ck_interaction_feedback_kind",
        ),
        sa.CheckConstraint(
            "retention_classification IN ('personal_deletable', 'consented_research_evidence')",
            name="ck_interaction_feedback_retention",
        ),
        sa.PrimaryKeyConstraint("feedback_id"),
    )
    op.create_index(
        "ix_interaction_feedback_turn",
        "interaction_feedback",
        ["turn_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_feedback_retention",
        "interaction_feedback",
        ["retention_classification", "created_at"],
        unique=False,
    )
    with op.batch_alter_table("external_calls") as batch:
        batch.alter_column("run_id", existing_type=sa.String(length=96), nullable=True)
        batch.add_column(sa.Column("interaction_trace_id", sa.String(length=96), nullable=True))
        batch.create_foreign_key(
            "fk_external_call_interaction_trace",
            "interaction_traces",
            ["interaction_trace_id"],
            ["trace_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_external_call_exactly_one_owner",
            "(run_id IS NOT NULL AND interaction_trace_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NOT NULL)",
        )
        batch.create_index(
            "ix_external_call_interaction_trace", ["interaction_trace_id"], unique=False
        )


def downgrade() -> None:
    op.execute("DELETE FROM external_calls WHERE interaction_trace_id IS NOT NULL")
    with op.batch_alter_table("external_calls") as batch:
        batch.drop_index("ix_external_call_interaction_trace")
        batch.drop_constraint("ck_external_call_exactly_one_owner", type_="check")
        batch.drop_constraint("fk_external_call_interaction_trace", type_="foreignkey")
        batch.drop_column("interaction_trace_id")
        batch.alter_column("run_id", existing_type=sa.String(length=96), nullable=False)
    op.drop_index("ix_interaction_feedback_turn", table_name="interaction_feedback")
    op.drop_table("interaction_feedback")
    op.drop_index("ix_interaction_trace_source", table_name="interaction_traces")
    op.drop_index("ix_interaction_trace_retention", table_name="interaction_traces")
    op.drop_table("interaction_traces")
    op.drop_index("ix_interaction_turn_session", table_name="interaction_turns")
    op.drop_index("ix_interaction_turn_parent", table_name="interaction_turns")
    op.drop_table("interaction_turns")
    op.drop_index("ix_interaction_message_session", table_name="interaction_messages")
    op.drop_index("ix_interaction_message_parent", table_name="interaction_messages")
    op.drop_table("interaction_messages")
    op.drop_table("interaction_consent_events")
    op.drop_index("ix_interaction_session_activity", table_name="interaction_sessions")
    op.drop_table("interaction_sessions")
