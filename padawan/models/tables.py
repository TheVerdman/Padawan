from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CompetencyRow(Base):
    __tablename__ = "competencies"

    competency_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parent_competency_id: Mapped[str | None] = mapped_column(
        ForeignKey("competencies.competency_id", ondelete="RESTRICT")
    )
    prerequisite_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    grader_requirements: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    teacher_modes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    difficulty_calibration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TemplateFamilyRow(Base):
    __tablename__ = "template_families"
    __table_args__ = (
        UniqueConstraint("template_family_id", "visibility_class"),
        CheckConstraint(
            "visibility_class IN ('training', 'evaluation', 'sealed')",
            name="ck_template_visibility",
        ),
    )

    template_family_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    competency_id: Mapped[str] = mapped_column(
        ForeignKey("competencies.competency_id", ondelete="RESTRICT"), nullable=False
    )
    visibility_class: Mapped[str] = mapped_column(String(24), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    contaminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstanceGroupRow(Base):
    __tablename__ = "instance_groups"
    __table_args__ = (
        UniqueConstraint(
            "instance_group_id",
            "template_family_id",
            "visibility_class",
            name="uq_instance_group_lineage",
        ),
        ForeignKeyConstraint(
            ["template_family_id", "visibility_class"],
            ["template_families.template_family_id", "template_families.visibility_class"],
            ondelete="RESTRICT",
            name="fk_instance_group_family_visibility",
        ),
    )

    instance_group_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    template_family_id: Mapped[str] = mapped_column(String(160), nullable=False)
    visibility_class: Mapped[str] = mapped_column(String(24), nullable=False)
    generation_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    sibling_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CorpusItemRow(Base):
    __tablename__ = "corpus_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["instance_group_id", "template_family_id", "visibility_class"],
            [
                "instance_groups.instance_group_id",
                "instance_groups.template_family_id",
                "instance_groups.visibility_class",
            ],
            ondelete="RESTRICT",
            name="fk_item_instance_lineage",
        ),
        CheckConstraint(
            "(pool = 'quarantine') OR "
            "(pool = 'curriculum' AND visibility_class = 'training') OR "
            "(pool = 'rotating_shadow' AND visibility_class = 'evaluation') OR "
            "(pool = 'sealed_anchor' AND visibility_class = 'sealed')",
            name="ck_item_pool_visibility",
        ),
        CheckConstraint("difficulty >= 0 AND difficulty <= 1", name="ck_item_difficulty"),
        CheckConstraint(
            "status IN ('active', 'leased', 'retired', 'quarantined')",
            name="ck_item_status",
        ),
        Index("ix_corpus_lease", "pool", "status", "lease_expires_at"),
    )

    item_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    competency_id: Mapped[str] = mapped_column(
        ForeignKey("competencies.competency_id", ondelete="RESTRICT"), nullable=False
    )
    template_family_id: Mapped[str] = mapped_column(String(160), nullable=False)
    instance_group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    visibility_class: Mapped[str] = mapped_column(String(24), nullable=False)
    generation_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    generator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verifier_spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pool: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    license: Mapped[str | None] = mapped_column(String(128))
    rights_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rights_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    contamination_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    # Matched siblings share one group claim token so non-PostgreSQL local tests
    # can atomically claim the whole block in a single conditional UPDATE.
    lease_token: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(Text)


class ExposureRow(Base):
    __tablename__ = "exposures"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "state_id",
            "item_id",
            "exposure_type",
            "episode_id",
            name="uq_exposure_idempotency",
        ),
        Index("ix_exposure_family", "student_id", "template_family_id"),
    )

    exposure_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state_id: Mapped[str] = mapped_column(String(96), nullable=False)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    template_family_id: Mapped[str] = mapped_column(String(160), nullable=False)
    instance_group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    exposure_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answer_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    critique_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    repair_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    metadata_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    episode_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    uri: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactInformationRow(Base):
    __tablename__ = "artifact_information"
    __table_args__ = (
        CheckConstraint(
            "information_class IN ('process_candidate', 'forensic')",
            name="ck_artifact_information_class",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), primary_key=True
    )
    information_class: Mapped[str] = mapped_column(String(32), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessEvidenceAdmissionRow(Base):
    __tablename__ = "process_evidence_admissions"

    process_artifact_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    execution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    candidate_classification_digest: Mapped[str] = mapped_column(
        ForeignKey("artifact_information.record_digest", ondelete="RESTRICT"), nullable=False
    )
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessContentAdmissionRow(Base):
    __tablename__ = "process_content_admissions"
    __table_args__ = (
        CheckConstraint("record_kind IN ('state', 'event')", name="ck_process_content_kind"),
        Index("ix_process_content_execution", "execution_digest", "created_at"),
    )

    record_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    execution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessObservationRow(Base):
    __tablename__ = "process_observations"
    __table_args__ = (
        UniqueConstraint(
            "rollout_id",
            "state_id",
            "worker_id",
            "lease_token_digest",
            "authorization_sequence",
            "policy_digest",
            name="uq_process_observation_context",
        ),
    )

    observation_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    state_id: Mapped[str] = mapped_column(
        ForeignKey("process_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    execution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    content_receipt_digest: Mapped[str] = mapped_column(
        ForeignKey("process_content_admissions.record_digest", ondelete="RESTRICT"), nullable=False
    )
    worker_id: Mapped[str] = mapped_column(String(192), nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    authorization_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    observation_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessObservationDecisionRow(Base):
    __tablename__ = "process_observation_decisions"

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"), primary_key=True
    )
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("process_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessGenerationWorkloadRow(Base):
    __tablename__ = "process_generation_workloads"

    invocation_id: Mapped[str] = mapped_column(
        ForeignKey("process_worker_invocations.invocation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("process_observations.observation_id", ondelete="RESTRICT"), nullable=False
    )
    prepared_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessContainerWorkloadRow(Base):
    __tablename__ = "process_container_workloads"

    invocation_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    decision_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    rollout_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"),
        nullable=False,
    )
    observation_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("process_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    container_name: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    input_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProcessContainerHeadRow(Base):
    __tablename__ = "process_container_heads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'starting', 'finished', 'unknown')",
            name="ck_process_container_status",
        ),
    )

    invocation_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("process_container_workloads.invocation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)


class ProcessContainerReceiptRow(Base):
    __tablename__ = "process_container_receipts"

    invocation_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("process_container_workloads.invocation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    evidence_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProcessResourceGrantRow(Base):
    __tablename__ = "process_resource_grants"

    authorization_digest: Mapped[str] = mapped_column(
        String(71),
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        primary_key=True,
    )
    grant_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProcessResourceAccountRow(Base):
    __tablename__ = "process_resource_accounts"

    authorization_digest: Mapped[str] = mapped_column(
        String(71),
        ForeignKey("process_resource_grants.authorization_digest", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False)


class ProcessResourceReservationRow(Base):
    __tablename__ = "process_resource_reservations"
    __table_args__ = (
        UniqueConstraint(
            "rollout_id",
            "state_digest",
            "lease_token_digest",
            name="uq_process_resource_leased_action",
        ),
    )

    decision_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    authorization_digest: Mapped[str] = mapped_column(
        String(71),
        ForeignKey("process_resource_accounts.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    state_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProcessResourceReservationHeadRow(Base):
    __tablename__ = "process_resource_reservation_heads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'started', 'settled', 'released')",
            name="ck_process_resource_reservation_status",
        ),
    )

    decision_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("process_resource_reservations.decision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False)


class ProcessResourceEventRow(Base):
    __tablename__ = "process_resource_events"
    __table_args__ = (
        UniqueConstraint("authorization_digest", "sequence", name="uq_process_resource_sequence"),
    )

    record_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    authorization_digest: Mapped[str] = mapped_column(
        String(71),
        ForeignKey("process_resource_grants.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProcessTrainingProjectionRow(Base):
    __tablename__ = "process_training_projections"

    projection_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    source_bundle_id: Mapped[str] = mapped_column(
        ForeignKey("training_bundles.bundle_id", ondelete="RESTRICT"), nullable=False
    )
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactReferenceRow(Base):
    __tablename__ = "artifact_references"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "artifact_id", name="uq_artifact_ref"),
    )

    reference_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(192), nullable=False)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingSourceDocumentRow(Base):
    __tablename__ = "training_source_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "source_version", name="uq_training_source_version"),
    )

    document_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(192), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_source_documents.document_id", ondelete="RESTRICT")
    )
    content_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rights_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingSourceDecisionRow(Base):
    __tablename__ = "training_source_decisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'review_required', 'quarantined', 'retired')",
            name="ck_training_source_decision_status",
        ),
        Index("ix_training_source_status", "document_id", "status", "created_at"),
    )

    decision_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("training_source_documents.document_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    contaminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthoredDemonstrationRow(Base):
    __tablename__ = "authored_demonstrations"
    __table_args__ = (
        Index(
            "ix_authored_demonstration_domain",
            "domain_id",
            "competency_id",
            "created_at",
        ),
    )

    demonstration_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(160), nullable=False)
    competency_id: Mapped[str] = mapped_column(
        ForeignKey("competencies.competency_id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    verifier_result_id: Mapped[str] = mapped_column(
        ForeignKey("verifier_results.result_id", ondelete="RESTRICT"), nullable=False
    )
    rights_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingBundleRow(Base):
    __tablename__ = "training_bundles"
    __table_args__ = (
        CheckConstraint("internal_only = true", name="ck_training_bundle_internal_only"),
        Index("ix_training_bundle_snapshot", "source_snapshot_digest", "as_of"),
    )

    bundle_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    manifest_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    internal_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProvenanceHeadRow(Base):
    __tablename__ = "provenance_heads"

    stream_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_hash: Mapped[str] = mapped_column(String(71), nullable=False)


class ProvenanceEventRow(Base):
    __tablename__ = "provenance_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "position", name="uq_provenance_stream_position"),
        Index("ix_provenance_episode", "episode_id"),
        Index("ix_provenance_state", "state_lineage_id"),
    )

    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    stream_id: Mapped[str] = mapped_column(
        ForeignKey("provenance_heads.stream_id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    state_lineage_id: Mapped[str | None] = mapped_column(String(96))
    episode_id: Mapped[str | None] = mapped_column(String(96))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    previous_chain_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    chain_hash: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    code_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudentRow(Base):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint(
            "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')",
            name="ck_students_research_role",
        ),
    )

    student_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    research_role: Mapped[str] = mapped_column(String(32), nullable=False, default="target")
    canonical_state_id: Mapped[str | None] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudentStateRow(Base):
    __tablename__ = "student_states"
    __table_args__ = (
        UniqueConstraint("student_id", "state_hash", name="uq_student_state_hash"),
        Index("ix_state_branch", "student_id", "branch_id", "created_at"),
        CheckConstraint(
            "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')",
            name="ck_student_states_research_role",
        ),
    )

    state_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id", ondelete="RESTRICT"), nullable=False
    )
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_role: Mapped[str] = mapped_column(String(32), nullable=False, default="target")
    parent_state_id: Mapped[str | None] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT")
    )
    branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    compacted_working_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    lesson_memory_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    unresolved_hypotheses: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    competency_estimates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    active_experiment_id: Mapped[str | None] = mapped_column(String(96))
    state_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    creation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StateForkRow(Base):
    __tablename__ = "state_forks"
    __table_args__ = (
        CheckConstraint("treatment_state_id <> control_state_id", name="ck_fork_distinct_states"),
        CheckConstraint(
            "treatment_branch_id <> control_branch_id", name="ck_fork_distinct_branches"
        ),
    )

    fork_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    parent_state_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    treatment_state_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    control_state_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    treatment_branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    control_branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    intervention: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EpisodeRow(Base):
    __tablename__ = "episodes"

    episode_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id", ondelete="RESTRICT"), nullable=False
    )
    state_before_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    state_after_id: Mapped[str | None] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT")
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptRow(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_attempt_request"),
        CheckConstraint(
            "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')",
            name="ck_attempts_research_role",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    state_before_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    state_after_id: Mapped[str | None] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT")
    )
    request_id: Mapped[str] = mapped_column(String(160), nullable=False)
    response_id: Mapped[str | None] = mapped_column(String(256))
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_role: Mapped[str] = mapped_column(String(32), nullable=False, default="target")
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GradeRow(Base):
    __tablename__ = "grades"

    grade_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.attempt_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeacherInterventionRow(Base):
    __tablename__ = "teacher_interventions"
    __table_args__ = (UniqueConstraint("request_id", name="uq_teacher_request"),)

    intervention_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.attempt_id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RevisionRow(Base):
    __tablename__ = "revisions"

    revision_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    original_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.attempt_id", ondelete="RESTRICT"), nullable=False
    )
    intervention_id: Mapped[str] = mapped_column(
        ForeignKey("teacher_interventions.intervention_id", ondelete="RESTRICT"), nullable=False
    )
    revised_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.attempt_id", ondelete="RESTRICT"), nullable=False
    )
    revised_grade_id: Mapped[str] = mapped_column(
        ForeignKey("grades.grade_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TransferTrialRow(Base):
    __tablename__ = "transfer_trials"

    transfer_trial_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"), nullable=False
    )
    experiment_id: Mapped[str | None] = mapped_column(String(96))
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LessonVersionRow(Base):
    __tablename__ = "lesson_versions"
    __table_args__ = (
        UniqueConstraint("lesson_id", "version", name="uq_lesson_version"),
        Index("ix_lesson_retrieval", "competency_id", "error_class", "status"),
    )

    lesson_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    competency_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_class: Mapped[str] = mapped_column(String(128), nullable=False)
    state_lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    general_rule: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    successful_transfer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_transfer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    harmful_retrieval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemorySnapshotRow(Base):
    __tablename__ = "memory_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state_lineage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    active_lesson_versions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalDecisionRow(Base):
    __tablename__ = "retrieval_decisions"

    retrieval_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    state_id: Mapped[str] = mapped_column(String(96), nullable=False)
    branch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_lesson_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    retrieved_lesson_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rejected: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessProfileRow(Base):
    __tablename__ = "harness_profiles"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_harness_profile_version"),
        Index("ix_harness_profile_tier", "tier", "purpose"),
    )

    profile_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(96), nullable=False)
    tier: Mapped[str] = mapped_column(String(96), nullable=False)
    purpose: Mapped[str] = mapped_column(String(160), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchExecutionRow(Base):
    __tablename__ = "research_executions"
    __table_args__ = (
        Index("ix_research_execution_checkpoint", "checkpoint_id", "created_at"),
        Index("ix_research_execution_parent_state", "parent_state_id", "created_at"),
        Index("ix_research_execution_task", "task_id", "environment_fingerprint"),
    )

    execution_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    harness_profile_digest: Mapped[str] = mapped_column(
        ForeignKey("harness_profiles.profile_digest", ondelete="RESTRICT"), nullable=False
    )
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_role: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_state_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    parent_state_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    task_id: Mapped[str] = mapped_column(String(192), nullable=False)
    task_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    corpus_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperimentRow(Base):
    __tablename__ = "experiments"

    experiment_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    parent_state_id: Mapped[str] = mapped_column(String(96), nullable=False)
    research_execution_digest: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT")
    )
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    design: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentBlockRow(Base):
    __tablename__ = "experiment_blocks"
    __table_args__ = (UniqueConstraint("experiment_id", "block_index"),)

    block_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="RESTRICT"), nullable=False
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcomes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    contamination_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    infrastructure_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class VerifierResultRow(Base):
    __tablename__ = "verifier_results"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('verified', 'rejected', 'unknown', 'infrastructure_failure')",
            name="ck_verifier_result_disposition",
        ),
        Index("ix_verifier_scope", "verifier_id", "scope", "created_at"),
    )

    result_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    verifier_id: Mapped[str] = mapped_column(String(128), nullable=False)
    verifier_version: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(192), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RewardPolicyRow(Base):
    __tablename__ = "reward_policies"

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RewardRow(Base):
    __tablename__ = "rewards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["reward_policies.policy_id", "reward_policies.version"],
            ondelete="RESTRICT",
            name="fk_reward_policy",
        ),
        Index("ix_reward_policy", "policy_id", "policy_version", "created_at"),
    )

    reward_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    derived_utility: Mapped[float | None] = mapped_column(Float)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingEligibilityRow(Base):
    __tablename__ = "training_eligibility_decisions"
    __table_args__ = (Index("ix_training_eligibility_reward", "reward_id", "created_at"),)

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reward_id: Mapped[str] = mapped_column(
        ForeignKey("rewards.reward_id", ondelete="RESTRICT"), nullable=False
    )
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudyRow(Base):
    __tablename__ = "studies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'active', 'complete', 'cancelled', 'invalid')",
            name="ck_study_status",
        ),
        UniqueConstraint("study_id", "version", name="uq_study_version"),
        Index("ix_study_suite", "suite_manifest_digest", "status"),
    )

    study_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    suite_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudyExperimentRow(Base):
    __tablename__ = "study_experiments"
    __table_args__ = (
        UniqueConstraint("study_id", "experiment_id", name="uq_study_experiment"),
        CheckConstraint(
            "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')",
            name="ck_study_experiment_role",
        ),
        CheckConstraint(
            "assignment_propensity IS NULL OR "
            "(assignment_propensity > 0 AND assignment_propensity <= 1)",
            name="ck_study_experiment_propensity",
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("studies.study_id", ondelete="RESTRICT"), nullable=False
    )
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.experiment_id", ondelete="RESTRICT"), nullable=False
    )
    condition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_role: Mapped[str] = mapped_column(String(32), nullable=False)
    suite_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    research_execution_digest: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT")
    )
    factor_values: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    assignment_propensity: Mapped[float | None] = mapped_column(Float)


class StudyResultRow(Base):
    __tablename__ = "study_results"
    __table_args__ = (
        UniqueConstraint(
            "study_id",
            "condition_id",
            "checkpoint_id",
            name="uq_study_result_condition_checkpoint",
        ),
        Index("ix_study_result_suite", "suite_manifest_digest", "checkpoint_id"),
    )

    result_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("studies.study_id", ondelete="RESTRICT"), nullable=False
    )
    study_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    suite_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationTrialRow(Base):
    __tablename__ = "evaluation_trials"
    __table_args__ = (
        CheckConstraint(
            "trial_type IN ('retention', 'interference')", name="ck_evaluation_trial_type"
        ),
        CheckConstraint(
            "status IN ('scheduled', 'leased', 'complete', 'cancelled', 'invalid')",
            name="ck_evaluation_trial_status",
        ),
        CheckConstraint(
            "assignment_propensity IS NULL OR "
            "(assignment_propensity > 0 AND assignment_propensity <= 1)",
            name="ck_evaluation_trial_propensity",
        ),
        CheckConstraint(
            "(trial_type = 'retention' AND interfering_episode_id IS NULL) OR "
            "(trial_type = 'interference' AND interfering_episode_id IS NOT NULL)",
            name="ck_evaluation_trial_interfering_episode",
        ),
        UniqueConstraint(
            "study_id",
            "student_id",
            "source_episode_id",
            "trial_type",
            "instance_group_id",
            name="uq_evaluation_trial_freshness",
        ),
        Index("ix_evaluation_trial_due", "status", "due_at", "lease_expires_at"),
        Index("ix_evaluation_trial_student", "student_id", "competency_id", "created_at"),
    )

    trial_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("studies.study_id", ondelete="RESTRICT"), nullable=False
    )
    trial_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"), nullable=False
    )
    interfering_episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT")
    )
    student_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("student_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    competency_id: Mapped[str] = mapped_column(String(128), nullable=False)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_items.item_id", ondelete="RESTRICT"), nullable=False
    )
    instance_group_id: Mapped[str] = mapped_column(String(160), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    assignment_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_propensity: Mapped[float | None] = mapped_column(Float)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcome_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_token: Mapped[str | None] = mapped_column(String(160), unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckpointRow(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'evaluating', 'promoted', 'rejected', "
            "'quarantined', 'revoked')",
            name="ck_checkpoint_status",
        ),
        CheckConstraint(
            "parent_checkpoint_id IS NULL OR parent_checkpoint_id <> checkpoint_id",
            name="ck_checkpoint_parent_distinct",
        ),
        Index("ix_checkpoint_model_status", "model_id", "status", "created_at"),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("checkpoints.checkpoint_id", ondelete="RESTRICT")
    )
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tokenizer_id: Mapped[str] = mapped_column(String(256), nullable=False)
    model_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    tokenizer_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    training_bundle_manifest_digest: Mapped[str | None] = mapped_column(String(71))
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationSuiteRow(Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (UniqueConstraint("suite_id", "version", name="uq_evaluation_suite_version"),)

    manifest_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    suite_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointPromotionPolicyRow(Base):
    __tablename__ = "checkpoint_promotion_policies"

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointEvaluationRow(Base):
    __tablename__ = "checkpoint_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "checkpoint_id",
            "study_id",
            "condition_id",
            "suite_manifest_digest",
            name="uq_checkpoint_evaluation_condition",
        ),
        Index("ix_checkpoint_evaluation_suite", "suite_manifest_digest", "created_at"),
    )

    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("checkpoints.checkpoint_id", ondelete="RESTRICT"), nullable=False
    )
    study_id: Mapped[str] = mapped_column(
        ForeignKey("studies.study_id", ondelete="RESTRICT"), nullable=False
    )
    condition_id: Mapped[str | None] = mapped_column(String(128))
    suite_manifest_digest: Mapped[str] = mapped_column(
        ForeignKey("evaluation_suites.manifest_digest", ondelete="RESTRICT"), nullable=False
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointComparisonRow(Base):
    __tablename__ = "checkpoint_comparisons"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["checkpoint_promotion_policies.policy_id", "checkpoint_promotion_policies.version"],
            ondelete="RESTRICT",
            name="fk_checkpoint_comparison_policy",
        ),
        UniqueConstraint(
            "baseline_evaluation_id",
            "candidate_evaluation_id",
            "policy_id",
            "policy_version",
            name="uq_checkpoint_comparison_inputs",
        ),
        CheckConstraint(
            "baseline_evaluation_id <> candidate_evaluation_id",
            name="ck_checkpoint_comparison_distinct",
        ),
    )

    comparison_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    baseline_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("checkpoint_evaluations.evaluation_id", ondelete="RESTRICT"), nullable=False
    )
    candidate_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("checkpoint_evaluations.evaluation_id", ondelete="RESTRICT"), nullable=False
    )
    suite_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointDecisionRow(Base):
    __tablename__ = "checkpoint_decisions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('start_evaluation', 'promote', 'reject', 'quarantine', 'revoke')",
            name="ck_checkpoint_decision_action",
        ),
        CheckConstraint(
            "from_status IN ('candidate', 'evaluating', 'promoted', 'rejected', "
            "'quarantined', 'revoked')",
            name="ck_checkpoint_decision_from_status",
        ),
        CheckConstraint(
            "to_status IN ('candidate', 'evaluating', 'promoted', 'rejected', "
            "'quarantined', 'revoked')",
            name="ck_checkpoint_decision_to_status",
        ),
        Index("ix_checkpoint_decision", "checkpoint_id", "created_at"),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("checkpoints.checkpoint_id", ondelete="RESTRICT"), nullable=False
    )
    comparison_id: Mapped[str | None] = mapped_column(
        ForeignKey("checkpoint_comparisons.comparison_id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InteractionSessionRow(Base):
    __tablename__ = "interaction_sessions"
    __table_args__ = (Index("ix_interaction_session_activity", "last_activity_at"),)

    session_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    current_consent_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InteractionConsentEventRow(Base):
    __tablename__ = "interaction_consent_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_interaction_consent_sequence"),
        CheckConstraint("sequence >= 1", name="ck_interaction_consent_positive_sequence"),
    )

    consent_event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interaction_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    research_trace_consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retention_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InteractionMessageRow(Base):
    __tablename__ = "interaction_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_interaction_message_role"),
        Index("ix_interaction_message_session", "session_id", "created_at"),
        Index("ix_interaction_message_parent", "parent_message_id"),
    )

    message_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interaction_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    parent_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_messages.message_id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InteractionTurnRow(Base):
    __tablename__ = "interaction_turns"
    __table_args__ = (
        UniqueConstraint("user_message_id", name="uq_interaction_turn_user_message"),
        UniqueConstraint("assistant_message_id", name="uq_interaction_turn_assistant_message"),
        CheckConstraint(
            "mode IN ('message', 'branch', 'retry', 'replay')",
            name="ck_interaction_turn_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="ck_interaction_turn_status",
        ),
        Index("ix_interaction_turn_session", "session_id", "created_at"),
        Index("ix_interaction_turn_parent", "parent_turn_id"),
    )

    turn_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interaction_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    parent_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_turns.turn_id", ondelete="CASCADE")
    )
    source_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_turns.turn_id", ondelete="SET NULL")
    )
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey("interaction_messages.message_id", ondelete="RESTRICT"), nullable=False
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_messages.message_id", ondelete="RESTRICT")
    )
    trace_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InteractionTraceRow(Base):
    __tablename__ = "interaction_traces"
    __table_args__ = (
        CheckConstraint(
            "retention_classification IN ('personal_deletable', 'consented_research_evidence')",
            name="ck_interaction_trace_retention",
        ),
        CheckConstraint(
            "evidence_class = 'exploratory_not_controlled_benchmark'",
            name="ck_interaction_trace_exploratory",
        ),
        CheckConstraint(
            "controlled_benchmark_eligible = false",
            name="ck_interaction_trace_not_benchmark",
        ),
        CheckConstraint(
            "memory_synthesis_status = 'not_implemented'",
            name="ck_interaction_trace_memory_deferred",
        ),
        CheckConstraint(
            "training_candidate_status = 'not_admitted'",
            name="ck_interaction_trace_training_not_admitted",
        ),
        CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="ck_interaction_trace_status",
        ),
        Index("ix_interaction_trace_source", "source_session_id", "created_at"),
        Index("ix_interaction_trace_retention", "retention_classification", "created_at"),
    )

    trace_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_turn_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    controlled_benchmark_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    memory_synthesis_status: Mapped[str] = mapped_column(String(32), nullable=False)
    training_candidate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_history_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    generation_request_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    wire_request_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    raw_events_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    public_response_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    private_reasoning_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    generation_result_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timing: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_event_summary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    record_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InteractionFeedbackRow(Base):
    __tablename__ = "interaction_feedback"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('positive', 'negative', 'correction', 'note')",
            name="ck_interaction_feedback_kind",
        ),
        CheckConstraint(
            "retention_classification IN ('personal_deletable', 'consented_research_evidence')",
            name="ck_interaction_feedback_retention",
        ),
        Index("ix_interaction_feedback_turn", "turn_id", "created_at"),
        Index("ix_interaction_feedback_retention", "retention_classification", "created_at"),
    )

    feedback_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(96), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(96), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    consent_event_id: Mapped[str] = mapped_column(String(96), nullable=False)
    retention_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("active_student_id", name="uq_run_active_student"),
        Index("ix_run_claim", "state", "lease_expires_at"),
        Index("ix_run_student", "student_id", "created_at"),
        CheckConstraint(
            "research_role IN ('target', 'baseline', 'teacher', 'verifier', 'adjudicator')",
            name="ck_runs_research_role",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    episode_id: Mapped[str | None] = mapped_column(String(96), unique=True)
    student_id: Mapped[str | None] = mapped_column(String(128))
    active_student_id: Mapped[str | None] = mapped_column(String(128))
    research_role: Mapped[str] = mapped_column(String(32), nullable=False, default="target")
    research_execution_digest: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_token: Mapped[str | None] = mapped_column(String(160), unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunTransitionRow(Base):
    __tablename__ = "run_transitions"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_transition_sequence"),)

    transition_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(64), nullable=False)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalCallRow(Base):
    __tablename__ = "external_calls"
    __table_args__ = (
        CheckConstraint(
            "(run_id IS NOT NULL AND interaction_trace_id IS NULL AND "
            "process_rollout_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NOT NULL AND "
            "process_rollout_id IS NULL) OR "
            "(run_id IS NULL AND interaction_trace_id IS NULL AND "
            "process_rollout_id IS NOT NULL)",
            name="ck_external_call_exactly_one_owner",
        ),
        Index("ix_external_call_interaction_trace", "interaction_trace_id"),
        Index("ix_external_call_process_rollout", "process_rollout_id"),
    )

    request_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="RESTRICT"))
    interaction_trace_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_traces.trace_id", ondelete="RESTRICT")
    )
    process_rollout_id: Mapped[str | None] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    response_artifact_id: Mapped[str | None] = mapped_column(String(96))
    provider_response_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_envelope_digest: Mapped[str | None] = mapped_column(String(71))
    result_model_id: Mapped[str | None] = mapped_column(String(256))
    result_protocol: Mapped[str | None] = mapped_column(String(128))
    result_raw_request_digest: Mapped[str | None] = mapped_column(String(71))
    result_raw_response_digest: Mapped[str | None] = mapped_column(String(71))
    result_output_text_digest: Mapped[str | None] = mapped_column(String(71))
    result_usage: Mapped[dict[str, int] | None] = mapped_column(JSON)
    result_capabilities_digest: Mapped[str | None] = mapped_column(String(71))
    result_latency_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationSpanRow(Base):
    __tablename__ = "operation_spans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_operation_span_status",
        ),
        Index(
            "ix_operation_span_profile",
            "operation_type",
            "environment_fingerprint",
            "workload_class",
            "completed_at",
        ),
        Index("ix_operation_span_active", "status", "updated_at"),
    )

    operation_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    parent_operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("operation_spans.operation_id", ondelete="RESTRICT")
    )
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="RESTRICT"))
    source_ref: Mapped[str | None] = mapped_column(String(192))
    operation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    workload_class: Mapped[str] = mapped_column(String(128), nullable=False)
    workload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationSpanEventRow(Base):
    __tablename__ = "operation_span_events"
    __table_args__ = (
        UniqueConstraint("operation_id", "sequence", name="uq_operation_span_event_sequence"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_operation_span_event_status",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1)",
            name="ck_operation_span_event_progress",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operation_spans.operation_id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    progress: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class DurationProfileRow(Base):
    __tablename__ = "duration_profiles"
    __table_args__ = (
        Index(
            "ix_duration_profile_lookup",
            "operation_type",
            "environment_fingerprint",
            "workload_class",
            "as_of",
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    workload_class: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerRow(Base):
    __tablename__ = "workers"

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_run_id: Mapped[str | None] = mapped_column(String(96))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewQueueRow(Base):
    __tablename__ = "review_queue"

    review_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    review_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(192), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AtlasBenchmarkClaimRow(Base):
    __tablename__ = "atlas_benchmark_claims"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('vendor', 'upstream', 'community')",
            name="ck_atlas_benchmark_claim_source_kind",
        ),
        UniqueConstraint("supersedes_claim_id", name="uq_atlas_claim_supersession"),
        Index(
            "ix_atlas_claim_benchmark",
            "benchmark_id",
            "benchmark_version",
            "metric_id",
        ),
        Index("ix_atlas_claim_model", "model_id", "model_revision"),
    )

    claim_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    supersedes_claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("atlas_benchmark_claims.claim_id", ondelete="RESTRICT")
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_revision: Mapped[str] = mapped_column(String(192), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(192), nullable=False)
    benchmark_id: Mapped[str] = mapped_column(String(192), nullable=False)
    benchmark_version: Mapped[str] = mapped_column(String(128), nullable=False)
    split: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(192), nullable=False)
    source_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasDatasetGovernanceRow(Base):
    __tablename__ = "atlas_dataset_governance"
    __table_args__ = (
        CheckConstraint(
            "access_classification IN ('local', 'registration_only', 'requires_approval', "
            "'unavailable', 'legally_unclear')",
            name="ck_atlas_dataset_access",
        ),
        CheckConstraint(
            "redistribution_classification IN ('permitted', 'metadata_only', 'prohibited', "
            "'unknown')",
            name="ck_atlas_dataset_redistribution",
        ),
        CheckConstraint(
            "contamination_classification IN ('unassessed', 'no_known_exposure', 'suspected', "
            "'confirmed')",
            name="ck_atlas_dataset_contamination",
        ),
        CheckConstraint(
            "evaluation_class IN ('development', 'adaptive_search', 'challenge', "
            "'sealed_promotion')",
            name="ck_atlas_dataset_evaluation_class",
        ),
        UniqueConstraint(
            "benchmark_id",
            "benchmark_version",
            "dataset_revision",
            name="uq_atlas_dataset_revision",
        ),
        Index(
            "ix_atlas_dataset_benchmark",
            "benchmark_id",
            "benchmark_version",
            "dataset_revision",
        ),
    )

    governance_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    benchmark_id: Mapped[str] = mapped_column(String(192), nullable=False)
    benchmark_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_revision: Mapped[str] = mapped_column(String(192), nullable=False)
    access_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    redistribution_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    contamination_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_class: Mapped[str] = mapped_column(String(32), nullable=False)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasItemRow(Base):
    __tablename__ = "atlas_items"
    __table_args__ = (
        CheckConstraint(
            "adapter_kind IN ('static_qa', 'generated_verifier', 'coding_agentic', "
            "'interactive_environment', 'context_memory', 'multimodal')",
            name="ck_atlas_item_adapter_kind",
        ),
        Index("ix_atlas_item_family", "family_id", "difficulty"),
    )

    item_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    family_id: Mapped[str] = mapped_column(String(192), nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    adapter_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasSuiteRow(Base):
    __tablename__ = "atlas_suites"
    __table_args__ = (
        UniqueConstraint("suite_id", "version", name="uq_atlas_suite_version"),
        CheckConstraint(
            "status IN ('planned', 'ready', 'sealed', 'blocked', 'retired')",
            name="ck_atlas_suite_status",
        ),
        CheckConstraint(
            "evaluation_class IN ('development', 'adaptive_search', 'challenge', "
            "'sealed_promotion')",
            name="ck_atlas_suite_evaluation_class",
        ),
        CheckConstraint(
            "adapter_kind IN ('static_qa', 'generated_verifier', 'coding_agentic', "
            "'interactive_environment', 'context_memory', 'multimodal')",
            name="ck_atlas_suite_adapter_kind",
        ),
        Index("ix_atlas_suite_benchmark", "benchmark_id", "benchmark_version", "split"),
        Index("ix_atlas_suite_evaluation_suite", "evaluation_suite_manifest_digest"),
    )

    suite_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    suite_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    governance_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_dataset_governance.governance_id", ondelete="RESTRICT"),
        nullable=False,
    )
    benchmark_id: Mapped[str] = mapped_column(String(192), nullable=False)
    benchmark_version: Mapped[str] = mapped_column(String(128), nullable=False)
    split: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_class: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_suite_manifest_digest: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_suites.manifest_digest", ondelete="RESTRICT")
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasSuiteItemRow(Base):
    __tablename__ = "atlas_suite_items"
    __table_args__ = (
        UniqueConstraint("suite_digest", "position", name="uq_atlas_suite_item_position"),
    )

    suite_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_suites.suite_digest", ondelete="RESTRICT"), primary_key=True
    )
    item_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_items.item_digest", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class AtlasOntologyRow(Base):
    __tablename__ = "atlas_ontologies"
    __table_args__ = (UniqueConstraint("ontology_id", "version", name="uq_atlas_ontology_version"),)

    ontology_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    ontology_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasCampaignRow(Base):
    __tablename__ = "atlas_campaigns"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_atlas_campaign_version"),
        CheckConstraint(
            "status IN ('planned', 'ready', 'externally_gated', 'complete', 'invalid')",
            name="ck_atlas_campaign_status",
        ),
        Index("ix_atlas_campaign_status", "status", "created_at"),
    )

    campaign_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_ontologies.ontology_digest", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasCampaignClaimRow(Base):
    __tablename__ = "atlas_campaign_claims"

    campaign_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_campaigns.campaign_digest", ondelete="RESTRICT"), primary_key=True
    )
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_benchmark_claims.claim_id", ondelete="RESTRICT"), primary_key=True
    )


class AtlasCampaignConditionRow(Base):
    __tablename__ = "atlas_campaign_conditions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_digest"],
            ["atlas_campaigns.campaign_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_campaign_condition_campaign",
        ),
        ForeignKeyConstraint(
            ["required_execution_digest"],
            ["research_executions.execution_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_campaign_condition_execution",
        ),
    )

    campaign_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    required_execution_digest: Mapped[str | None] = mapped_column(String(71))
    externally_gated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    condition_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AtlasCampaignSuiteRow(Base):
    __tablename__ = "atlas_campaign_suites"
    __table_args__ = (
        CheckConstraint(
            "evaluation_class IN ('development', 'adaptive_search', 'challenge', "
            "'sealed_promotion')",
            name="ck_atlas_campaign_suite_evaluation_class",
        ),
    )

    campaign_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_campaigns.campaign_digest", ondelete="RESTRICT"), primary_key=True
    )
    suite_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_suites.suite_digest", ondelete="RESTRICT"), primary_key=True
    )
    evaluation_class: Mapped[str] = mapped_column(String(32), nullable=False)
    planned_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    trials_per_item: Mapped[int] = mapped_column(Integer, nullable=False)
    adaptive: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AtlasCampaignSuiteConditionRow(Base):
    __tablename__ = "atlas_campaign_suite_conditions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_digest", "suite_digest"],
            ["atlas_campaign_suites.campaign_digest", "atlas_campaign_suites.suite_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_campaign_suite_condition_suite",
        ),
        ForeignKeyConstraint(
            ["campaign_digest", "condition_id"],
            ["atlas_campaign_conditions.campaign_digest", "atlas_campaign_conditions.condition_id"],
            ondelete="RESTRICT",
            name="fk_atlas_campaign_suite_condition_condition",
        ),
    )

    campaign_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    suite_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(192), primary_key=True)


class AtlasCampaignExecutionBindingRow(Base):
    __tablename__ = "atlas_campaign_execution_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_digest", "suite_digest", "condition_id"],
            [
                "atlas_campaign_suite_conditions.campaign_digest",
                "atlas_campaign_suite_conditions.suite_digest",
                "atlas_campaign_suite_conditions.condition_id",
            ],
            ondelete="RESTRICT",
            name="fk_atlas_execution_binding_suite_condition",
        ),
        UniqueConstraint(
            "campaign_digest",
            "condition_id",
            "research_execution_digest",
            name="uq_atlas_execution_binding_coordinates",
        ),
        UniqueConstraint(
            "campaign_digest",
            "condition_id",
            "suite_digest",
            "research_execution_digest",
            name="uq_atlas_execution_binding_request_target",
        ),
        Index(
            "ix_atlas_execution_binding_execution",
            "research_execution_digest",
            "harness_profile_digest",
        ),
    )

    binding_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    campaign_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(192), nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    harness_profile_digest: Mapped[str] = mapped_column(
        ForeignKey("harness_profiles.profile_digest", ondelete="RESTRICT"), nullable=False
    )
    external_authorization_ref: Mapped[str | None] = mapped_column(String(512))
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasAllocationRow(Base):
    __tablename__ = "atlas_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_digest", "suite_digest"],
            ["atlas_campaign_suites.campaign_digest", "atlas_campaign_suites.suite_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_allocation_campaign_suite",
        ),
        ForeignKeyConstraint(
            ["campaign_digest", "condition_id"],
            ["atlas_campaign_conditions.campaign_digest", "atlas_campaign_conditions.condition_id"],
            ondelete="RESTRICT",
            name="fk_atlas_allocation_campaign_condition",
        ),
        ForeignKeyConstraint(
            ["suite_digest", "item_digest"],
            ["atlas_suite_items.suite_digest", "atlas_suite_items.item_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_allocation_suite_item",
        ),
        UniqueConstraint(
            "campaign_digest",
            "condition_id",
            "suite_digest",
            "item_digest",
            "trial_index",
            name="uq_atlas_allocation_trial",
        ),
        UniqueConstraint(
            "campaign_digest",
            "condition_id",
            "suite_digest",
            "decision_sequence",
            name="uq_atlas_allocation_decision_sequence",
        ),
        Index("ix_atlas_allocation_campaign", "campaign_digest", "condition_id"),
    )

    allocation_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    campaign_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(192), nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    item_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    trial_index: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasRunManifestRow(Base):
    __tablename__ = "atlas_run_manifests"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "campaign_digest",
                "condition_id",
                "suite_digest",
                "research_execution_digest",
            ],
            [
                "atlas_campaign_execution_bindings.campaign_digest",
                "atlas_campaign_execution_bindings.condition_id",
                "atlas_campaign_execution_bindings.suite_digest",
                "atlas_campaign_execution_bindings.research_execution_digest",
            ],
            ondelete="RESTRICT",
            name="fk_atlas_run_manifest_execution_binding",
        ),
        CheckConstraint(
            "run_kind IN ('offline_verification', 'model_evaluation', 'replay_grading')",
            name="ck_atlas_run_manifest_kind",
        ),
        CheckConstraint(
            "evaluation_class IN ('development', 'adaptive_search', 'challenge', "
            "'sealed_promotion')",
            name="ck_atlas_run_manifest_evaluation_class",
        ),
        Index("ix_atlas_run_manifest_campaign", "campaign_digest", "condition_id"),
    )

    manifest_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    run_manifest_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    run_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    binding_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_campaign_execution_bindings.binding_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    campaign_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(192), nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    research_execution_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    harness_profile_digest: Mapped[str] = mapped_column(
        ForeignKey("harness_profiles.profile_digest", ondelete="RESTRICT"), nullable=False
    )
    evaluation_class: Mapped[str] = mapped_column(String(32), nullable=False)
    adaptive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allocation_policy_id: Mapped[str] = mapped_column(String(192), nullable=False)
    allocation_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    stop_rule_id: Mapped[str | None] = mapped_column(String(192))
    request_template_digest: Mapped[str | None] = mapped_column(String(71))
    planned_request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retry_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_actions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    external_execution: Mapped[bool] = mapped_column(Boolean, nullable=False)
    external_authorization_ref: Mapped[str | None] = mapped_column(String(512))
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasTrialRequestRow(Base):
    __tablename__ = "atlas_trial_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_digest", "condition_id"],
            ["atlas_campaign_conditions.campaign_digest", "atlas_campaign_conditions.condition_id"],
            ondelete="RESTRICT",
            name="fk_atlas_trial_request_campaign_condition",
        ),
        ForeignKeyConstraint(
            ["suite_digest", "item_digest"],
            ["atlas_suite_items.suite_digest", "atlas_suite_items.item_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_trial_request_suite_item",
        ),
        ForeignKeyConstraint(
            [
                "campaign_digest",
                "condition_id",
                "suite_digest",
                "research_execution_digest",
            ],
            [
                "atlas_campaign_execution_bindings.campaign_digest",
                "atlas_campaign_execution_bindings.condition_id",
                "atlas_campaign_execution_bindings.suite_digest",
                "atlas_campaign_execution_bindings.research_execution_digest",
            ],
            ondelete="RESTRICT",
            name="fk_atlas_trial_request_execution_binding",
        ),
        UniqueConstraint("allocation_id", "attempt_index", name="uq_atlas_request_attempt"),
        UniqueConstraint(
            "campaign_digest",
            "condition_id",
            "suite_digest",
            "item_digest",
            "trial_index",
            "attempt_index",
            name="uq_atlas_request_trial_attempt",
        ),
        Index("ix_atlas_request_execution", "research_execution_digest", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    allocation_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_allocations.allocation_id", ondelete="RESTRICT"), nullable=False
    )
    parent_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("atlas_trial_requests.request_id", ondelete="RESTRICT")
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_run_manifests.run_id", ondelete="RESTRICT"), nullable=False
    )
    campaign_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    condition_id: Mapped[str] = mapped_column(String(192), nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    item_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    trial_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_index: Mapped[int] = mapped_column(Integer, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    prompt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    tool_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(192), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(128), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasTrialResultRow(Base):
    __tablename__ = "atlas_trial_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('verified_success', 'verified_failure', 'partial', 'abstained', "
            "'malformed', 'unscorable', 'verifier_failure', 'parser_failure', 'timeout', "
            "'infrastructure_failure', 'contaminated', 'not_run')",
            name="ck_atlas_trial_result_status",
        ),
        Index("ix_atlas_result_execution", "research_execution_digest", "completed_at"),
    )

    result_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_trial_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    success: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    response_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    result_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasPhenomenonRow(Base):
    __tablename__ = "atlas_phenomena"

    phenomenon_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    phenomenon_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    ontology_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_ontologies.ontology_digest", ondelete="RESTRICT"), nullable=False
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasProbeSetRow(Base):
    __tablename__ = "atlas_probe_sets"

    probe_set_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    probe_set_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    phenomenon_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_phenomena.phenomenon_digest", ondelete="RESTRICT"), nullable=False
    )
    suite_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_suites.suite_digest", ondelete="RESTRICT"), nullable=False
    )
    outcome_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasProbeItemRow(Base):
    __tablename__ = "atlas_probe_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["suite_digest", "item_digest"],
            ["atlas_suite_items.suite_digest", "atlas_suite_items.item_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_probe_item_suite_item",
        ),
    )

    probe_set_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_probe_sets.probe_set_digest", ondelete="RESTRICT"), primary_key=True
    )
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    item_digest: Mapped[str] = mapped_column(String(71), primary_key=True)


class AtlasProbeResultRow(Base):
    __tablename__ = "atlas_probe_results"

    probe_set_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_probe_sets.probe_set_digest", ondelete="RESTRICT"), primary_key=True
    )
    result_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_trial_results.result_digest", ondelete="RESTRICT"), primary_key=True
    )


class AtlasFailureClusterRow(Base):
    __tablename__ = "atlas_failure_clusters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'admitted', 'rejected', 'superseded')",
            name="ck_atlas_failure_cluster_status",
        ),
        Index("ix_atlas_failure_cluster_campaign", "campaign_digest", "status"),
    )

    cluster_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    campaign_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_campaigns.campaign_digest", ondelete="RESTRICT"), nullable=False
    )
    ontology_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_ontologies.ontology_digest", ondelete="RESTRICT"), nullable=False
    )
    probe_set_digest: Mapped[str | None] = mapped_column(
        ForeignKey("atlas_probe_sets.probe_set_digest", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasFailureClusterResultRow(Base):
    __tablename__ = "atlas_failure_cluster_results"

    cluster_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_failure_clusters.cluster_digest", ondelete="RESTRICT"), primary_key=True
    )
    result_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_trial_results.result_digest", ondelete="RESTRICT"), primary_key=True
    )
    exemplar: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AtlasSnapshotRow(Base):
    __tablename__ = "atlas_snapshots"
    __table_args__ = (
        Index("ix_atlas_snapshot_execution", "research_execution_digest", "created_at"),
    )

    snapshot_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    campaign_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_campaigns.campaign_digest", ondelete="RESTRICT"), nullable=False
    )
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    harness_profile_digest: Mapped[str] = mapped_column(
        ForeignKey("harness_profiles.profile_digest", ondelete="RESTRICT"), nullable=False
    )
    ontology_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_ontologies.ontology_digest", ondelete="RESTRICT"), nullable=False
    )
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasComparisonRow(Base):
    __tablename__ = "atlas_comparisons"
    __table_args__ = (
        CheckConstraint(
            "left_snapshot_digest <> right_snapshot_digest",
            name="ck_atlas_comparison_distinct_snapshots",
        ),
        Index(
            "ix_atlas_comparison_snapshots",
            "left_snapshot_digest",
            "right_snapshot_digest",
        ),
    )

    comparison_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    comparison_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    left_snapshot_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_snapshots.snapshot_digest", ondelete="RESTRICT"), nullable=False
    )
    right_snapshot_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_snapshots.snapshot_digest", ondelete="RESTRICT"), nullable=False
    )
    causal_claim_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasExploratoryProposalRow(Base):
    __tablename__ = "atlas_exploratory_proposals"
    __table_args__ = (
        CheckConstraint(
            "raw_chat_promoted = false", name="ck_atlas_proposal_raw_chat_not_promoted"
        ),
    )

    proposal_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    deduplication_key: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    consent_evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_trace_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_trace_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    raw_chat_promoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasExploratoryReproductionRow(Base):
    __tablename__ = "atlas_exploratory_reproductions"

    reproduction_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_exploratory_proposals.proposal_id", ondelete="RESTRICT"), nullable=False
    )
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    independent_item_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_items.item_digest", ondelete="RESTRICT"), nullable=False
    )
    reproduced: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasExploratoryReproductionResultRow(Base):
    __tablename__ = "atlas_exploratory_reproduction_results"

    reproduction_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_exploratory_reproductions.reproduction_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    result_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_trial_results.result_digest", ondelete="RESTRICT"), primary_key=True
    )


class AtlasChallengeAdmissionRow(Base):
    __tablename__ = "atlas_challenge_admissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["challenge_suite_digest", "admitted_item_digest"],
            ["atlas_suite_items.suite_digest", "atlas_suite_items.item_digest"],
            ondelete="RESTRICT",
            name="fk_atlas_challenge_admission_suite_item",
        ),
        CheckConstraint("action IN ('admit', 'reject')", name="ck_atlas_challenge_action"),
        UniqueConstraint("reproduction_id", name="uq_atlas_challenge_reproduction_decision"),
    )

    decision_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_exploratory_proposals.proposal_id", ondelete="RESTRICT"), nullable=False
    )
    reproduction_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_exploratory_reproductions.reproduction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    challenge_suite_digest: Mapped[str | None] = mapped_column(String(71))
    admitted_item_digest: Mapped[str | None] = mapped_column(String(71))
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasTrainingEligibilityRow(Base):
    __tablename__ = "atlas_training_eligibility"
    __table_args__ = (Index("ix_atlas_training_eligible", "eligible", "created_at"),)

    assessment_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    failure_cluster_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_failure_clusters.cluster_digest", ondelete="RESTRICT"), nullable=False
    )
    source_suite_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_suites.suite_digest", ondelete="RESTRICT"), nullable=False
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasMemoryEligibilityRow(Base):
    __tablename__ = "atlas_memory_eligibility"
    __table_args__ = (Index("ix_atlas_memory_eligible", "eligible", "created_at"),)

    assessment_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    failure_cluster_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_failure_clusters.cluster_digest", ondelete="RESTRICT"), nullable=False
    )
    source_suite_digest: Mapped[str] = mapped_column(
        ForeignKey("atlas_suites.suite_digest", ondelete="RESTRICT"), nullable=False
    )
    research_execution_digest: Mapped[str] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessDistributionRow(Base):
    __tablename__ = "process_distributions"
    __table_args__ = (
        UniqueConstraint("distribution_id", "version", name="uq_process_distribution_version"),
    )

    distribution_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    distribution_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    generator_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessProgramRow(Base):
    __tablename__ = "process_programs"
    __table_args__ = (
        UniqueConstraint("program_id", "version", name="uq_process_program_version"),
        CheckConstraint(
            "persistence_mode IN ('episodic', 'continual')",
            name="ck_process_program_persistence_mode",
        ),
        CheckConstraint(
            "reward_authority_kind IN ('verifiable', 'empirical', 'adjudicated', 'hybrid')",
            name="ck_process_program_reward_authority",
        ),
    )

    program_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    program_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    distribution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_distributions.distribution_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    persistence_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    reward_authority_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AmberAuthorizationRow(Base):
    __tablename__ = "amber_authorizations"
    __table_args__ = (
        UniqueConstraint("authorization_id", "version", name="uq_amber_authorization_version"),
        Index("ix_amber_authorization_expiry", "expires_at"),
    )

    authorization_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    authorization_id: Mapped[str] = mapped_column(String(192), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    program_digest: Mapped[str] = mapped_column(
        ForeignKey("process_programs.program_digest", ondelete="RESTRICT"), nullable=False
    )
    distribution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_distributions.distribution_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AmberAuthorizationHeadRow(Base):
    __tablename__ = "amber_authorization_heads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'authorized', 'active', 'paused', 'quarantined', "
            "'release_approved', 'expired', 'revoked')",
            name="ck_amber_authorization_status",
        ),
        Index("ix_amber_authorization_status", "status", "updated_at"),
    )

    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AmberAuthorizationEventRow(Base):
    __tablename__ = "amber_authorization_events"
    __table_args__ = (
        UniqueConstraint(
            "authorization_digest", "sequence", name="uq_amber_authorization_event_sequence"
        ),
        CheckConstraint(
            "to_status IN ('prepared', 'authorized', 'active', 'paused', 'quarantined', "
            "'release_approved', 'expired', 'revoked')",
            name="ck_amber_event_to_status",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(192), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectInstanceRow(Base):
    __tablename__ = "project_instances"
    __table_args__ = (
        UniqueConstraint("distribution_digest", "split", "seed", name="uq_project_instance_sample"),
        CheckConstraint(
            "split IN ('train', 'adaptive_development', 'validation', 'sealed')",
            name="ck_project_instance_split",
        ),
        Index("ix_project_instance_distribution", "distribution_digest", "split"),
    )

    instance_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    instance_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    distribution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_distributions.distribution_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessExecutionRow(Base):
    __tablename__ = "process_executions"

    execution_digest: Mapped[str] = mapped_column(String(71), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    program_digest: Mapped[str] = mapped_column(
        ForeignKey("process_programs.program_digest", ondelete="RESTRICT"), nullable=False
    )
    distribution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_distributions.distribution_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    instance_digest: Mapped[str] = mapped_column(
        ForeignKey("project_instances.instance_digest", ondelete="RESTRICT"), nullable=False
    )
    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    environment_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessRolloutRow(Base):
    __tablename__ = "process_rollouts"
    __table_args__ = (
        UniqueConstraint(
            "execution_digest",
            "replication_index",
            name="uq_process_rollout_execution_replication",
        ),
        CheckConstraint(
            "split IN ('train', 'adaptive_development', 'validation', 'sealed')",
            name="ck_process_rollout_split",
        ),
        CheckConstraint(
            "status IN ('planned', 'active', 'paused', 'review_required', 'complete', "
            "'failed', 'quarantined', 'cancelled')",
            name="ck_process_rollout_status",
        ),
        Index("ix_process_rollout_claim", "status", "lease_expires_at"),
        Index("ix_process_rollout_instance", "instance_id", "replication_index"),
    )

    rollout_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    execution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_executions.execution_digest", ondelete="RESTRICT"), nullable=False
    )
    program_digest: Mapped[str] = mapped_column(
        ForeignKey("process_programs.program_digest", ondelete="RESTRICT"), nullable=False
    )
    distribution_digest: Mapped[str] = mapped_column(
        ForeignKey("process_distributions.distribution_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("project_instances.instance_id", ondelete="RESTRICT"), nullable=False
    )
    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    replication_index: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_state_id: Mapped[str] = mapped_column(String(192), nullable=False)
    current_state_id: Mapped[str] = mapped_column(String(192), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_rollout_id: Mapped[str | None] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT")
    )
    fork_id: Mapped[str | None] = mapped_column(String(192))
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(192))
    lease_token: Mapped[str | None] = mapped_column(String(192), unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessStateRow(Base):
    __tablename__ = "process_states"
    __table_args__ = (
        UniqueConstraint("rollout_id", "sequence", name="uq_process_state_sequence"),
        Index("ix_process_state_lineage", "rollout_id", "parent_state_id"),
    )

    state_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_state_id: Mapped[str | None] = mapped_column(
        ForeignKey("process_states.state_id", ondelete="RESTRICT")
    )
    triggering_event_id: Mapped[str | None] = mapped_column(String(192), unique=True)
    state_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessEventRow(Base):
    __tablename__ = "process_events"
    __table_args__ = (
        UniqueConstraint("rollout_id", "sequence", name="uq_process_event_sequence"),
        UniqueConstraint("resulting_state_id", name="uq_process_event_resulting_state"),
        Index("ix_process_event_kind", "kind", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(192), nullable=False)
    parent_state_id: Mapped[str] = mapped_column(
        ForeignKey("process_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    resulting_state_id: Mapped[str] = mapped_column(
        ForeignKey("process_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    worker_invocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("process_worker_invocations.invocation_id", ondelete="RESTRICT")
    )
    research_execution_digest: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT")
    )
    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    amber_decision_id: Mapped[str] = mapped_column(
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    event_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessForkRow(Base):
    __tablename__ = "process_forks"

    fork_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    parent_rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    parent_state_id: Mapped[str] = mapped_column(
        ForeignKey("process_states.state_id", ondelete="RESTRICT"), nullable=False
    )
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessForkChildRow(Base):
    __tablename__ = "process_fork_children"
    __table_args__ = (
        UniqueConstraint("fork_id", "condition_id", name="uq_process_fork_condition"),
        UniqueConstraint("rollout_id", name="uq_process_fork_child_rollout"),
    )

    fork_id: Mapped[str] = mapped_column(
        ForeignKey("process_forks.fork_id", ondelete="RESTRICT"), primary_key=True
    )
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), primary_key=True
    )
    condition_id: Mapped[str] = mapped_column(String(192), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)


class ProcessOutcomeRow(Base):
    __tablename__ = "process_outcomes"
    __table_args__ = (Index("ix_process_outcome_rollout", "rollout_id", "created_at"),)

    assessment_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    authority_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    eligible_for_learning: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scalar_return: Mapped[float | None] = mapped_column(Float)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessTrainingEligibilityRow(Base):
    __tablename__ = "process_training_eligibility"
    __table_args__ = (
        Index("ix_process_training_eligibility", "rollout_id", "eligible", "created_at"),
    )

    decision_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    policy_id: Mapped[str] = mapped_column(String(192), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessWorkerInvocationRow(Base):
    __tablename__ = "process_worker_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_process_worker_invocation_status",
        ),
        Index("ix_process_worker_invocation_rollout", "rollout_id", "created_at"),
        UniqueConstraint("amber_decision_id", name="uq_process_worker_invocation_admission"),
    )

    invocation_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(String(192), nullable=False)
    worker_model_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    amber_decision_id: Mapped[str] = mapped_column(
        ForeignKey("amber_admission_decisions.decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    research_execution_digest: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.execution_digest", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    response_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT")
    )
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    workload_digest: Mapped[str | None] = mapped_column(String(71))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AmberAdmissionDecisionRow(Base):
    __tablename__ = "amber_admission_decisions"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('admitted', 'denied', 'review_required')",
            name="ck_amber_admission_disposition",
        ),
        Index("ix_amber_admission_rollout", "rollout_id", "decided_at"),
    )

    decision_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    authorization_digest: Mapped[str] = mapped_column(
        ForeignKey("amber_authorizations.authorization_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    authorization_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    rollout_id: Mapped[str] = mapped_column(
        ForeignKey("process_rollouts.rollout_id", ondelete="RESTRICT"), nullable=False
    )
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _immutable(_mapper: Any, _connection: Any, target: Any) -> None:
    raise ValueError(f"{type(target).__name__} is immutable")


for _immutable_type in (
    StudentStateRow,
    ArtifactRow,
    ArtifactInformationRow,
    ProcessEvidenceAdmissionRow,
    ProcessContentAdmissionRow,
    ProcessObservationRow,
    ProcessObservationDecisionRow,
    ProcessGenerationWorkloadRow,
    ProcessContainerWorkloadRow,
    ProcessContainerReceiptRow,
    ProcessResourceGrantRow,
    ProcessResourceReservationRow,
    ProcessResourceEventRow,
    ProcessTrainingProjectionRow,
    ProvenanceEventRow,
    TrainingSourceDocumentRow,
    TrainingSourceDecisionRow,
    AuthoredDemonstrationRow,
    TrainingBundleRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    OperationSpanEventRow,
    DurationProfileRow,
    AtlasBenchmarkClaimRow,
    AtlasDatasetGovernanceRow,
    AtlasItemRow,
    AtlasSuiteRow,
    AtlasSuiteItemRow,
    AtlasOntologyRow,
    AtlasCampaignRow,
    AtlasCampaignClaimRow,
    AtlasCampaignConditionRow,
    AtlasCampaignSuiteRow,
    AtlasCampaignSuiteConditionRow,
    AtlasCampaignExecutionBindingRow,
    AtlasAllocationRow,
    AtlasRunManifestRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    AtlasPhenomenonRow,
    AtlasProbeSetRow,
    AtlasProbeItemRow,
    AtlasProbeResultRow,
    AtlasFailureClusterRow,
    AtlasFailureClusterResultRow,
    AtlasSnapshotRow,
    AtlasComparisonRow,
    AtlasExploratoryProposalRow,
    AtlasExploratoryReproductionRow,
    AtlasExploratoryReproductionResultRow,
    AtlasChallengeAdmissionRow,
    AtlasTrainingEligibilityRow,
    AtlasMemoryEligibilityRow,
    ProcessDistributionRow,
    ProcessProgramRow,
    AmberAuthorizationRow,
    AmberAuthorizationEventRow,
    ProjectInstanceRow,
    ProcessExecutionRow,
    ProcessStateRow,
    ProcessEventRow,
    ProcessForkRow,
    ProcessForkChildRow,
    ProcessOutcomeRow,
    ProcessTrainingEligibilityRow,
    AmberAdmissionDecisionRow,
):
    event.listen(_immutable_type, "before_update", _immutable)
    event.listen(_immutable_type, "before_delete", _immutable)
