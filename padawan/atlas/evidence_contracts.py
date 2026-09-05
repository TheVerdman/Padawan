"""Privileged, explicitly reviewed Atlas-to-process disclosure contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, SourceRights, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.evidence_contracts import ProcessEvidenceAdmission, ProcessEvidenceUse


class AtlasEvidenceSourceScope(StrictRecord):
    campaign_digest: Sha256
    condition_id: NonEmpty
    suite_digest: Sha256
    research_execution_digest: Sha256
    output_rights: SourceRights

    @property
    def coordinates(self) -> tuple[str, str, str, str]:
        return (
            self.campaign_digest,
            self.condition_id,
            self.suite_digest,
            self.research_execution_digest,
        )


class AtlasEvidenceDisclosurePolicy(StrictRecord):
    """Trusted composition input; an Atlas record cannot grant itself disclosure."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    target_execution_digest: Sha256
    target_contamination_scope: NonEmpty
    intervention_description: NonEmpty
    reviewer_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1, max_length=16)]
    source_scopes: Annotated[
        tuple[AtlasEvidenceSourceScope, ...], Field(min_length=1, max_length=16)
    ]
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def scope_is_explicit(self) -> AtlasEvidenceDisclosurePolicy:
        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.created_at
        ):
            raise ValueError("Atlas disclosure needs an explicit timezone-aware validity period")
        if tuple(sorted(set(self.reviewer_ids))) != self.reviewer_ids:
            raise ValueError("Atlas disclosure reviewers must be unique and canonical")
        scopes = tuple(scope.coordinates for scope in self.source_scopes)
        if tuple(sorted(set(scopes))) != scopes:
            raise ValueError("Atlas disclosure source scopes must be unique and canonical")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class AtlasTrialEvidenceSource(StrictRecord):
    """Native IDs/digests for privileged reconstruction, never a worker reference."""

    result_id: NonEmpty
    result_digest: Sha256
    result_record_digest: Sha256
    request_id: NonEmpty
    request_record_digest: Sha256
    context_digest: Sha256


class AtlasEvidenceOriginReview(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    candidate_review_digest: Sha256
    disclosure_policy: AtlasEvidenceDisclosurePolicy
    policy_digest: Sha256
    trials: Annotated[tuple[AtlasTrialEvidenceSource, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def origin_is_canonical(self) -> AtlasEvidenceOriginReview:
        if self.disclosure_policy.digest != self.policy_digest:
            raise ValueError("Atlas origin must retain its exact disclosure policy")
        identities = tuple(trial.result_id for trial in self.trials)
        if tuple(sorted(set(identities))) != identities:
            raise ValueError("Atlas origin trials must be unique and canonical")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class AtlasProcessEvidenceAdmissionRecord(StrictRecord):
    """Version 2 retains an explicit origin without rewriting version-1 receipt bytes."""

    schema_version: Literal["2.0.0"] = "2.0.0"
    review: ProcessEvidenceAdmission
    atlas_origin: AtlasEvidenceOriginReview
    authorization_sequence: Annotated[int, Field(ge=0)]
    admitted_at: datetime

    @model_validator(mode="after")
    def origin_binds_review(self) -> AtlasProcessEvidenceAdmissionRecord:
        if self.atlas_origin.candidate_review_digest != self.review.digest:
            raise ValueError("Atlas origin substitutes the exact reviewed candidate")
        if self.review.allowed_uses != (ProcessEvidenceUse.PROCESS,):
            raise ValueError("Atlas derivatives require separate future training materialization")
        if self.admitted_at.tzinfo is None or self.admitted_at < self.review.reviewed_at:
            raise ValueError("Atlas admission must follow its review in timezone-aware time")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
