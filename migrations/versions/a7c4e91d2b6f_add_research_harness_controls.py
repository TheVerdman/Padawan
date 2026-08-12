"""add immutable harness profiles and research execution bindings

Revision ID: a7c4e91d2b6f
Revises: d6f1c2a9b7e4
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e91d2b6f"
down_revision: str | Sequence[str] | None = "d6f1c2a9b7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "harness_profiles",
        sa.Column("profile_digest", sa.String(length=71), nullable=False),
        sa.Column("profile_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=96), nullable=False),
        sa.Column("tier", sa.String(length=96), nullable=False),
        sa.Column("purpose", sa.String(length=160), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_digest"),
        sa.UniqueConstraint("profile_id", "version", name="uq_harness_profile_version"),
    )
    with op.batch_alter_table("harness_profiles") as batch:
        batch.create_index("ix_harness_profile_tier", ["tier", "purpose"], unique=False)

    op.create_table(
        "research_executions",
        sa.Column("execution_digest", sa.String(length=71), nullable=False),
        sa.Column("execution_id", sa.String(length=160), nullable=False),
        sa.Column("harness_profile_digest", sa.String(length=71), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=256), nullable=False),
        sa.Column("runtime_id", sa.String(length=256), nullable=False),
        sa.Column("research_role", sa.String(length=32), nullable=False),
        sa.Column("parent_state_id", sa.String(length=96), nullable=False),
        sa.Column("parent_state_hash", sa.String(length=71), nullable=False),
        sa.Column("task_id", sa.String(length=192), nullable=False),
        sa.Column("task_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("corpus_digest", sa.String(length=71), nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["harness_profile_digest"],
            ["harness_profiles.profile_digest"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_state_id"],
            ["student_states.state_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("execution_digest"),
        sa.UniqueConstraint("execution_id"),
    )
    with op.batch_alter_table("research_executions") as batch:
        batch.create_index(
            "ix_research_execution_checkpoint",
            ["checkpoint_id", "created_at"],
            unique=False,
        )
        batch.create_index(
            "ix_research_execution_parent_state",
            ["parent_state_id", "created_at"],
            unique=False,
        )
        batch.create_index(
            "ix_research_execution_task",
            ["task_id", "environment_fingerprint"],
            unique=False,
        )

    with op.batch_alter_table("experiments") as batch:
        batch.add_column(
            sa.Column("research_execution_digest", sa.String(length=71), nullable=True)
        )
        batch.create_foreign_key(
            "fk_experiments_research_execution",
            "research_executions",
            ["research_execution_digest"],
            ["execution_digest"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column("research_execution_digest", sa.String(length=71), nullable=True)
        )
        batch.create_foreign_key(
            "fk_runs_research_execution",
            "research_executions",
            ["research_execution_digest"],
            ["execution_digest"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("study_experiments") as batch:
        batch.add_column(
            sa.Column("research_execution_digest", sa.String(length=71), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "factor_values",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.create_foreign_key(
            "fk_study_experiments_research_execution",
            "research_executions",
            ["research_execution_digest"],
            ["execution_digest"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("checkpoint_evaluations") as batch:
        batch.drop_constraint("uq_checkpoint_evaluation", type_="unique")
        batch.add_column(sa.Column("condition_id", sa.String(length=128), nullable=True))
        batch.create_unique_constraint(
            "uq_checkpoint_evaluation_condition",
            ["checkpoint_id", "study_id", "condition_id", "suite_manifest_digest"],
        )

    op.create_table(
        "study_results",
        sa.Column("result_id", sa.String(length=128), nullable=False),
        sa.Column("study_id", sa.String(length=128), nullable=False),
        sa.Column("study_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("suite_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("condition_id", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=256), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["study_id"], ["studies.study_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("result_id"),
        sa.UniqueConstraint("record_digest"),
        sa.UniqueConstraint(
            "study_id",
            "condition_id",
            "checkpoint_id",
            name="uq_study_result_condition_checkpoint",
        ),
    )
    with op.batch_alter_table("study_results") as batch:
        batch.create_index(
            "ix_study_result_suite",
            ["suite_manifest_digest", "checkpoint_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("study_results")
    with op.batch_alter_table("checkpoint_evaluations") as batch:
        batch.drop_constraint("uq_checkpoint_evaluation_condition", type_="unique")
        batch.drop_column("condition_id")
        batch.create_unique_constraint(
            "uq_checkpoint_evaluation",
            ["checkpoint_id", "study_id", "suite_manifest_digest"],
        )
    with op.batch_alter_table("study_experiments") as batch:
        batch.drop_constraint("fk_study_experiments_research_execution", type_="foreignkey")
        batch.drop_column("factor_values")
        batch.drop_column("research_execution_digest")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_research_execution", type_="foreignkey")
        batch.drop_column("research_execution_digest")
    with op.batch_alter_table("experiments") as batch:
        batch.drop_constraint("fk_experiments_research_execution", type_="foreignkey")
        batch.drop_column("research_execution_digest")
    op.drop_table("research_executions")
    op.drop_table("harness_profiles")
