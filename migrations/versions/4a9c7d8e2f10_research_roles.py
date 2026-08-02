"""separate research role from provider identity

Revision ID: 4a9c7d8e2f10
Revises: 08bc4fd6c08b
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a9c7d8e2f10"
down_revision: str | None = "08bc4fd6c08b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CHECK = "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')"


def upgrade() -> None:
    with op.batch_alter_table("students") as batch:
        batch.add_column(
            sa.Column(
                "research_role", sa.String(length=32), nullable=False, server_default="target"
            )
        )
    with op.batch_alter_table("student_states") as batch:
        batch.add_column(
            sa.Column(
                "research_role", sa.String(length=32), nullable=False, server_default="target"
            )
        )
    with op.batch_alter_table("attempts") as batch:
        batch.add_column(
            sa.Column(
                "research_role", sa.String(length=32), nullable=False, server_default="target"
            )
        )
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "research_role", sa.String(length=32), nullable=False, server_default="target"
            )
        )
    for table, constraint in (
        ("students", "ck_students_research_role"),
        ("student_states", "ck_student_states_research_role"),
        ("attempts", "ck_attempts_research_role"),
        ("runs", "ck_runs_research_role"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("research_role", server_default=None)
            batch.create_check_constraint(constraint, ROLE_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_research_role", type_="check")
        batch.drop_column("research_role")
    with op.batch_alter_table("attempts") as batch:
        batch.drop_constraint("ck_attempts_research_role", type_="check")
        batch.drop_column("research_role")
    with op.batch_alter_table("student_states") as batch:
        batch.drop_constraint("ck_student_states_research_role", type_="check")
        batch.drop_column("research_role")
    with op.batch_alter_table("students") as batch:
        batch.drop_constraint("ck_students_research_role", type_="check")
        batch.drop_column("research_role")
