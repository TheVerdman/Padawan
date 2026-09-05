"""Privileged recovery disclosure records; only the reviewed process reference is public."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.evidence_contracts import ProcessEvidenceAdmission, ProcessEvidenceUse


class RecoveryEvidenceDisclosurePolicy(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    target_execution_digest: Sha256
    target_contamination_scope: NonEmpty
    content_policy_digest: Sha256
    reviewer_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1, max_length=16)]
    maximum_recovery_effects: Annotated[int, Field(ge=1, le=1024)] = 64
    maximum_recovery_source_bytes: Annotated[int, Field(ge=1, le=134_217_728)] = 16_777_216
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def explicit_scope(self) -> "RecoveryEvidenceDisclosurePolicy":
        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.created_at
            or tuple(sorted(set(self.reviewer_ids))) != self.reviewer_ids
        ):
            raise ValueError("recovery disclosure requires canonical reviewers and an aware window")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class RecoveredEffectSource(StrictRecord):
    recovery_id: Annotated[str, Field(pattern=r"^process-recovery-[0-9a-f]{32}$")]
    recovery_digest: Sha256
    decision_id: NonEmpty
    effect_digest: Sha256


class RecoveredEvidenceOriginReview(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    candidate_review_digest: Sha256
    disclosure_policy: RecoveryEvidenceDisclosurePolicy
    policy_digest: Sha256
    source: RecoveredEffectSource

    @model_validator(mode="after")
    def exact_policy(self) -> "RecoveredEvidenceOriginReview":
        if self.disclosure_policy.digest != self.policy_digest:
            raise ValueError("recovery origin must retain its exact disclosure policy")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class RecoveredProcessEvidenceAdmissionRecord(StrictRecord):
    """Version 3 preserves recovery origin without rewriting ordinary or Atlas receipts."""

    schema_version: Literal["3.0.0"] = "3.0.0"
    review: ProcessEvidenceAdmission
    recovery_origin: RecoveredEvidenceOriginReview
    authorization_sequence: Annotated[int, Field(ge=0)]
    admitted_at: datetime

    @model_validator(mode="after")
    def exact_process_use(self) -> "RecoveredProcessEvidenceAdmissionRecord":
        if self.recovery_origin.candidate_review_digest != self.review.digest:
            raise ValueError("recovery origin substitutes its reviewed candidate")
        if self.review.allowed_uses != (ProcessEvidenceUse.PROCESS,):
            raise ValueError(
                "recovered derivatives require separate future training materialization"
            )
        if self.admitted_at.tzinfo is None or self.admitted_at < self.review.reviewed_at:
            raise ValueError("recovered admission must follow its review in aware time")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
