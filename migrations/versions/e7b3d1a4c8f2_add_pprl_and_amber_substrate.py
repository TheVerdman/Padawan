"""add persistent-process RL and Amber substrate

Revision ID: e7b3d1a4c8f2
Revises: f91c2a7d4e30
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3d1a4c8f2"
down_revision: str | Sequence[str] | None = "f91c2a7d4e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_distributions",
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("distribution_id", sa.String(length=192), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("generator_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("distribution_digest"),
        sa.UniqueConstraint("distribution_id", "version", name="uq_process_distribution_version"),
    )
    op.create_table(
        "process_programs",
        sa.Column("program_digest", sa.String(length=71), nullable=False),
        sa.Column("program_id", sa.String(length=192), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("persistence_mode", sa.String(length=24), nullable=False),
        sa.Column("reward_authority_kind", sa.String(length=24), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "persistence_mode IN ('episodic', 'continual')",
            name="ck_process_program_persistence_mode",
        ),
        sa.CheckConstraint(
            "reward_authority_kind IN ('verifiable', 'empirical', 'adjudicated', 'hybrid')",
            name="ck_process_program_reward_authority",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_digest"],
            ["process_distributions.distribution_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("program_digest"),
        sa.UniqueConstraint("program_id", "version", name="uq_process_program_version"),
    )
    op.create_table(
        "amber_authorizations",
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("authorization_id", sa.String(length=192), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("program_digest", sa.String(length=71), nullable=False),
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["distribution_digest"],
            ["process_distributions.distribution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["program_digest"], ["process_programs.program_digest"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("authorization_digest"),
        sa.UniqueConstraint("authorization_id", "version", name="uq_amber_authorization_version"),
    )
    op.create_index(
        "ix_amber_authorization_expiry",
        "amber_authorizations",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "amber_authorization_heads",
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared', 'authorized', 'active', 'paused', 'quarantined', "
            "'release_approved', 'expired', 'revoked')",
            name="ck_amber_authorization_status",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("authorization_digest"),
    )
    op.create_index(
        "ix_amber_authorization_status",
        "amber_authorization_heads",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "amber_authorization_events",
        sa.Column("event_id", sa.String(length=192), nullable=False),
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=192), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "to_status IN ('prepared', 'authorized', 'active', 'paused', 'quarantined', "
            "'release_approved', 'expired', 'revoked')",
            name="ck_amber_event_to_status",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("record_digest"),
        sa.UniqueConstraint(
            "authorization_digest", "sequence", name="uq_amber_authorization_event_sequence"
        ),
    )
    op.create_table(
        "project_instances",
        sa.Column("instance_id", sa.String(length=192), nullable=False),
        sa.Column("instance_digest", sa.String(length=71), nullable=False),
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("split", sa.String(length=32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.Float(), nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "split IN ('train', 'adaptive_development', 'validation', 'sealed')",
            name="ck_project_instance_split",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_digest"],
            ["process_distributions.distribution_digest"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("instance_id"),
        sa.UniqueConstraint("instance_digest"),
        sa.UniqueConstraint(
            "distribution_digest", "split", "seed", name="uq_project_instance_sample"
        ),
    )
    op.create_index(
        "ix_project_instance_distribution",
        "project_instances",
        ["distribution_digest", "split"],
        unique=False,
    )
    op.create_table(
        "process_executions",
        sa.Column("execution_digest", sa.String(length=71), nullable=False),
        sa.Column("execution_id", sa.String(length=192), nullable=False),
        sa.Column("program_digest", sa.String(length=71), nullable=False),
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("instance_digest", sa.String(length=71), nullable=False),
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_digest"],
            ["process_distributions.distribution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instance_digest"], ["project_instances.instance_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["program_digest"], ["process_programs.program_digest"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("execution_digest"),
        sa.UniqueConstraint("execution_id"),
    )
    op.create_table(
        "process_rollouts",
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("execution_digest", sa.String(length=71), nullable=False),
        sa.Column("program_digest", sa.String(length=71), nullable=False),
        sa.Column("distribution_digest", sa.String(length=71), nullable=False),
        sa.Column("instance_id", sa.String(length=192), nullable=False),
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("split", sa.String(length=32), nullable=False),
        sa.Column("replication_index", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("initial_state_id", sa.String(length=192), nullable=False),
        sa.Column("current_state_id", sa.String(length=192), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("parent_rollout_id", sa.String(length=192), nullable=True),
        sa.Column("fork_id", sa.String(length=192), nullable=True),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=192), nullable=True),
        sa.Column("lease_token", sa.String(length=192), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "split IN ('train', 'adaptive_development', 'validation', 'sealed')",
            name="ck_process_rollout_split",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'paused', 'review_required', 'complete', "
            "'failed', 'quarantined', 'cancelled')",
            name="ck_process_rollout_status",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_digest"],
            ["process_distributions.distribution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_digest"],
            ["process_executions.execution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["project_instances.instance_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["program_digest"], ["process_programs.program_digest"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("rollout_id"),
        sa.UniqueConstraint("lease_token"),
        sa.UniqueConstraint(
            "execution_digest",
            "replication_index",
            name="uq_process_rollout_execution_replication",
        ),
    )
    op.create_index(
        "ix_process_rollout_claim",
        "process_rollouts",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_process_rollout_instance",
        "process_rollouts",
        ["instance_id", "replication_index"],
        unique=False,
    )
    with op.batch_alter_table("external_calls") as batch:
        batch.add_column(sa.Column("process_rollout_id", sa.String(length=192), nullable=True))
        batch.drop_constraint("ck_external_call_exactly_one_owner", type_="check")
        batch.create_foreign_key(
            "fk_external_call_process_rollout",
            "process_rollouts",
            ["process_rollout_id"],
            ["rollout_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_external_call_exactly_one_owner",
            "(run_id IS NOT NULL AND interaction_trace_id IS NULL AND "
            "process_rollout_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NOT NULL AND "
            "process_rollout_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NULL AND "
            "process_rollout_id IS NOT NULL)",
        )
        batch.create_index("ix_external_call_process_rollout", ["process_rollout_id"], unique=False)
    op.create_table(
        "process_states",
        sa.Column("state_id", sa.String(length=192), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("parent_state_id", sa.String(length=192), nullable=True),
        sa.Column("triggering_event_id", sa.String(length=192), nullable=True),
        sa.Column("state_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_state_id"], ["process_states.state_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("state_id"),
        sa.UniqueConstraint("state_digest"),
        sa.UniqueConstraint("triggering_event_id"),
        sa.UniqueConstraint("rollout_id", "sequence", name="uq_process_state_sequence"),
    )
    op.create_index(
        "ix_process_state_lineage",
        "process_states",
        ["rollout_id", "parent_state_id"],
        unique=False,
    )
    op.create_table(
        "process_events",
        sa.Column("event_id", sa.String(length=192), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("actor_id", sa.String(length=192), nullable=False),
        sa.Column("parent_state_id", sa.String(length=192), nullable=False),
        sa.Column("resulting_state_id", sa.String(length=192), nullable=False),
        sa.Column("worker_invocation_id", sa.String(length=192), nullable=True),
        sa.Column("research_execution_digest", sa.String(length=71), nullable=True),
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("amber_decision_id", sa.String(length=192), nullable=False),
        sa.Column("event_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_state_id"], ["process_states.state_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["research_execution_digest"],
            ["research_executions.execution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_state_id"], ["process_states.state_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("event_digest"),
        sa.UniqueConstraint("amber_decision_id"),
        sa.UniqueConstraint("resulting_state_id", name="uq_process_event_resulting_state"),
        sa.UniqueConstraint("rollout_id", "sequence", name="uq_process_event_sequence"),
    )
    op.create_index(
        "ix_process_event_kind",
        "process_events",
        ["kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "process_forks",
        sa.Column("fork_id", sa.String(length=192), nullable=False),
        sa.Column("parent_rollout_id", sa.String(length=192), nullable=False),
        sa.Column("parent_state_id", sa.String(length=192), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_state_id"], ["process_states.state_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fork_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_table(
        "process_fork_children",
        sa.Column("fork_id", sa.String(length=192), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("condition_id", sa.String(length=192), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["fork_id"], ["process_forks.fork_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("fork_id", "rollout_id"),
        sa.UniqueConstraint("fork_id", "condition_id", name="uq_process_fork_condition"),
        sa.UniqueConstraint("rollout_id", name="uq_process_fork_child_rollout"),
    )
    op.create_table(
        "process_outcomes",
        sa.Column("assessment_id", sa.String(length=192), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("authority_kind", sa.String(length=24), nullable=False),
        sa.Column("eligible_for_learning", sa.Boolean(), nullable=False),
        sa.Column("scalar_return", sa.Float(), nullable=True),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("assessment_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_index(
        "ix_process_outcome_rollout",
        "process_outcomes",
        ["rollout_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "process_training_eligibility",
        sa.Column("decision_id", sa.String(length=192), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("policy_id", sa.String(length=192), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_index(
        "ix_process_training_eligibility",
        "process_training_eligibility",
        ["rollout_id", "eligible", "created_at"],
        unique=False,
    )
    op.create_table(
        "process_worker_invocations",
        sa.Column("invocation_id", sa.String(length=192), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("role_id", sa.String(length=192), nullable=False),
        sa.Column("worker_model_digest", sa.String(length=71), nullable=False),
        sa.Column("amber_decision_id", sa.String(length=192), nullable=False),
        sa.Column("research_execution_digest", sa.String(length=71), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("response_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_process_worker_invocation_status",
        ),
        sa.ForeignKeyConstraint(
            ["request_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["research_execution_digest"],
            ["research_executions.execution_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["response_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
        sa.UniqueConstraint("amber_decision_id", name="uq_process_worker_invocation_admission"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_process_worker_invocation_rollout",
        "process_worker_invocations",
        ["rollout_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "amber_admission_decisions",
        sa.Column("decision_id", sa.String(length=192), nullable=False),
        sa.Column("authorization_digest", sa.String(length=71), nullable=False),
        sa.Column("authorization_sequence", sa.Integer(), nullable=False),
        sa.Column("rollout_id", sa.String(length=192), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("request_digest", sa.String(length=71), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('admitted', 'denied', 'review_required')",
            name="ck_amber_admission_disposition",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_digest"],
            ["amber_authorizations.authorization_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"], ["process_rollouts.rollout_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
    )
    op.create_index(
        "ix_amber_admission_rollout",
        "amber_admission_decisions",
        ["rollout_id", "decided_at"],
        unique=False,
    )
    with op.batch_alter_table("process_worker_invocations") as batch:
        batch.create_foreign_key(
            "fk_process_worker_invocation_amber_decision",
            "amber_admission_decisions",
            ["amber_decision_id"],
            ["decision_id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("process_events") as batch:
        batch.create_foreign_key(
            "fk_process_event_amber_decision",
            "amber_admission_decisions",
            ["amber_decision_id"],
            ["decision_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_process_event_worker_invocation",
            "process_worker_invocations",
            ["worker_invocation_id"],
            ["invocation_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("process_events") as batch:
        batch.drop_constraint("fk_process_event_worker_invocation", type_="foreignkey")
        batch.drop_constraint("fk_process_event_amber_decision", type_="foreignkey")
    with op.batch_alter_table("process_worker_invocations") as batch:
        batch.drop_constraint("fk_process_worker_invocation_amber_decision", type_="foreignkey")
    op.drop_index("ix_amber_admission_rollout", table_name="amber_admission_decisions")
    op.drop_table("amber_admission_decisions")
    op.drop_index("ix_process_worker_invocation_rollout", table_name="process_worker_invocations")
    op.drop_table("process_worker_invocations")
    op.drop_index("ix_process_training_eligibility", table_name="process_training_eligibility")
    op.drop_table("process_training_eligibility")
    op.drop_index("ix_process_outcome_rollout", table_name="process_outcomes")
    op.drop_table("process_outcomes")
    op.drop_table("process_fork_children")
    op.drop_table("process_forks")
    op.drop_index("ix_process_event_kind", table_name="process_events")
    op.drop_table("process_events")
    op.drop_index("ix_process_state_lineage", table_name="process_states")
    op.drop_table("process_states")
    op.execute("DELETE FROM external_calls WHERE process_rollout_id IS NOT NULL")
    with op.batch_alter_table("external_calls") as batch:
        batch.drop_index("ix_external_call_process_rollout")
        batch.drop_constraint("ck_external_call_exactly_one_owner", type_="check")
        batch.drop_constraint("fk_external_call_process_rollout", type_="foreignkey")
        batch.drop_column("process_rollout_id")
        batch.create_check_constraint(
            "ck_external_call_exactly_one_owner",
            "(run_id IS NOT NULL AND interaction_trace_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NOT NULL)",
        )
    op.drop_index("ix_process_rollout_instance", table_name="process_rollouts")
    op.drop_index("ix_process_rollout_claim", table_name="process_rollouts")
    op.drop_table("process_rollouts")
    op.drop_table("process_executions")
    op.drop_index("ix_project_instance_distribution", table_name="project_instances")
    op.drop_table("project_instances")
    op.drop_table("amber_authorization_events")
    op.drop_index("ix_amber_authorization_status", table_name="amber_authorization_heads")
    op.drop_table("amber_authorization_heads")
    op.drop_index("ix_amber_authorization_expiry", table_name="amber_authorizations")
    op.drop_table("amber_authorizations")
    op.drop_table("process_programs")
    op.drop_table("process_distributions")
