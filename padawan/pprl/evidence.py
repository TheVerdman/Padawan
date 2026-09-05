"""Reviewed evidence admission and narrowly scoped reads in the trusted broker.

No worker receives this service object or its privileged inspection methods.
Identity authentication and an external containment boundary are separate gates.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import (
    ArtifactInformationStore,
    InformationClass,
    ProcessArtifactRef,
)
from padawan.artifacts.store import ArtifactCatalog, ArtifactIntegrityError, artifact_read_bytes
from padawan.governance.amber import AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import ArtifactRef, RightsUse
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ProcessDistributionRow,
    ProcessEvidenceAdmissionRow,
    ProcessExecutionRow,
    ProcessRolloutRow,
    ProcessWorkerInvocationRow,
    ProjectInstanceRow,
)
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessExecutionManifest,
    ProjectInstance,
    ProjectSplit,
)
from padawan.pprl.evidence_contracts import (
    EvidenceAdmissionPolicy,
    ProcessEvidenceAdmission,
    ProcessEvidenceAdmissionRecord,
    ProcessEvidenceUse,
)


class ProcessEvidenceReadDeniedError(PermissionError):
    """Uniform worker-facing failure with no privileged exception payload."""


class ProcessEvidenceStore:
    def __init__(
        self,
        *,
        catalog: ArtifactCatalog,
        amber: AmberStore,
        policy: EvidenceAdmissionPolicy,
    ) -> None:
        self.catalog = catalog
        self.information = ArtifactInformationStore(catalog)
        self.amber = amber
        # Revalidate frozen configuration rather than trusting model_copy updates.
        self.policy = EvidenceAdmissionPolicy.model_validate_json(policy.model_dump_json())

    async def admit(
        self,
        session: AsyncSession,
        *,
        review: ProcessEvidenceAdmission,
        now: datetime | None = None,
    ) -> ProcessArtifactRef:
        review = ProcessEvidenceAdmission.model_validate_json(review.model_dump_json())
        timestamp = _timestamp(now)
        # An exception caught by the outer transaction must not commit partial pins.
        async with session.begin_nested():
            candidate, authorization_sequence = await self._validate(
                session, review=review, now=timestamp
            )
            existing = await session.get(
                ProcessEvidenceAdmissionRow, review.process_reference.process_artifact_id
            )
            if existing is not None:
                receipt = _admission_record(existing)
                if receipt.review != review:
                    raise ArtifactIntegrityError(
                        "process evidence ID was reused for another review"
                    )
                if timestamp < receipt.admitted_at:
                    raise PermissionError("evidence retry predates the recorded admission")
                await _validate_pins(session, review)
                return review.process_reference
            references = (candidate, *(source.artifact for source in review.forensic_sources))
            for reference in references:
                await self.catalog.reference(
                    session,
                    reference,
                    owner_type="process_evidence_admission",
                    owner_id=review.process_reference.process_artifact_id,
                )
            receipt = ProcessEvidenceAdmissionRecord(
                review=review, authorization_sequence=authorization_sequence, admitted_at=timestamp
            )
            session.add(
                ProcessEvidenceAdmissionRow(
                    process_artifact_id=review.process_reference.process_artifact_id,
                    execution_digest=review.process_reference.execution_digest,
                    candidate_classification_digest=review.candidate_classification_digest,
                    policy_digest=review.policy_digest,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                    created_at=timestamp,
                )
            )
            await session.flush()
        return review.process_reference

    async def inspect_admission(
        self, session: AsyncSession, *, process_artifact_id: str
    ) -> ProcessEvidenceAdmissionRecord:
        """Privileged reconstruction only; never use as a worker projection."""
        row = await session.get(ProcessEvidenceAdmissionRow, process_artifact_id)
        if row is None:
            raise PermissionError("process evidence has no reviewed admission")
        return _admission_record(row)

    async def read(
        self,
        session: AsyncSession,
        *,
        reference: ProcessArtifactRef,
        execution_digest: str,
        use: ProcessEvidenceUse,
        now: datetime | None = None,
    ) -> bytes:
        """Return only admitted bytes, under broker-supplied execution/use authority."""
        cancelled = False
        try:
            return await self._read(
                session, reference=reference, execution_digest=execution_digest, use=use, now=now
            )
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # Validation and backend exceptions can embed privileged records or
            # physical paths. Keep their payloads out of the worker-facing result.
            pass
        # Raise outside the handler so even __context__ contains no private error.
        if cancelled:
            raise asyncio.CancelledError("process evidence read cancelled")
        raise ProcessEvidenceReadDeniedError("process evidence read denied")

    async def _read(
        self,
        session: AsyncSession,
        *,
        reference: ProcessArtifactRef,
        execution_digest: str,
        use: ProcessEvidenceUse,
        now: datetime | None,
    ) -> bytes:
        _receipt, candidate = await self._resolve(
            session, reference=reference, execution_digest=execution_digest, use=use, now=now
        )
        return await artifact_read_bytes(self.catalog.backend, candidate, allow_restricted=True)

    async def retain_for_process(
        self,
        session: AsyncSession,
        *,
        reference: ProcessArtifactRef,
        execution_digest: str,
        owner_type: Literal[
            "process_state", "process_event", "process_observation", "process_training_projection"
        ],
        owner_id: str,
        now: datetime,
        use: ProcessEvidenceUse = ProcessEvidenceUse.PROCESS,
    ) -> None:
        """Broker-only ownership; do not expose the resolved dependencies to workers."""
        if (
            owner_type
            not in {
                "process_state",
                "process_event",
                "process_observation",
                "process_training_projection",
            }
            or not owner_id.strip()
        ):
            raise ValueError("process evidence requires a concrete process record owner")
        expected_use = (
            ProcessEvidenceUse.TRAINING_PROJECTION
            if owner_type == "process_training_projection"
            else ProcessEvidenceUse.PROCESS
        )
        if use != expected_use:
            raise ValueError("process ownership purpose differs from its declared use")
        async with session.begin_nested():
            receipt, candidate = await self._resolve(
                session,
                reference=reference,
                execution_digest=execution_digest,
                use=use,
                now=now,
            )
            for artifact in (
                candidate,
                *(source.artifact for source in receipt.review.forensic_sources),
            ):
                await self.catalog.reference(
                    session, artifact, owner_type=owner_type, owner_id=owner_id
                )

    async def validate_process_ownership(
        self,
        session: AsyncSession,
        *,
        references: tuple[ProcessArtifactRef, ...],
        execution_digest: str,
        owner_type: Literal[
            "process_state", "process_event", "process_observation", "process_training_projection"
        ],
        owner_id: str,
        now: datetime,
        use: ProcessEvidenceUse = ProcessEvidenceUse.PROCESS,
    ) -> None:
        """Broker-only check of the full private dependency set for one process record."""
        if (
            owner_type
            not in {
                "process_state",
                "process_event",
                "process_observation",
                "process_training_projection",
            }
            or not owner_id.strip()
            or not isinstance(use, ProcessEvidenceUse)
            or (
                owner_type == "process_training_projection"
                and use != ProcessEvidenceUse.TRAINING_PROJECTION
            )
        ):
            raise ValueError("process ownership validation requires a concrete owner and use")
        expected = set()
        for reference in references:
            receipt, candidate = await self._resolve(
                session,
                reference=reference,
                execution_digest=execution_digest,
                use=use,
                now=now,
            )
            expected.add(candidate.artifact_id)
            expected.update(
                source.artifact.artifact_id for source in receipt.review.forensic_sources
            )
        retained = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == owner_type,
                    ArtifactReferenceRow.owner_id == owner_id,
                )
            )
        )
        if retained != expected:
            raise ArtifactIntegrityError("process record has inconsistent retention ownership")

    async def _resolve(
        self,
        session: AsyncSession,
        *,
        reference: ProcessArtifactRef,
        execution_digest: str,
        use: ProcessEvidenceUse,
        now: datetime | None,
    ) -> tuple[ProcessEvidenceAdmissionRecord, ArtifactRef]:
        if not isinstance(reference, ProcessArtifactRef) or not isinstance(use, ProcessEvidenceUse):
            raise PermissionError("process reads require a process reference and explicit use")
        if reference.execution_digest != execution_digest:
            raise PermissionError("process evidence belongs to another execution")
        receipt = await self.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        timestamp = _timestamp(now)
        if timestamp < receipt.admitted_at:
            raise PermissionError("evidence read predates its admission")
        review = receipt.review
        if review.process_reference != reference or use not in review.allowed_uses:
            raise PermissionError("process reference or requested use differs from admission")
        candidate, _ = await self._validate(session, review=review, now=timestamp, read_use=use)
        await _validate_pins(session, review)
        return receipt, candidate

    async def _validate(
        self,
        session: AsyncSession,
        *,
        review: ProcessEvidenceAdmission,
        now: datetime,
        read_use: ProcessEvidenceUse | None = None,
    ) -> tuple[ArtifactRef, int]:
        if review.policy_digest != self.policy.digest:
            raise PermissionError("evidence review differs from the pinned admission policy")
        if review.reviewer_id not in self.policy.reviewer_ids or review.reviewed_at > now:
            raise PermissionError("evidence review lacks current declared reviewer authority")
        if review.process_reference.size_bytes > self.policy.maximum_bytes:
            raise PermissionError("reviewed evidence exceeds the admission size bound")
        if (
            len(review.forensic_sources) > self.policy.maximum_forensic_sources
            or sum(source.artifact.size_bytes for source in review.forensic_sources)
            > self.policy.maximum_forensic_source_bytes
        ):
            raise PermissionError("reviewed evidence exceeds the forensic provenance bounds")
        if not review.rights.permits(RightsUse.INTERNAL_RESEARCH) or not review.rights.permits(
            RightsUse.EVIDENCE_RETENTION
        ):
            raise PermissionError("reviewed evidence lacks retention and research rights")
        if review.rights.reviewed_at is None or review.rights.reviewed_at > review.reviewed_at:
            raise PermissionError("evidence admission predates its rights review")

        execution_row = await session.get(
            ProcessExecutionRow, review.process_reference.execution_digest
        )
        if execution_row is None:
            raise PermissionError("evidence admission requires a registered execution")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        if (
            sha256_digest(execution_row.record_json) != execution_row.execution_digest
            or sha256_digest(execution) != execution_row.execution_digest
        ):
            raise ArtifactIntegrityError("evidence admission found a corrupted execution")
        envelope = await self.amber.get(
            session, authorization_digest=execution.amber_authorization_digest
        )
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(
                AmberAuthorizationHeadRow.authorization_digest
                == execution.amber_authorization_digest
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        allowed_statuses = {AmberStatus.ACTIVE.value}
        if read_use == ProcessEvidenceUse.TRAINING_PROJECTION:
            allowed_statuses.update({AmberStatus.PAUSED.value, AmberStatus.RELEASE_APPROVED.value})
        if (
            head is None
            or head.status not in allowed_statuses
            or not _utc(head.updated_at) <= now < envelope.expires_at
            or review.reviewed_at < envelope.created_at
            or review.reviewed_at < execution.created_at
            or review.reviewer_id not in envelope.required_reviewers
        ):
            raise PermissionError("evidence admission requires current Amber review authority")
        instance_row = await session.scalar(
            select(ProjectInstanceRow).where(
                ProjectInstanceRow.instance_digest == execution.instance_digest
            )
        )
        distribution_row = await session.get(ProcessDistributionRow, execution.distribution_digest)
        if instance_row is None or distribution_row is None:
            raise ArtifactIntegrityError("evidence admission lost its distribution or instance")
        instance = ProjectInstance.model_validate(instance_row.record_json, strict=False)
        distribution = ProcessDistributionManifest.model_validate(
            distribution_row.record_json, strict=False
        )
        if (
            sha256_digest(instance_row.record_json) != execution.instance_digest
            or sha256_digest(distribution_row.record_json) != execution.distribution_digest
            or envelope.program_digest != execution.program_digest
            or envelope.distribution_digest != execution.distribution_digest
            or sha256_digest(instance) != execution.instance_digest
            or sha256_digest(distribution) != execution.distribution_digest
            or instance.distribution_digest != execution.distribution_digest
            or instance.split not in envelope.allowed_splits
        ):
            raise ArtifactIntegrityError("evidence admission has inconsistent experiment identity")
        partitions = [
            partition for partition in distribution.partitions if partition.split == instance.split
        ]
        if len(partitions) != 1 or partitions[0].contamination_scope != review.contamination_scope:
            raise PermissionError("evidence admission differs from its contamination partition")
        if ProcessEvidenceUse.TRAINING_PROJECTION in review.allowed_uses and (
            instance.split != ProjectSplit.TRAIN
            or not envelope.checkpoint_policy.training_permitted
            or not review.rights.permits(RightsUse.PROCESS)
            or not execution.output_rights.permits(RightsUse.PROCESS)
            or not distribution.rights.permits(RightsUse.PROCESS)
        ):
            raise PermissionError("evidence has no process-training projection authority")

        classification = await self.information.get(
            session, artifact_id=review.candidate_artifact_id
        )
        candidate = classification.artifact
        if (
            classification.information_class != InformationClass.PROCESS_CANDIDATE
            or classification.digest != review.candidate_classification_digest
            or classification.classified_at > review.reviewed_at
            or candidate.digest != review.process_reference.content_digest
            or candidate.media_type != review.process_reference.media_type
            or candidate.size_bytes != review.process_reference.size_bytes
        ):
            raise PermissionError("review does not bind an exact classified process candidate")
        for source in review.forensic_sources:
            actual = await self.information.forensic_reference(
                session, artifact_id=source.artifact.artifact_id
            )
            if actual != source:
                raise PermissionError("evidence review cites inconsistent forensic provenance")
            source_classification = await self.information.get(
                session, artifact_id=source.artifact.artifact_id
            )
            if source_classification.classified_at > review.reviewed_at:
                raise PermissionError("evidence review predates its forensic classification")
            source_invocation = await session.scalar(
                select(ProcessWorkerInvocationRow.invocation_id)
                .join(
                    ProcessRolloutRow,
                    ProcessRolloutRow.rollout_id == ProcessWorkerInvocationRow.rollout_id,
                )
                .where(
                    ProcessRolloutRow.execution_digest == execution_row.execution_digest,
                    ProcessWorkerInvocationRow.status == "completed",
                    ProcessWorkerInvocationRow.completed_at <= review.reviewed_at,
                    or_(
                        ProcessWorkerInvocationRow.request_artifact_id
                        == source.artifact.artifact_id,
                        ProcessWorkerInvocationRow.response_artifact_id
                        == source.artifact.artifact_id,
                    ),
                )
                .limit(1)
            )
            if source_invocation is None:
                raise PermissionError(
                    "forensic evidence lacks a completed source in this execution"
                )
        if review.forensic_sources:
            content = await artifact_read_bytes(
                self.catalog.backend, candidate, allow_restricted=True
            )
            for source in review.forensic_sources:
                identifiers = (
                    source.artifact.artifact_id,
                    source.artifact.uri,
                    source.artifact.digest,
                    source.artifact.digest.removeprefix("sha256:"),
                    source.classification_digest,
                )
                if any(identifier.encode("utf-8") in content for identifier in identifiers):
                    raise PermissionError("process evidence contains a forensic source identifier")
        return candidate, head.sequence


def _admission_record(row: ProcessEvidenceAdmissionRow) -> ProcessEvidenceAdmissionRecord:
    record = ProcessEvidenceAdmissionRecord.model_validate(row.record_json, strict=False)
    review = record.review
    if (
        sha256_digest(row.record_json) != row.record_digest
        or record.digest != row.record_digest
        or review.process_reference.process_artifact_id != row.process_artifact_id
        or review.process_reference.execution_digest != row.execution_digest
        or review.candidate_classification_digest != row.candidate_classification_digest
        or review.policy_digest != row.policy_digest
        or record.admitted_at != _utc(row.created_at)
    ):
        raise ArtifactIntegrityError("stored process evidence admission is inconsistent")
    return record


async def _validate_pins(session: AsyncSession, review: ProcessEvidenceAdmission) -> None:
    retained = set(
        await session.scalars(
            select(ArtifactReferenceRow.artifact_id).where(
                ArtifactReferenceRow.owner_type == "process_evidence_admission",
                ArtifactReferenceRow.owner_id == review.process_reference.process_artifact_id,
            )
        )
    )
    expected = {
        review.candidate_artifact_id,
        *(source.artifact.artifact_id for source in review.forensic_sources),
    }
    if retained != expected:
        raise ArtifactIntegrityError(
            "process evidence admission has inconsistent retention ownership"
        )


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("evidence operation time must be timezone-aware")
    return timestamp.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
