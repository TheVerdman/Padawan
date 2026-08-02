"""add training compiler and rights manifests

Revision ID: b41d3f0c92aa
Revises: 0ace28c76900
Create Date: 2026-08-01
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b41d3f0c92aa"
down_revision: str | Sequence[str] | None = "0ace28c76900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_digest(value: dict[str, Any]) -> str:
    content = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _legacy_rights(*, source: str, license_id: str, created_at: datetime) -> dict[str, Any]:
    reviewed_at = created_at.isoformat()
    if reviewed_at.endswith("+00:00"):
        reviewed_at = reviewed_at.removesuffix("+00:00") + "Z"
    if source.startswith("deterministic:padawan."):
        return {
            "schema_version": "1.0.0",
            "rights_id": "padawan.project-authored.internal",
            "version": "1.0.0",
            "basis": "project_authored",
            "basis_detail": (
                "Generated from project-authored Padawan code and admitted for internal "
                "research use."
            ),
            "permitted_uses": [
                "continued_pretraining",
                "evaluation",
                "evidence_retention",
                "internal_research",
                "preference",
                "process",
                "rlvr",
                "sft",
            ],
            "distribution_scope": "internal_only",
            "review_status": "confirmed",
            "license_id": None,
            "terms_uri": None,
            "terms_digest": None,
            "attribution": None,
            "restrictions": ["internal research only"],
            "reviewed_by": "padawan.generated-source-policy",
            "reviewed_at": reviewed_at,
        }
    return {
        "schema_version": "1.0.0",
        "rights_id": "padawan.legacy-import.review-required",
        "version": "1.0.0",
        "basis": "unknown",
        "basis_detail": (
            "Imported from the legacy license label; provenance and intended training uses "
            "require confirmation."
        ),
        "permitted_uses": ["evaluation", "evidence_retention", "internal_research"],
        "distribution_scope": "internal_only",
        "review_status": "review_required",
        "license_id": license_id,
        "terms_uri": None,
        "terms_digest": None,
        "attribution": None,
        "restrictions": ["legacy rights label requires review before training"],
        "reviewed_by": None,
        "reviewed_at": None,
    }


def upgrade() -> None:
    with op.batch_alter_table("corpus_items") as batch:
        batch.add_column(sa.Column("rights_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("rights_digest", sa.String(length=71), nullable=True))
        batch.alter_column("license", existing_type=sa.String(length=128), nullable=True)

    connection = op.get_bind()
    corpus = sa.table(
        "corpus_items",
        sa.column("item_id", sa.String()),
        sa.column("source", sa.String()),
        sa.column("license", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("rights_json", sa.JSON()),
        sa.column("rights_digest", sa.String()),
    )
    for row in connection.execute(
        sa.select(corpus.c.item_id, corpus.c.source, corpus.c.license, corpus.c.created_at)
    ).mappings():
        rights = _legacy_rights(
            source=str(row["source"]),
            license_id=str(row["license"] or "NOASSERTION"),
            created_at=row["created_at"],
        )
        connection.execute(
            corpus.update()
            .where(corpus.c.item_id == row["item_id"])
            .values(rights_json=rights, rights_digest=_canonical_digest(rights))
        )

    with op.batch_alter_table("corpus_items") as batch:
        batch.alter_column("rights_json", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("rights_digest", existing_type=sa.String(length=71), nullable=False)

    op.create_table(
        "training_source_documents",
        sa.Column("document_id", sa.String(length=192), nullable=False),
        sa.Column("source_id", sa.String(length=192), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("supersedes_document_id", sa.String(length=192), nullable=True),
        sa.Column("content_artifact_id", sa.String(length=96), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("rights_digest", sa.String(length=71), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_document_id"],
            ["training_source_documents.document_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint("record_digest"),
        sa.UniqueConstraint("source_id", "source_version", name="uq_training_source_version"),
    )

    op.create_table(
        "training_source_decisions",
        sa.Column("decision_id", sa.String(length=192), nullable=False),
        sa.Column("document_id", sa.String(length=192), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("contaminated", sa.Boolean(), nullable=False),
        sa.Column("record_digest", sa.String(length=71), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'review_required', 'quarantined', 'retired')",
            name="ck_training_source_decision_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["training_source_documents.document_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("record_digest"),
    )
    with op.batch_alter_table("training_source_decisions") as batch:
        batch.create_index(
            "ix_training_source_status",
            ["document_id", "status", "created_at"],
            unique=False,
        )

    op.create_table(
        "training_bundles",
        sa.Column("bundle_id", sa.String(length=128), nullable=False),
        sa.Column("compiler_version", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_artifact_id", sa.String(length=96), nullable=False),
        sa.Column("internal_only", sa.Boolean(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("internal_only = true", name="ck_training_bundle_internal_only"),
        sa.ForeignKeyConstraint(
            ["manifest_artifact_id"], ["artifacts.artifact_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("bundle_id"),
        sa.UniqueConstraint("manifest_digest"),
    )
    with op.batch_alter_table("training_bundles") as batch:
        batch.create_index(
            "ix_training_bundle_snapshot", ["source_snapshot_digest", "as_of"], unique=False
        )


def downgrade() -> None:
    op.drop_table("training_bundles")
    op.drop_table("training_source_decisions")
    op.drop_table("training_source_documents")

    connection = op.get_bind()
    corpus = sa.table(
        "corpus_items",
        sa.column("item_id", sa.String()),
        sa.column("license", sa.String()),
        sa.column("rights_json", sa.JSON()),
    )
    for row in connection.execute(
        sa.select(corpus.c.item_id, corpus.c.license, corpus.c.rights_json)
    ).mappings():
        if row["license"] is not None:
            continue
        rights = row["rights_json"] or {}
        restored = rights.get("license_id") or "NOASSERTION"
        connection.execute(
            corpus.update().where(corpus.c.item_id == row["item_id"]).values(license=restored)
        )

    with op.batch_alter_table("corpus_items") as batch:
        batch.alter_column("license", existing_type=sa.String(length=128), nullable=False)
        batch.drop_column("rights_digest")
        batch.drop_column("rights_json")
