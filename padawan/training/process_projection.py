"""Explicit PPRL learning projection from a privileged research archive.

The broker retains source identities and authority separately. Consumers receive
only validated public JSONL; this is not a trainer or execution authorization.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Concatenate, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import (
    ArtifactInformationStore,
    ForensicArtifactRef,
    InformationClass,
    ProcessArtifactRef,
)
from padawan.artifacts.store import ArtifactCatalog, artifact_read_bytes
from padawan.governance.amber import AmberStatus
from padawan.models.contracts import ArtifactRef, RightsUse, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ArtifactRow,
    ProcessContentAdmissionRow,
    ProcessExecutionRow,
    ProcessRolloutRow,
    ProcessTrainingProjectionRow,
    ProcessWorkerInvocationRow,
    TrainingBundleRow,
)
from padawan.pprl.content import _closed_schema
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProcessEventRecord,
    ProcessExecutionManifest,
    ProcessOutcomeAssessment,
    ProjectSplit,
    ProjectStateVersion,
    RewardAuthorityKind,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.evidence_contracts import ProcessEvidenceUse
from padawan.pprl.observations import ProcessObservationStore, _public_observation
from padawan.pprl.store import (
    ProcessStore,
    _invocation_forensic_artifact_ids,
    _invocation_forensic_bytes,
)
from padawan.training.compiler import TrainingCompiler, _artifact_ref
from padawan.training.contracts import (
    PPRLForkPreferenceTrainingRow,
    PPRLTrajectoryTrainingRow,
    PPRLVerifiableTrainingRow,
    TrainingBundleManifest,
    TrainingProductKind,
)
from padawan.training.pprl import compile_pprl_snapshot
from padawan.training.process_projection_contracts import (
    ProcessForkPreferencePayload,
    ProcessLearningOutcome,
    ProcessLearningOutcomeComponent,
    ProcessLearningStep,
    ProcessProjectionExclusion,
    ProcessProjectionKind,
    ProcessProjectionProduct,
    ProcessProjectionRowLink,
    ProcessProjectionSource,
    ProcessTrainingProjectionPolicy,
    ProcessTrainingProjectionReceipt,
    ProcessTrainingTaskSchema,
    ProcessTrajectoryPayload,
    ProcessVerifiablePayload,
)

_KINDS: tuple[ProcessProjectionKind, ...] = (
    "pprl_fork_preference",
    "pprl_trajectory",
    "pprl_verifiable",
)
_TRAINING_STATUSES = {
    AmberStatus.ACTIVE.value,
    AmberStatus.PAUSED.value,
    AmberStatus.RELEASE_APPROVED.value,
}


class ProcessTrainingProjectionDeniedError(PermissionError):
    """Uniform public failure; privileged source validation errors are not returned."""


@dataclass(frozen=True)
class RegisteredProcessTrainingTask:
    generator_digest: str
    namespace: str
    version: str
    model: type[StrictRecord]


@dataclass(frozen=True)
class _Candidate:
    source: ProcessProjectionSource
    payload: ProcessTrajectoryPayload
    generator_digest: str
    minimum_instances: int
    minimum_replicates: int


type _ProjectedRow = tuple[StrictRecord, tuple[str, ...], str]


def _guard_projection[**P, R](
    operation: Callable[Concatenate[ProcessTrainingProjectionStore, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[ProcessTrainingProjectionStore, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(
        self: ProcessTrainingProjectionStore,
        session: AsyncSession,
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        cancelled = False
        try:
            return await operation(self, session, *args, **kwargs)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            pass
        if cancelled:
            raise asyncio.CancelledError("process training projection cancelled")
        raise ProcessTrainingProjectionDeniedError("process training projection denied")

    return wrapped


class ProcessTrainingProjectionStore:
    def __init__(
        self,
        *,
        compiler: TrainingCompiler,
        process: ProcessStore,
        tasks: tuple[RegisteredProcessTrainingTask, ...] = (),
        policy: ProcessTrainingProjectionPolicy | None = None,
    ) -> None:
        self.compiler = compiler
        self.process = process
        self.catalog: ArtifactCatalog = compiler.catalog
        if (
            process.evidence is not None
            and process.evidence.catalog.backend is not compiler.backend
        ):
            raise ValueError("projection and evidence must share their owned artifact backend")
        self.information = ArtifactInformationStore(self.catalog)
        self.observations = ProcessObservationStore(process)
        self._tasks: dict[str, type[StrictRecord]] = {}
        schemas = []
        for task in tasks:
            if task.generator_digest in self._tasks or not issubclass(task.model, StrictRecord):
                raise ValueError("task registration requires one closed schema per generator")
            schema = task.model.model_json_schema()
            _closed_schema(schema, schema, ())
            schemas.append(
                ProcessTrainingTaskSchema(
                    generator_digest=task.generator_digest,
                    namespace=task.namespace,
                    version=task.version,
                    json_schema=schema,
                    schema_digest=sha256_digest(schema),
                )
            )
            self._tasks[task.generator_digest] = task.model
        public_schema_digest = sha256_digest(
            {
                "trajectory": ProcessTrajectoryPayload.model_json_schema(),
                "verifiable": ProcessVerifiablePayload.model_json_schema(),
                "fork_preference": ProcessForkPreferencePayload.model_json_schema(),
            }
        )
        expected_schemas = tuple(sorted(schemas, key=lambda item: item.generator_digest))
        configured = policy or ProcessTrainingProjectionPolicy(
            policy_id="padawan.institutional-training-projection",
            version="1.0.0",
            content_policy_digest=process.content.policy.digest,
            observation_policy_digest=self.observations.policy.digest,
            public_schema_digest=public_schema_digest,
            task_schemas=expected_schemas,
        )
        self._policy = ProcessTrainingProjectionPolicy.model_validate_json(
            configured.model_dump_json()
        )
        if (
            self._policy.content_policy_digest != process.content.policy.digest
            or self._policy.observation_policy_digest != self.observations.policy.digest
            or self._policy.public_schema_digest != public_schema_digest
            or self._policy.task_schemas != expected_schemas
        ):
            raise ValueError("projection policy differs from broker configuration")

    @property
    def policy(self) -> ProcessTrainingProjectionPolicy:
        return self._policy.model_copy(deep=True)

    @_guard_projection
    async def compile(
        self, session: AsyncSession, *, source_bundle_id: str, now: datetime | None = None
    ) -> ProcessTrainingProjectionReceipt:
        """Broker-only result. Never return this private receipt to a learner."""
        timestamp = _timestamp(now)
        async with session.begin_nested():
            receipt = await self._collect(session, source_bundle_id=source_bundle_id, now=timestamp)
            existing = await session.get(ProcessTrainingProjectionRow, receipt.projection_id)
            if existing is not None:
                stored = _receipt(existing)
                _same_projection(stored, receipt)
                await self._validate_retention(session, stored, now=timestamp)
                return stored
            for artifact in receipt.archive_artifacts:
                # Retaining an archive as forensic does not admit its content for learning.
                await self.information.classify(
                    session,
                    artifact=artifact,
                    information_class=InformationClass.FORENSIC,
                    classified_by="padawan.training-archive",
                    reason="Privileged source archive; public projection is separately validated.",
                    classified_at=timestamp,
                )
                await self.catalog.reference(
                    session,
                    artifact,
                    owner_type="process_training_archive",
                    owner_id=receipt.projection_id,
                )
            for source in receipt.sources:
                owner = _evidence_owner(receipt.projection_id, source.execution_digest)
                for reference in source.artifact_refs:
                    assert self.process.evidence is not None
                    await self.process.evidence.retain_for_process(
                        session,
                        reference=reference,
                        execution_digest=source.execution_digest,
                        owner_type="process_training_projection",
                        owner_id=owner,
                        now=timestamp,
                        use=ProcessEvidenceUse.TRAINING_PROJECTION,
                    )
                for forensic_reference in source.forensic_artifacts:
                    await self.catalog.reference(
                        session,
                        forensic_reference.artifact,
                        owner_type="process_training_forensic",
                        owner_id=receipt.projection_id,
                    )
            session.add(
                ProcessTrainingProjectionRow(
                    projection_id=receipt.projection_id,
                    source_bundle_id=source_bundle_id,
                    policy_digest=receipt.policy_digest,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                    created_at=timestamp,
                )
            )
            await session.flush()
            await self._validate_retention(session, receipt, now=timestamp)
            return receipt

    async def inspect_receipt(
        self, session: AsyncSession, *, projection_id: str
    ) -> ProcessTrainingProjectionReceipt:
        """Privileged reconstruction only; no current use or parameter-training authority."""
        row = await session.get(ProcessTrainingProjectionRow, projection_id)
        if row is None:
            raise ValueError("training projection receipt is missing")
        return _receipt(row)

    @_guard_projection
    async def read_product(
        self,
        session: AsyncSession,
        *,
        projection_id: str,
        kind: ProcessProjectionKind,
        now: datetime | None = None,
    ) -> bytes:
        timestamp = _timestamp(now)
        if kind not in _KINDS:
            raise ValueError("unknown training projection product")
        receipt = await self.inspect_receipt(session, projection_id=projection_id)
        current = await self._collect(
            session, source_bundle_id=receipt.source_bundle_id, now=timestamp
        )
        _same_projection(receipt, current)
        await self._validate_retention(session, receipt, now=timestamp)
        return next(
            product.public_jsonl.encode("utf-8")
            for product in receipt.products
            if product.kind == kind
        )

    async def _collect(
        self, session: AsyncSession, *, source_bundle_id: str, now: datetime
    ) -> ProcessTrainingProjectionReceipt:
        if self.process.content.policy.digest != self._policy.content_policy_digest:
            raise ValueError("source content policy changed")
        manifest = await self.compiler.get_manifest(session, bundle_id=source_bundle_id)
        if manifest.invocation.as_of > now:
            raise ValueError("projection predates its archive")
        archive = await self._archive_artifacts(session, manifest)
        if sum(ref.size_bytes for ref in archive) > self._policy.maximum_source_artifact_bytes:
            raise ValueError("source archive exceeds the projection read bound")
        verification = await self.compiler.verify(session, bundle_id=source_bundle_id)
        if not verification.valid:
            raise ValueError("training archive cannot be reconstructed")
        snapshot = await compile_pprl_snapshot(
            session,
            as_of=manifest.invocation.as_of,
            eligibility_policy_id=manifest.invocation.eligibility_policy_id,
            eligibility_policy_version=manifest.invocation.eligibility_policy_version,
        )
        # Archive verification alone checks digests, not product/source equivalence.
        # Reproduce the exact source-derived PPRL rows before using any of them.
        for kind in _KINDS:
            product = next(p for p in manifest.products if p.kind.value == kind)
            rows = sorted(snapshot.products[TrainingProductKind(kind)], key=lambda row: row.row_id)
            expected = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
            if (
                await artifact_read_bytes(
                    self.compiler.backend, product.artifact, allow_restricted=True
                )
                != expected
            ):
                raise ValueError("archive learning rows differ from their retained sources")
        exclusions: list[ProcessProjectionExclusion] = []
        for exclusion in snapshot.exclusions:
            if exclusion.product_kind.value in _KINDS:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=exclusion.candidate_id,
                        kind=exclusion.product_kind.value,
                        reason="archive_ineligible",
                    )
                )
        candidates: dict[str, _Candidate] = {}
        for raw in snapshot.products[TrainingProductKind.PPRL_TRAJECTORY]:
            row = cast(PPRLTrajectoryTrainingRow, raw)
            try:
                candidate = await self._candidate(
                    session, row, as_of=manifest.invocation.as_of, now=now
                )
            except Exception:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=row.rollout_id,
                        kind="pprl_trajectory",
                        reason="projection_not_admitted",
                    )
                )
                continue
            candidates[row.rollout_id] = candidate
        projected: dict[ProcessProjectionKind, list[_ProjectedRow]] = {kind: [] for kind in _KINDS}
        for rollout_id, candidate in sorted(candidates.items()):
            projected["pprl_trajectory"].append(
                (candidate.payload, (rollout_id,), candidate.source.archive_row_digest)
            )
        for raw in snapshot.products[TrainingProductKind.PPRL_VERIFIABLE]:
            verifiable_row = cast(PPRLVerifiableTrainingRow, raw)
            verifiable_candidate = candidates.get(verifiable_row.rollout_id)
            if verifiable_candidate is None:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=verifiable_row.rollout_id,
                        kind="pprl_verifiable",
                        reason="projection_not_admitted",
                    )
                )
                continue
            try:
                task = self._task(verifiable_candidate.generator_digest, verifiable_row.task)
                assessments = tuple(
                    ProcessOutcomeAssessment.model_validate(value, strict=False)
                    for value in verifiable_row.outcome["assessments"]
                )
                if any(
                    value.authority.kind != RewardAuthorityKind.VERIFIABLE for value in assessments
                ):
                    raise ValueError("verifiable projection lost its outcome authority")
                payload = ProcessVerifiablePayload(
                    task=task,
                    steps=verifiable_candidate.payload.steps,
                    outcomes=tuple(_outcome(value) for value in assessments),
                )
                await self._public_content(session, task, references=())
                await self._public_content(
                    session, payload.outcomes, references=verifiable_candidate.source.artifact_refs
                )
            except Exception:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=verifiable_row.rollout_id,
                        kind="pprl_verifiable",
                        reason="task_schema_not_admitted",
                    )
                )
                continue
            projected["pprl_verifiable"].append(
                (payload, (verifiable_row.rollout_id,), sha256_digest(verifiable_row))
            )
        for raw in snapshot.products[TrainingProductKind.PPRL_FORK_PREFERENCE]:
            preference_row = cast(PPRLForkPreferenceTrainingRow, raw)
            chosen, rejected = (
                candidates.get(preference_row.chosen_rollout_id),
                candidates.get(preference_row.rejected_rollout_id),
            )
            if (
                chosen is None
                or rejected is None
                or chosen.payload.initial_state != rejected.payload.initial_state
            ):
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=preference_row.fork_id,
                        kind="pprl_fork_preference",
                        reason="fork_pair_not_admitted",
                    )
                )
                continue
            try:
                chosen_outcome = _outcome(
                    ProcessOutcomeAssessment.model_validate(
                        preference_row.chosen_outcome, strict=False
                    )
                )
                rejected_outcome = _outcome(
                    ProcessOutcomeAssessment.model_validate(
                        preference_row.rejected_outcome, strict=False
                    )
                )
                await self._public_content(
                    session, chosen_outcome, references=chosen.source.artifact_refs
                )
                await self._public_content(
                    session, rejected_outcome, references=rejected.source.artifact_refs
                )
            except Exception:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=preference_row.fork_id,
                        kind="pprl_fork_preference",
                        reason="fork_pair_not_admitted",
                    )
                )
                continue
            projected["pprl_fork_preference"].append(
                (
                    ProcessForkPreferencePayload(
                        chosen=chosen.payload.model_copy(update={"outcomes": (chosen_outcome,)}),
                        rejected=rejected.payload.model_copy(
                            update={"outcomes": (rejected_outcome,)}
                        ),
                    ),
                    tuple(
                        sorted(
                            (preference_row.chosen_rollout_id, preference_row.rejected_rollout_id)
                        )
                    ),
                    sha256_digest(preference_row),
                )
            )
        for kind in _KINDS:
            projected[kind] = _replicated_rows(kind, projected[kind], candidates, exclusions)
        products = tuple(self._product(kind, projected[kind]) for kind in _KINDS)
        included = {
            source_id for rows in projected.values() for _, ids, _ in rows for source_id in ids
        }
        sources = tuple(
            candidate.source
            for source_id, candidate in sorted(candidates.items())
            if source_id in included
        )
        ordered_exclusions = tuple(
            sorted(exclusions, key=lambda row: (row.kind, row.candidate_id, row.reason))
        )
        manifest_row = await session.get(TrainingBundleRow, source_bundle_id)
        assert manifest_row is not None
        identity = {
            "source_manifest_digest": manifest_row.manifest_digest,
            "policy_digest": self._policy.digest,
            "sources": sources,
            "products": products,
            "exclusions": ordered_exclusions,
        }
        return ProcessTrainingProjectionReceipt(
            projection_id=f"process-training-projection-{sha256_digest(identity)[7:]}",
            source_bundle_id=source_bundle_id,
            source_manifest_digest=manifest_row.manifest_digest,
            source_snapshot_digest=manifest.source_snapshot_digest,
            source_as_of=manifest.invocation.as_of,
            created_at=now,
            policy=self._policy,
            policy_digest=self._policy.digest,
            sources=sources,
            products=products,
            exclusions=ordered_exclusions,
            archive_artifacts=archive,
        )

    async def _candidate(
        self,
        session: AsyncSession,
        row: PPRLTrajectoryTrainingRow,
        *,
        as_of: datetime,
        now: datetime,
    ) -> _Candidate:
        if (
            row.split != ProjectSplit.TRAIN.value
            or len(row.events) > self._policy.maximum_rollout_steps
        ):
            raise ValueError("trajectory split or size has no projection authority")
        rollout = await session.get(ProcessRolloutRow, row.rollout_id)
        execution_row = await session.get(ProcessExecutionRow, row.execution_digest)
        if (
            rollout is None
            or execution_row is None
            or rollout.execution_digest != row.execution_digest
        ):
            raise ValueError("trajectory lost its execution")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        if (
            sha256_digest(execution_row.record_json) != row.execution_digest
            or sha256_digest(execution) != row.execution_digest
        ):
            raise ValueError("trajectory execution is inconsistent")
        distribution = await ProcessDistributionRegistry().get_distribution(
            session, distribution_digest=row.distribution_digest
        )
        envelope = await self.process.amber.get(
            session, authorization_digest=execution.amber_authorization_digest
        )
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == envelope.digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            head is None
            or head.status not in _TRAINING_STATUSES
            or not envelope.checkpoint_policy.training_permitted
            or not _utc(head.updated_at) <= now < envelope.expires_at
        ):
            raise ValueError("trajectory lacks current Amber training authority")
        rights = (distribution.rights, execution.output_rights)
        if any(
            not all(
                value.permits(use)
                for use in (
                    RightsUse.PROCESS,
                    RightsUse.INTERNAL_RESEARCH,
                    RightsUse.EVIDENCE_RETENTION,
                )
            )
            or value.reviewed_at is None
            or value.reviewed_at > as_of
            for value in rights
        ):
            raise ValueError("trajectory lacks current source/output rights")
        initial = ProjectStateVersion.model_validate(row.initial_state, strict=False)
        states = [await self.process.get_state(session, state_id=initial.state_id)]
        if states[0] != initial:
            raise ValueError("archive initial state differs from its source")
        content_digests: set[str] = set()
        observation_digests: set[str] = set()
        binding_digests: set[str] = set()
        references: dict[str, ProcessArtifactRef] = {}
        forensic: dict[str, ForensicArtifactRef] = {}
        steps = []
        for raw_event in row.events:
            event = ProcessEventRecord.model_validate(raw_event, strict=False)
            parent = states[-1]
            result = await self.process.get_state(session, state_id=event.resulting_state_id)
            if event.parent_state_id != parent.state_id or event.rollout_id != row.rollout_id:
                raise ValueError("trajectory states are discontinuous")
            await self.process.content.verify_event(
                session, event, execution_digest=row.execution_digest, now=now
            )
            event_references = _process_refs(event.artifact_refs)
            await self._source_ownership(
                session,
                event_references,
                row.execution_digest,
                "process_event",
                event.event_id,
                now,
            )
            content = await session.get(ProcessContentAdmissionRow, ("event", event.event_id))
            assert content is not None
            content_digests.add(content.record_digest)
            binding = await self.observations.inspect_decision_binding(
                session, decision_id=event.amber_decision_id
            )
            observation = await self.observations.inspect_receipt(
                session, observation_id=binding.observation_id
            )
            parent_content = await session.get(
                ProcessContentAdmissionRow, ("state", parent.state_id)
            )
            decision_row = await session.get(AmberAdmissionDecisionRow, event.amber_decision_id)
            if (
                parent_content is None
                or decision_row is None
                or observation.rollout_id != row.rollout_id
                or observation.content_receipt_digest != parent_content.record_digest
                or observation.rights_digests
                != tuple(sorted({sha256_digest(value) for value in rights}))
                or observation.authorization_sequence != decision_row.authorization_sequence
                or observation.state_id != parent.state_id
                or observation.state_digest != parent.state_digest
                or observation.execution_digest != row.execution_digest
                or observation.worker_id != event.actor_id
                or observation.policy_digest != self._policy.observation_policy_digest
                or observation.lease_token_digest != event.lease_token_digest
                or observation.authorization_digest != envelope.digest
                or not observation.created_at <= binding.bound_at <= event.created_at <= as_of
                or observation.observation_json.encode("utf-8")
                != canonical_json_bytes(_public_observation(parent))
            ):
                raise ValueError("trajectory has no exact admitted observation")
            await self._source_ownership(
                session,
                _process_refs(parent.payload.artifact_refs),
                row.execution_digest,
                "process_observation",
                observation.observation_id,
                now,
            )
            observation_digests.add(observation.digest)
            binding_digests.add(binding.digest)
            references.update({ref.process_artifact_id: ref for ref in event_references})
            if event.worker_invocation_id is not None:
                invocation = await session.get(
                    ProcessWorkerInvocationRow, event.worker_invocation_id
                )
                if (
                    invocation is None
                    or invocation.status != "completed"
                    or invocation.rollout_id != row.rollout_id
                    or invocation.amber_decision_id != event.amber_decision_id
                    or invocation.role_id != binding.declared_role_id
                    or invocation.worker_model_digest != binding.declared_worker_model_digest
                ):
                    raise ValueError("trajectory invocation is inconsistent")
                await _invocation_forensic_bytes(session, invocation, event.created_at)
                for artifact_id in await _invocation_forensic_artifact_ids(
                    session, invocation, event.created_at
                ):
                    forensic_source = await self.information.forensic_reference(
                        session, artifact_id=artifact_id
                    )
                    forensic[artifact_id] = forensic_source
            action = event.payload
            if event.kind == ProcessEventKind.ROLLOUT_FORKED:
                action = {"intervention": event.payload["intervention"]}
            steps.append(
                ProcessLearningStep(
                    observation=_public_observation(parent),
                    event_kind=event.kind,
                    public_action=json.loads(canonical_json_bytes(action)),
                    artifact_refs=event_references,
                    resulting_state=_public_observation(result).state,
                )
            )
            states.append(result)
        if states[-1].model_dump(mode="json") != row.terminal_state:
            raise ValueError("archive terminal state differs from its source")
        for state in states:
            if state.created_at > as_of:
                raise ValueError("state postdates its snapshot")
            await self.process.content.verify_state(
                session, state, execution_digest=row.execution_digest, now=now
            )
            state_refs = _process_refs(state.payload.artifact_refs)
            await self._source_ownership(
                session, state_refs, row.execution_digest, "process_state", state.state_id, now
            )
            references.update({ref.process_artifact_id: ref for ref in state_refs})
            content = await session.get(ProcessContentAdmissionRow, ("state", state.state_id))
            assert content is not None
            content_digests.add(content.record_digest)
        all_refs = tuple(references[key] for key in sorted(references))
        evidence_digests = set()
        rights_digests = set(row.rights_digests)
        for reference in all_refs:
            assert self.process.evidence is not None
            admission = await self.process.evidence.inspect_admission(
                session, process_artifact_id=reference.process_artifact_id
            )
            if admission.admitted_at > as_of:
                raise ValueError("training source admission postdates its archive")
            evidence_digests.add(admission.digest)
            rights_digests.add(sha256_digest(admission.review.rights))
        outcomes = tuple(
            _outcome(ProcessOutcomeAssessment.model_validate(value, strict=False))
            for value in row.outcomes
        )
        await self._public_content(session, outcomes, references=all_refs)
        payload = ProcessTrajectoryPayload(
            initial_state=_public_observation(initial).state, steps=tuple(steps), outcomes=outcomes
        )
        source = ProcessProjectionSource(
            rollout_id=row.rollout_id,
            execution_digest=row.execution_digest,
            distribution_digest=row.distribution_digest,
            instance_id=row.instance_id,
            authorization_digest=envelope.digest,
            authorization_sequence=head.sequence,
            archive_row_digest=sha256_digest(row),
            source_record_digests=row.source_record_digests,
            content_receipt_digests=tuple(sorted(content_digests)),
            observation_receipt_digests=tuple(sorted(observation_digests)),
            observation_binding_digests=tuple(sorted(binding_digests)),
            rights_digests=tuple(sorted(rights_digests)),
            evidence_admission_digests=tuple(sorted(evidence_digests)),
            artifact_refs=all_refs,
            forensic_artifacts=tuple(forensic[key] for key in sorted(forensic)),
        )
        return _Candidate(
            source,
            payload,
            distribution.generator.digest,
            distribution.replication.minimum_unique_instances,
            distribution.replication.minimum_rollouts_per_instance,
        )

    async def _source_ownership(
        self,
        session: AsyncSession,
        refs: tuple[ProcessArtifactRef, ...],
        execution_digest: str,
        owner_type: Literal[
            "process_state", "process_event", "process_observation", "process_training_projection"
        ],
        owner_id: str,
        now: datetime,
    ) -> None:
        if self.process.evidence is not None:
            await self.process.evidence.validate_process_ownership(
                session,
                references=refs,
                execution_digest=execution_digest,
                owner_type=owner_type,
                owner_id=owner_id,
                now=now,
                use=ProcessEvidenceUse.TRAINING_PROJECTION,
            )
        elif (
            refs
            or await session.scalar(
                select(ArtifactReferenceRow.reference_id)
                .where(
                    ArtifactReferenceRow.owner_type == owner_type,
                    ArtifactReferenceRow.owner_id == owner_id,
                )
                .limit(1)
            )
            is not None
        ):
            raise ValueError("trajectory requires its configured evidence boundary")

    async def _public_content(
        self, session: AsyncSession, value: Any, *, references: tuple[ProcessArtifactRef, ...]
    ) -> None:
        strings = self.process.content._bounded_strings(
            value, maximum_bytes=self.process.content.policy.maximum_event_bytes
        )
        await self.process.content._check_identifiers(
            session,
            strings,
            allowed={ref.process_artifact_id for ref in references},
            admitted_digests={ref.content_digest for ref in references},
        )

    def _task(self, generator_digest: str, value: dict[str, Any]) -> dict[str, Any]:
        model = self._tasks.get(generator_digest)
        if model is None:
            raise ValueError("task generator has no admitted projection schema")
        encoded = canonical_json_bytes(value)
        parsed = model.model_validate_json(encoded)
        if canonical_json_bytes(parsed.model_dump(mode="json", exclude_unset=True)) != encoded:
            raise ValueError("task projection schema changed the supplied content")
        return cast(dict[str, Any], json.loads(encoded))

    def _product(
        self,
        kind: ProcessProjectionKind,
        candidates: list[_ProjectedRow],
    ) -> ProcessProjectionProduct:
        # Sort on public bytes first. Equal bytes remain separate sampled rows.
        rows = sorted(
            ((canonical_json_bytes(payload), ids, digest) for payload, ids, digest in candidates),
            key=lambda row: (row[0], row[1], row[2]),
        )
        if len(rows) > self._policy.maximum_examples:
            raise ValueError("projection exceeds its example bound")
        data = b"".join(row[0] + b"\n" for row in rows)
        if len(data) > self._policy.maximum_product_bytes:
            raise ValueError("projection exceeds its product byte bound")
        return ProcessProjectionProduct(
            kind=kind,
            public_jsonl=data.decode("utf-8"),
            content_digest=sha256_digest(data),
            rows=tuple(
                ProcessProjectionRowLink(
                    row_index=index,
                    public_row_digest=sha256_digest(row[0]),
                    source_rollout_ids=row[1],
                    archive_row_digest=row[2],
                )
                for index, row in enumerate(rows)
            ),
        )

    async def _archive_artifacts(
        self, session: AsyncSession, manifest: TrainingBundleManifest
    ) -> tuple[ArtifactRef, ...]:
        bundle = await session.get(TrainingBundleRow, manifest.bundle_id)
        if bundle is None:
            raise ValueError("projection source bundle is missing")
        artifact = await session.get(ArtifactRow, bundle.manifest_artifact_id)
        if artifact is None:
            raise ValueError("projection source manifest artifact is missing")
        refs = {_artifact_ref(artifact).artifact_id: _artifact_ref(artifact)}
        for product in manifest.products:
            # Full archive verification depends on every product, including exclusions.
            refs[product.artifact.artifact_id] = product.artifact
        return tuple(refs[key] for key in sorted(refs))

    async def _validate_retention(
        self, session: AsyncSession, receipt: ProcessTrainingProjectionReceipt, *, now: datetime
    ) -> None:
        for reference in receipt.archive_artifacts:
            stored = await self.information.forensic_reference(
                session, artifact_id=reference.artifact_id
            )
            if stored.artifact != reference:
                raise ValueError("projection archive identity differs from retained classification")
        await _exact_pins(
            session,
            "process_training_archive",
            receipt.projection_id,
            {ref.artifact_id for ref in receipt.archive_artifacts},
        )
        # An execution can have several sampled rollouts sharing a dependency owner.
        grouped: dict[str, dict[str, ProcessArtifactRef]] = defaultdict(dict)
        forensic_ids = set()
        for source in receipt.sources:
            grouped[source.execution_digest].update(
                {ref.process_artifact_id: ref for ref in source.artifact_refs}
            )
            for forensic_reference in source.forensic_artifacts:
                if (
                    await self.information.forensic_reference(
                        session, artifact_id=forensic_reference.artifact.artifact_id
                    )
                    != forensic_reference
                ):
                    raise ValueError("projection invocation source classification changed")
                forensic_ids.add(forensic_reference.artifact.artifact_id)
        for execution_digest, references in grouped.items():
            await self._source_ownership(
                session,
                tuple(references[key] for key in sorted(references)),
                execution_digest,
                "process_training_projection",
                _evidence_owner(receipt.projection_id, execution_digest),
                now,
            )
        await _exact_pins(session, "process_training_forensic", receipt.projection_id, forensic_ids)


def _replicated_rows(
    kind: ProcessProjectionKind,
    rows: list[_ProjectedRow],
    candidates: dict[str, _Candidate],
    exclusions: list[ProcessProjectionExclusion],
) -> list[_ProjectedRow]:
    """Reapply declared sample minima to each product after all use exclusions.

    Count distinct source rollouts, never public hashes or pair appearances. Fork
    siblings remain related samples; this check does not establish independence.
    """
    retained = rows
    while retained:
        source_ids = {source_id for _, ids, _ in retained for source_id in ids}
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for source_id in source_ids:
            source = candidates[source_id].source
            counts[source.distribution_digest][source.instance_id] += 1
        ready = {
            source_id
            for source_id in source_ids
            if (
                counts[candidates[source_id].source.distribution_digest][
                    candidates[source_id].source.instance_id
                ]
                >= candidates[source_id].minimum_replicates
                and sum(
                    count >= candidates[source_id].minimum_replicates
                    for count in counts[candidates[source_id].source.distribution_digest].values()
                )
                >= candidates[source_id].minimum_instances
            )
        }
        remaining = []
        for row in retained:
            if set(row[1]).issubset(ready):
                remaining.append(row)
            else:
                exclusions.append(
                    ProcessProjectionExclusion(
                        candidate_id=row[2], kind=kind, reason="replication_insufficient"
                    )
                )
        if len(remaining) == len(retained):
            break
        retained = remaining
    return retained


def _outcome(value: ProcessOutcomeAssessment) -> ProcessLearningOutcome:
    return ProcessLearningOutcome(
        components=tuple(
            ProcessLearningOutcomeComponent(
                component_id=component.component_id,
                disposition=component.disposition,
                deterministic=component.deterministic,
                value=component.value,
                uncertainty=component.uncertainty,
            )
            for component in value.components
        ),
        scalar_return=value.scalar_return,
        preference_rank=value.preference_rank,
    )


def _process_refs(values: Any) -> tuple[ProcessArtifactRef, ...]:
    if any(not isinstance(value, ProcessArtifactRef) for value in values):
        raise ValueError("legacy artifact reference has no training projection admission")
    return tuple(values)


def _evidence_owner(projection_id: str, execution_digest: str) -> str:
    return f"projection-evidence-{sha256_digest((projection_id, execution_digest))[7:]}"


async def _exact_pins(
    session: AsyncSession, owner_type: str, owner_id: str, expected: set[str]
) -> None:
    actual = set(
        await session.scalars(
            select(ArtifactReferenceRow.artifact_id).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == owner_id,
            )
        )
    )
    if actual != expected:
        raise ValueError("projection retention ownership is incomplete")


def _receipt(row: ProcessTrainingProjectionRow) -> ProcessTrainingProjectionReceipt:
    receipt = ProcessTrainingProjectionReceipt.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or receipt.digest != row.record_digest
        or receipt.projection_id != row.projection_id
        or receipt.source_bundle_id != row.source_bundle_id
        or receipt.policy_digest != row.policy_digest
        or receipt.created_at != _utc(row.created_at)
    ):
        raise ValueError("training projection receipt is inconsistent")
    return receipt


def _same_projection(
    stored: ProcessTrainingProjectionReceipt, current: ProcessTrainingProjectionReceipt
) -> None:
    if stored.created_at > current.created_at or stored != current.model_copy(
        update={"created_at": stored.created_at}
    ):
        raise ValueError("projection differs from its current source, policy, rights, or authority")


def _timestamp(value: datetime | None) -> datetime:
    now = value or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("projection time must be timezone-aware")
    return now.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
