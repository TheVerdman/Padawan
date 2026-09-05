from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.artifacts.information import ForensicArtifactRef, ProcessArtifactRef
from padawan.models.contracts import NonEmpty, Sha256, SourceRights, StrictRecord
from padawan.models.hashing import sha256_digest


class ProcessEvidenceUse(StrEnum):
    PROCESS = "process"
    TRAINING_PROJECTION = "training_projection"


class EvidenceAdmissionPolicy(StrictRecord):
    """Pinned broker configuration, supplied at composition rather than by workers."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    reviewer_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    maximum_bytes: Annotated[int, Field(gt=0)]
    maximum_forensic_sources: Annotated[int, Field(ge=0)]
    maximum_forensic_source_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def reviewers_are_canonical(self) -> EvidenceAdmissionPolicy:
        if tuple(sorted(set(self.reviewer_ids))) != self.reviewer_ids:
            raise ValueError("evidence reviewers must be unique and canonical")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessEvidenceAdmission(StrictRecord):
    """Privileged review receipt. Only process_reference may reach workers.

    Review attests the exact candidate bytes and their full source set. Semantic
    redaction is a review responsibility; byte identity cannot prove it occurred.
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    process_reference: ProcessArtifactRef
    candidate_artifact_id: NonEmpty
    candidate_classification_digest: Sha256
    forensic_sources: tuple[ForensicArtifactRef, ...] = ()
    admission_policy: EvidenceAdmissionPolicy
    policy_digest: Sha256
    reviewer_id: NonEmpty
    rationale: NonEmpty
    contamination_scope: NonEmpty
    rights: SourceRights
    allowed_uses: Annotated[tuple[ProcessEvidenceUse, ...], Field(min_length=1)]
    reviewed_at: datetime

    @model_validator(mode="after")
    def review_is_canonical(self) -> ProcessEvidenceAdmission:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("evidence review time must be timezone-aware")
        if self.admission_policy.digest != self.policy_digest:
            raise ValueError("evidence review must retain the exact admission policy")
        source_ids = tuple(source.artifact.artifact_id for source in self.forensic_sources)
        if tuple(sorted(set(source_ids))) != source_ids:
            raise ValueError("forensic source references must be unique and canonical")
        if self.candidate_artifact_id in source_ids:
            raise ValueError("reviewed evidence must be separate from its forensic sources")
        if tuple(sorted(set(self.allowed_uses))) != self.allowed_uses:
            raise ValueError("evidence uses must be unique and canonical")
        if ProcessEvidenceUse.PROCESS not in self.allowed_uses:
            raise ValueError("a process admission requires process use")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessEvidenceAdmissionRecord(StrictRecord):
    """Broker receipt binding the reviewed request to actual admission time and authority."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    review: ProcessEvidenceAdmission
    authorization_sequence: Annotated[int, Field(ge=0)]
    admitted_at: datetime

    @model_validator(mode="after")
    def admission_follows_review(self) -> ProcessEvidenceAdmissionRecord:
        if self.admitted_at.tzinfo is None or self.admitted_at < self.review.reviewed_at:
            raise ValueError("admission time must be timezone-aware and follow review")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
