from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
            "checkpoint_id", "study_id", "suite_manifest_digest", name="uq_checkpoint_evaluation"
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

    request_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    response_artifact_id: Mapped[str | None] = mapped_column(String(96))
    provider_response_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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


def _immutable(_mapper: Any, _connection: Any, target: Any) -> None:
    raise ValueError(f"{type(target).__name__} is immutable")


for _immutable_type in (
    StudentStateRow,
    ArtifactRow,
    ProvenanceEventRow,
    TrainingSourceDocumentRow,
    TrainingSourceDecisionRow,
    AuthoredDemonstrationRow,
    TrainingBundleRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    OperationSpanEventRow,
    DurationProfileRow,
):
    event.listen(_immutable_type, "before_update", _immutable)
    event.listen(_immutable_type, "before_delete", _immutable)
