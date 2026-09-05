from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.domains.contracts import TrainingLane
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAdmissionDisposition,
    AmberAuthorizationEnvelope,
    AmberAuthorizationEvent,
    AmberPolicy,
    AmberStatus,
    assert_amber_transition,
)
from padawan.models.contracts import ArtifactRef, ResearchRole, RightsUse
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationEventRow,
    AmberAuthorizationRow,
    ProcessAbandonmentRow,
    ProcessDistributionRow,
    ProcessEventRow,
    ProcessExecutionRow,
    ProcessForkChildRow,
    ProcessForkRow,
    ProcessOutcomeRow,
    ProcessProgramRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessTrainingEligibilityRow,
    ProjectInstanceRow,
)
from padawan.pprl.abandonment import read_abandonment
from padawan.pprl.abandonment_contracts import ProcessAbandonmentReceipt
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessEventRecord,
    ProcessExecutionManifest,
    ProcessForkRecord,
    ProcessLearningLane,
    ProcessOutcomeAssessment,
    ProcessProgram,
    ProcessRolloutRecord,
    ProcessTrainingEligibilityDecision,
    ProjectBudgetUsage,
    ProjectInstance,
    ProjectSplit,
    ProjectStateVersion,
    RewardAuthorityKind,
    RolloutStatus,
)
from padawan.pprl.recovery_contracts import ProcessRecoveryReceipt
from padawan.pprl.task_contracts import ProcessTaskPlan
from padawan.pprl.tasks import ProcessTaskStore
from padawan.training.contracts import (
    CompiledRow,
    EvidenceLedgerEntry,
    EvidenceSourceKind,
    PolicyIdentity,
    PPRLForkPreferenceTrainingRow,
    PPRLTrajectoryTrainingRow,
    PPRLVerifiableTrainingRow,
    TrainingExclusionReason,
    TrainingExclusionRecord,
    TrainingProductKind,
)


class PPRLTrainingCompilationError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Source:
    kind: EvidenceSourceKind
    source_id: str
    record: dict[str, Any]
    digest: str
    rights_digests: tuple[str, ...] = ()

    @property
    def reference(self) -> str:
        return f"{self.kind.value}:{self.source_id}"


@dataclass(frozen=True)
class _AdmissionEvidence:
    decision: AmberAdmissionDecision
    request: AmberActionRequest
    record: dict[str, Any]
    digest: str


@dataclass(frozen=True)
class PPRLCompiledSnapshot:
    products: dict[TrainingProductKind, tuple[CompiledRow, ...]]
    evidence_rows: tuple[EvidenceLedgerEntry, ...]
    exclusions: tuple[TrainingExclusionRecord, ...]
    source_snapshot_digest: str
    rollout_ids: tuple[str, ...]
    rights_digests: tuple[str, ...]
    artifact_digests: tuple[str, ...]
    environment_fingerprints: tuple[str, ...]
    eligibility_policies: tuple[PolicyIdentity, ...]


async def compile_pprl_snapshot(
    session: AsyncSession,
    *,
    as_of: datetime,
    eligibility_policy_id: str | None = None,
    eligibility_policy_version: str | None = None,
) -> PPRLCompiledSnapshot:
    distributions = await _records(
        session,
        ProcessDistributionRow,
        ProcessDistributionRow.created_at,
        ProcessDistributionRow.distribution_digest,
        as_of,
    )
    programs = await _records(
        session,
        ProcessProgramRow,
        ProcessProgramRow.created_at,
        ProcessProgramRow.program_digest,
        as_of,
    )
    authorizations = await _records(
        session,
        AmberAuthorizationRow,
        AmberAuthorizationRow.created_at,
        AmberAuthorizationRow.authorization_digest,
        as_of,
    )
    authorization_events = await _records(
        session,
        AmberAuthorizationEventRow,
        AmberAuthorizationEventRow.created_at,
        AmberAuthorizationEventRow.event_id,
        as_of,
    )
    instances = await _records(
        session,
        ProjectInstanceRow,
        ProjectInstanceRow.created_at,
        ProjectInstanceRow.instance_id,
        as_of,
    )
    executions = await _records(
        session,
        ProcessExecutionRow,
        ProcessExecutionRow.created_at,
        ProcessExecutionRow.execution_digest,
        as_of,
    )
    rollouts = await _records(
        session,
        ProcessRolloutRow,
        ProcessRolloutRow.created_at,
        ProcessRolloutRow.rollout_id,
        as_of,
    )
    states = await _records(
        session,
        ProcessStateRow,
        ProcessStateRow.created_at,
        ProcessStateRow.state_id,
        as_of,
    )
    events = await _records(
        session,
        ProcessEventRow,
        ProcessEventRow.created_at,
        ProcessEventRow.event_id,
        as_of,
    )
    admission_decisions = await _records(
        session,
        AmberAdmissionDecisionRow,
        AmberAdmissionDecisionRow.decided_at,
        AmberAdmissionDecisionRow.decision_id,
        as_of,
    )
    outcomes = await _records(
        session,
        ProcessOutcomeRow,
        ProcessOutcomeRow.created_at,
        ProcessOutcomeRow.assessment_id,
        as_of,
    )
    eligibilities = await _records(
        session,
        ProcessTrainingEligibilityRow,
        ProcessTrainingEligibilityRow.created_at,
        ProcessTrainingEligibilityRow.decision_id,
        as_of,
    )
    forks = await _records(
        session,
        ProcessForkRow,
        ProcessForkRow.created_at,
        ProcessForkRow.fork_id,
        as_of,
    )

    distribution_records = {
        row.distribution_digest: _validate_digest(
            ProcessDistributionManifest,
            row.record_json,
            row.distribution_digest,
            f"process distribution {row.distribution_id}",
        )
        for row in distributions
    }
    program_records = {
        row.program_digest: _validate_digest(
            ProcessProgram,
            row.record_json,
            row.program_digest,
            f"process program {row.program_id}",
        )
        for row in programs
    }
    authorization_records = {
        row.authorization_digest: _authorization_record(row) for row in authorizations
    }
    authorization_event_records = {
        row.event_id: _authorization_event_record(row) for row in authorization_events
    }
    authorization_events_by_digest = _group(
        authorization_event_records.values(), "authorization_digest", "sequence"
    )
    authorization_statuses = _validate_authorization_timelines(
        authorization_records, authorization_events_by_digest
    )
    instance_records = {
        row.instance_id: _validate_digest(
            ProjectInstance,
            row.record_json,
            row.instance_digest,
            f"project instance {row.instance_id}",
        )
        for row in instances
    }
    execution_records = {
        row.execution_digest: _validate_digest(
            ProcessExecutionManifest,
            row.record_json,
            row.execution_digest,
            f"process execution {row.execution_id}",
        )
        for row in executions
    }
    state_records = {row.state_id: _state_record(row) for row in states}
    event_records = {row.event_id: _event_record(row) for row in events}
    admission_records = {row.decision_id: _admission_record(row) for row in admission_decisions}
    outcome_records = {
        row.assessment_id: _validate_digest(
            ProcessOutcomeAssessment,
            row.record_json,
            row.record_digest,
            f"process outcome {row.assessment_id}",
        )
        for row in outcomes
    }
    eligibility_records = {
        row.decision_id: _validate_digest(
            ProcessTrainingEligibilityDecision,
            row.record_json,
            row.record_digest,
            f"process eligibility {row.decision_id}",
        )
        for row in eligibilities
        if _policy_selected(
            row.policy_id,
            row.policy_version,
            eligibility_policy_id,
            eligibility_policy_version,
        )
    }
    fork_records = {
        row.fork_id: _validate_digest(
            ProcessForkRecord,
            row.record_json,
            row.record_digest,
            f"process fork {row.fork_id}",
        )
        for row in forks
    }
    states_by_rollout = _group(state_records.values(), "rollout_id", "sequence")
    events_by_rollout = _group(event_records.values(), "rollout_id", "sequence")
    rollout_records = {
        row.rollout_id: _rollout_record(row, tuple(events_by_rollout.get(row.rollout_id, ())))
        for row in rollouts
    }
    rollout_authorizations = {row.rollout_id: row.authorization_digest for row in rollouts}
    task_plans: dict[str, ProcessTaskPlan] = {}
    rollout_task_plans: dict[str, str] = {}
    for row in rollouts:
        plan = await ProcessTaskStore().check_rollout(session, row)
        if plan is not None:
            if plan.created_at > as_of:
                raise PPRLTrainingCompilationError("task ownership postdates its rollout watermark")
            task_plans[plan.plan_id] = plan
            rollout_task_plans[row.rollout_id] = plan.plan_id
    abandonment_records: dict[str, ProcessAbandonmentReceipt] = {}
    recovery_records: dict[str, ProcessRecoveryReceipt] = {}
    abandoned_rollouts: dict[str, ProcessAbandonmentReceipt] = {}
    for row in rollouts:
        disposition_row = await session.scalar(
            select(ProcessAbandonmentRow).where(ProcessAbandonmentRow.rollout_id == row.rollout_id)
        )
        if row.terminal_abandonment_id is None and disposition_row is None:
            continue
        if disposition_row is None or disposition_row.abandonment_id != row.terminal_abandonment_id:
            raise PPRLTrainingCompilationError("rollout lost its terminal abandonment marker")
        receipt, recovery = await read_abandonment(
            session, abandonment_id=disposition_row.abandonment_id
        )
        if receipt.created_at > as_of:
            continue  # a later disposition does not rewrite an older evidence watermark
        historical = rollout_records[row.rollout_id]
        if (
            historical.current_state_id != receipt.state_id
            or historical.sequence != receipt.state_sequence
        ):
            raise PPRLTrainingCompilationError(
                "abandonment differs from the retained event frontier"
            )
        rollout_records[row.rollout_id] = historical.model_copy(
            update={
                "status": RolloutStatus.CANCELLED,
                "updated_at": receipt.created_at,
            }
        )
        abandonment_records[receipt.request.abandonment_id] = receipt
        recovery_records[recovery.request.recovery_id] = recovery
        abandoned_rollouts[row.rollout_id] = receipt
    outcomes_by_rollout = _group(outcome_records.values(), "rollout_id", "assessment_id")
    decisions_by_rollout = _group(eligibility_records.values(), "rollout_id", "decision_id")
    for rollout_id, rollout in rollout_records.items():
        _validate_rollout_governance(
            rollout=rollout,
            authorization_digest=rollout_authorizations[rollout_id],
            authorization=authorization_records.get(rollout_authorizations[rollout_id]),
            authorization_events=tuple(
                authorization_events_by_digest.get(rollout_authorizations[rollout_id], ())
            ),
            program=program_records.get(rollout.program_digest),
            execution=execution_records.get(rollout.execution_digest),
            states=tuple(states_by_rollout.get(rollout_id, ())),
            events=tuple(events_by_rollout.get(rollout_id, ())),
            admissions=admission_records,
        )
    sources = _sources(
        distribution_records=distribution_records,
        program_records=program_records,
        authorization_records=authorization_records,
        authorization_event_records=authorization_event_records,
        admission_records=admission_records,
        instance_records=instance_records,
        execution_records=execution_records,
        rollout_records=rollout_records,
        state_records=state_records,
        event_records=event_records,
        outcome_records=outcome_records,
        eligibility_records=eligibility_records,
        fork_records=fork_records,
        abandonment_records=abandonment_records,
        recovery_records=recovery_records,
        task_plans=task_plans,
    )
    source_index = {(source.kind, source.source_id): source for source in sources}
    replication_ready = _replication_ready_distributions(
        distributions=distribution_records,
        rollouts=rollout_records,
        events_by_rollout=events_by_rollout,
        decisions_by_rollout=decisions_by_rollout,
        training_authorized_rollouts={
            rollout_id
            for rollout_id, authorization_digest in rollout_authorizations.items()
            if _authorization_permits_training(
                authorization_records[authorization_digest],
                authorization_statuses[authorization_digest],
            )
        },
    )

    trajectory_rows: list[PPRLTrajectoryTrainingRow] = []
    verifiable_rows: list[PPRLVerifiableTrainingRow] = []
    exclusions: list[TrainingExclusionRecord] = []
    trajectories_by_rollout: dict[str, PPRLTrajectoryTrainingRow] = {}
    for rollout_id, rollout in sorted(rollout_records.items()):
        distribution = distribution_records.get(rollout.distribution_digest)
        program = program_records.get(rollout.program_digest)
        instance = instance_records.get(rollout.instance_id)
        execution = execution_records.get(rollout.execution_digest)
        authorization_digest = rollout_authorizations[rollout_id]
        authorization = authorization_records.get(authorization_digest)
        authorization_status = authorization_statuses.get(authorization_digest)
        rollout_states = tuple(states_by_rollout.get(rollout_id, ()))
        rollout_events = tuple(events_by_rollout.get(rollout_id, ()))
        rollout_outcomes = tuple(outcomes_by_rollout.get(rollout_id, ()))
        rollout_decisions = tuple(decisions_by_rollout.get(rollout_id, ()))
        if not all(
            (distribution, program, instance, execution, authorization, authorization_status)
        ):
            raise PPRLTrainingCompilationError(
                f"process rollout has incomplete identity lineage: {rollout_id}"
            )
        typed_distribution = cast(ProcessDistributionManifest, distribution)
        typed_instance = cast(ProjectInstance, instance)
        typed_execution = cast(ProcessExecutionManifest, execution)
        typed_authorization = cast(AmberAuthorizationEnvelope, authorization)
        typed_authorization_status = cast(AmberStatus, authorization_status)
        lineage = _rollout_lineage(
            rollout=rollout,
            states=rollout_states,
            events=rollout_events,
            outcomes=rollout_outcomes,
            decisions=rollout_decisions,
            source_index=source_index,
            distribution=typed_distribution,
            execution=typed_execution,
            authorization_digest=authorization_digest,
            authorization_events=tuple(
                authorization_events_by_digest.get(authorization_digest, ())
            ),
            admissions=admission_records,
        )
        trajectory_reasons = _rollout_reasons(
            rollout=rollout,
            events=rollout_events,
            outcomes=rollout_outcomes,
            decisions=rollout_decisions,
            distribution=typed_distribution,
            execution=typed_execution,
            authorization=typed_authorization,
            authorization_status=typed_authorization_status,
            required_lane=ProcessLearningLane.TRAJECTORY,
            replication_ready=rollout.distribution_digest in replication_ready,
        )
        if rollout_id in rollout_task_plans:
            plan_source = source_index[
                (EvidenceSourceKind.PROCESS_TASK_PLAN, rollout_task_plans[rollout_id])
            ]
            lineage = (
                tuple(sorted(set(lineage[0]) | {plan_source.reference})),
                tuple(sorted(set(lineage[1]) | {plan_source.digest})),
                lineage[2],
            )
        if rollout_id in abandoned_rollouts:
            receipt = abandoned_rollouts[rollout_id]
            additions = [
                source_index[(kind, identity)]
                for kind, identity in (
                    (EvidenceSourceKind.PROCESS_ABANDONMENT, receipt.request.abandonment_id),
                    (EvidenceSourceKind.PROCESS_RECOVERY, receipt.request.recovery_id),
                )
            ]
            lineage = (
                tuple(sorted(set(lineage[0]) | {s.reference for s in additions})),
                tuple(sorted(set(lineage[1]) | {s.digest for s in additions})),
                lineage[2],
            )
            trajectory_reasons[TrainingExclusionReason.PROCESS_ROLLOUT_ABANDONED] = (
                receipt.request.exclusion
            )
        if trajectory_reasons:
            exclusions.append(
                _exclusion(
                    rollout_id,
                    TrainingProductKind.PPRL_TRAJECTORY,
                    TrainingLane.PROCESS,
                    trajectory_reasons,
                    lineage,
                )
            )
        else:
            relevant_decisions = tuple(
                decision
                for decision in rollout_decisions
                if decision.eligible and ProcessLearningLane.TRAJECTORY in decision.allowed_lanes
            )
            relevant_outcomes = _cited_outcomes(relevant_decisions, outcome_records)
            row = PPRLTrajectoryTrainingRow(
                row_id=_row_id("pprl-trajectory", rollout_id, lineage[1]),
                source_evidence_refs=lineage[0],
                source_record_digests=lineage[1],
                rights_digests=lineage[2],
                rollout_id=rollout_id,
                execution_digest=rollout.execution_digest,
                program_digest=rollout.program_digest,
                distribution_digest=rollout.distribution_digest,
                instance_id=rollout.instance_id,
                split=rollout.split.value,
                replication_index=rollout.replication_index,
                process_policy=typed_execution.process_policy.model_dump(mode="json"),
                worker_models=tuple(
                    worker.model_dump(mode="json") for worker in typed_execution.worker_models
                ),
                initial_state=rollout_states[0].model_dump(mode="json"),
                events=tuple(event.model_dump(mode="json") for event in rollout_events),
                terminal_state=rollout_states[-1].model_dump(mode="json"),
                outcomes=tuple(outcome.model_dump(mode="json") for outcome in relevant_outcomes),
                eligibility_decision_ids=tuple(
                    sorted(decision.decision_id for decision in relevant_decisions)
                ),
            )
            trajectory_rows.append(row)
            trajectories_by_rollout[rollout_id] = row

        verifiable_reasons = _rollout_reasons(
            rollout=rollout,
            events=rollout_events,
            outcomes=rollout_outcomes,
            decisions=rollout_decisions,
            distribution=typed_distribution,
            execution=typed_execution,
            authorization=typed_authorization,
            authorization_status=typed_authorization_status,
            required_lane=ProcessLearningLane.VERIFIABLE_REPLAY,
            replication_ready=rollout.distribution_digest in replication_ready,
        )
        if rollout_id in abandoned_rollouts:
            verifiable_reasons[TrainingExclusionReason.PROCESS_ROLLOUT_ABANDONED] = (
                abandoned_rollouts[rollout_id].request.exclusion
            )
        relevant_verifiable_decisions = tuple(
            decision
            for decision in rollout_decisions
            if decision.eligible and ProcessLearningLane.VERIFIABLE_REPLAY in decision.allowed_lanes
        )
        relevant_verifiable_outcomes = _cited_outcomes(
            relevant_verifiable_decisions, outcome_records
        )
        if relevant_verifiable_outcomes and not all(
            outcome.authority.kind == RewardAuthorityKind.VERIFIABLE
            for outcome in relevant_verifiable_outcomes
        ):
            verifiable_reasons[TrainingExclusionReason.PROCESS_OUTCOME_UNRESOLVED] = (
                "PPRL-VR product requires genuinely verifiable outcome authority"
            )
        if verifiable_reasons:
            exclusions.append(
                _exclusion(
                    rollout_id,
                    TrainingProductKind.PPRL_VERIFIABLE,
                    TrainingLane.RLVR,
                    verifiable_reasons,
                    lineage,
                )
            )
        else:
            verifiable_rows.append(
                PPRLVerifiableTrainingRow(
                    row_id=_row_id("pprl-verifiable", rollout_id, lineage[1]),
                    source_evidence_refs=lineage[0],
                    source_record_digests=lineage[1],
                    rights_digests=lineage[2],
                    rollout_id=rollout_id,
                    execution_digest=rollout.execution_digest,
                    program_digest=rollout.program_digest,
                    distribution_digest=rollout.distribution_digest,
                    instance_id=rollout.instance_id,
                    task=typed_instance.task,
                    environment_fingerprint=typed_execution.environment_fingerprint,
                    process_policy=typed_execution.process_policy.model_dump(mode="json"),
                    trajectory=tuple(event.model_dump(mode="json") for event in rollout_events),
                    outcome={
                        "assessments": [
                            outcome.model_dump(mode="json")
                            for outcome in relevant_verifiable_outcomes
                        ]
                    },
                    eligibility_decision_ids=tuple(
                        sorted(decision.decision_id for decision in relevant_verifiable_decisions)
                    ),
                )
            )

    preference_rows, preference_exclusions = await _fork_preferences(
        session=session,
        as_of=as_of,
        forks=fork_records,
        rollout_records=rollout_records,
        trajectories=trajectories_by_rollout,
        outcomes_by_rollout=outcomes_by_rollout,
        decisions_by_rollout=decisions_by_rollout,
        source_index=source_index,
        distribution_records=distribution_records,
        execution_records=execution_records,
    )
    exclusions.extend(preference_exclusions)
    evidence_rows = tuple(_evidence_row(source) for source in sources)
    source_snapshot_digest = sha256_digest(
        [{"source_ref": source.reference, "record_digest": source.digest} for source in sources]
    )
    artifact_digests = tuple(
        sorted(
            {
                artifact.digest
                for source in sources
                for artifact in _extract_artifact_refs(source.record)
            }
        )
    )
    rights_digests = tuple(
        sorted({digest for source in sources for digest in source.rights_digests})
    )
    environment_fingerprints = tuple(
        sorted({execution.environment_fingerprint for execution in execution_records.values()})
    )
    policy_identities = tuple(
        PolicyIdentity(policy_id=policy_id, policy_version=policy_version)
        for policy_id, policy_version in sorted(
            {
                (decision.policy_id, decision.policy_version)
                for decision in eligibility_records.values()
            }
        )
    )
    return PPRLCompiledSnapshot(
        products={
            TrainingProductKind.PPRL_TRAJECTORY: tuple(trajectory_rows),
            TrainingProductKind.PPRL_VERIFIABLE: tuple(verifiable_rows),
            TrainingProductKind.PPRL_FORK_PREFERENCE: tuple(preference_rows),
        },
        evidence_rows=evidence_rows,
        exclusions=tuple(exclusions),
        source_snapshot_digest=source_snapshot_digest,
        rollout_ids=tuple(sorted(rollout_records)),
        rights_digests=rights_digests,
        artifact_digests=artifact_digests,
        environment_fingerprints=environment_fingerprints,
        eligibility_policies=policy_identities,
    )


async def _fork_preferences(
    *,
    session: AsyncSession,
    as_of: datetime,
    forks: dict[str, ProcessForkRecord],
    rollout_records: dict[str, ProcessRolloutRecord],
    trajectories: dict[str, PPRLTrajectoryTrainingRow],
    outcomes_by_rollout: dict[str, list[ProcessOutcomeAssessment]],
    decisions_by_rollout: dict[str, list[ProcessTrainingEligibilityDecision]],
    source_index: dict[tuple[EvidenceSourceKind, str], _Source],
    distribution_records: dict[str, ProcessDistributionManifest],
    execution_records: dict[str, ProcessExecutionManifest],
) -> tuple[list[PPRLForkPreferenceTrainingRow], list[TrainingExclusionRecord]]:
    child_rows = (
        await session.scalars(
            select(ProcessForkChildRow).order_by(
                ProcessForkChildRow.fork_id, ProcessForkChildRow.condition_id
            )
        )
    ).all()
    children_by_fork: dict[str, list[str]] = defaultdict(list)
    for child in child_rows:
        if child.fork_id in forks:
            children_by_fork[child.fork_id].append(child.rollout_id)
    rows: list[PPRLForkPreferenceTrainingRow] = []
    exclusions: list[TrainingExclusionRecord] = []
    for fork_id, fork in sorted(forks.items()):
        candidates: list[
            tuple[
                str,
                float,
                ProcessOutcomeAssessment,
                ProcessTrainingEligibilityDecision,
            ]
        ] = []
        for rollout_id in children_by_fork.get(fork_id, ()):
            trajectory = trajectories.get(rollout_id)
            if trajectory is None:
                continue
            decisions = [
                decision
                for decision in decisions_by_rollout.get(rollout_id, ())
                if decision.eligible
                and ProcessLearningLane.FORK_PREFERENCE in decision.allowed_lanes
            ]
            if not decisions:
                continue
            cited = _cited_outcomes(
                tuple(decisions),
                {
                    outcome.assessment_id: outcome
                    for outcome in outcomes_by_rollout.get(rollout_id, ())
                },
            )
            scalar = [
                (float(outcome.scalar_return), outcome)
                for outcome in cited
                if outcome.scalar_return is not None
            ]
            if not scalar:
                continue
            best_value, best_outcome = max(scalar, key=lambda item: item[0])
            candidates.append((rollout_id, best_value, best_outcome, decisions[-1]))
        if len(candidates) < 2:
            lineage = _fork_lineage(fork, source_index)
            exclusions.append(
                _exclusion(
                    fork_id,
                    TrainingProductKind.PPRL_FORK_PREFERENCE,
                    TrainingLane.PROCESS,
                    {
                        TrainingExclusionReason.PROCESS_FORK_PAIR_MISSING: (
                            "fork needs two eligible completed continuations with scalar outcomes"
                        )
                    },
                    lineage,
                    candidate_kind="process_fork",
                )
            )
            continue
        ordered = sorted(candidates, key=lambda item: (item[1], item[0]))
        rejected = ordered[0]
        chosen = ordered[-1]
        if chosen[1] <= rejected[1]:
            lineage = _fork_lineage(fork, source_index)
            exclusions.append(
                _exclusion(
                    fork_id,
                    TrainingProductKind.PPRL_FORK_PREFERENCE,
                    TrainingLane.PROCESS,
                    {
                        TrainingExclusionReason.PROCESS_FORK_PAIR_MISSING: (
                            "fork continuations have no strict outcome ordering"
                        )
                    },
                    lineage,
                    candidate_kind="process_fork",
                )
            )
            continue
        chosen_row = trajectories[chosen[0]]
        rejected_row = trajectories[rejected[0]]
        lineage = _merge_lineages(
            _trajectory_lineage(chosen_row),
            _trajectory_lineage(rejected_row),
            _fork_lineage(fork, source_index),
        )
        rows.append(
            PPRLForkPreferenceTrainingRow(
                row_id=_row_id("pprl-fork-preference", fork_id, lineage[1]),
                source_evidence_refs=lineage[0],
                source_record_digests=lineage[1],
                rights_digests=lineage[2],
                fork_id=fork_id,
                parent_state_id=fork.parent_state_id,
                chosen_rollout_id=chosen[0],
                rejected_rollout_id=rejected[0],
                chosen_trajectory_digest=sha256_digest(chosen_row),
                rejected_trajectory_digest=sha256_digest(rejected_row),
                chosen_outcome=chosen[2].model_dump(mode="json"),
                rejected_outcome=rejected[2].model_dump(mode="json"),
                eligibility_decision_ids=tuple(
                    sorted({chosen[3].decision_id, rejected[3].decision_id})
                ),
            )
        )
    return rows, exclusions


def _sources(
    *,
    distribution_records: dict[str, ProcessDistributionManifest],
    program_records: dict[str, ProcessProgram],
    authorization_records: dict[str, AmberAuthorizationEnvelope],
    authorization_event_records: dict[str, AmberAuthorizationEvent],
    admission_records: dict[str, _AdmissionEvidence],
    instance_records: dict[str, ProjectInstance],
    execution_records: dict[str, ProcessExecutionManifest],
    rollout_records: dict[str, ProcessRolloutRecord],
    state_records: dict[str, ProjectStateVersion],
    event_records: dict[str, ProcessEventRecord],
    outcome_records: dict[str, ProcessOutcomeAssessment],
    eligibility_records: dict[str, ProcessTrainingEligibilityDecision],
    fork_records: dict[str, ProcessForkRecord],
    abandonment_records: dict[str, ProcessAbandonmentReceipt],
    recovery_records: dict[str, ProcessRecoveryReceipt],
    task_plans: dict[str, ProcessTaskPlan],
) -> tuple[_Source, ...]:
    sources: list[_Source] = []
    for digest, distribution_record in distribution_records.items():
        sources.append(
            _source(
                EvidenceSourceKind.PROCESS_DISTRIBUTION,
                digest,
                distribution_record,
                rights=(sha256_digest(distribution_record.rights),),
            )
        )
    for digest, program_record in program_records.items():
        sources.append(_source(EvidenceSourceKind.PROCESS_PROGRAM, digest, program_record))
    for digest, authorization in authorization_records.items():
        sources.append(_source(EvidenceSourceKind.AMBER_AUTHORIZATION, digest, authorization))
    for event_id, event in authorization_event_records.items():
        sources.append(_source(EvidenceSourceKind.AMBER_AUTHORIZATION_EVENT, event_id, event))
    for decision_id, admission in admission_records.items():
        sources.append(
            _Source(
                kind=EvidenceSourceKind.AMBER_ADMISSION_DECISION,
                source_id=decision_id,
                record=admission.record,
                digest=admission.digest,
            )
        )
    for instance_id, instance_record in instance_records.items():
        distribution = distribution_records[instance_record.distribution_digest]
        sources.append(
            _source(
                EvidenceSourceKind.PROJECT_INSTANCE,
                instance_id,
                instance_record,
                rights=(sha256_digest(distribution.rights),),
            )
        )
    for digest, execution_record in execution_records.items():
        sources.append(
            _source(
                EvidenceSourceKind.PROCESS_EXECUTION,
                digest,
                execution_record,
                rights=(sha256_digest(execution_record.output_rights),),
            )
        )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_ROLLOUT, key, record)
        for key, record in rollout_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_STATE, key, record)
        for key, record in state_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_EVENT, key, record)
        for key, record in event_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_OUTCOME, key, record)
        for key, record in outcome_records.items()
    )
    sources.extend(
        _source(
            EvidenceSourceKind.PROCESS_TRAINING_ELIGIBILITY,
            key,
            record,
            rights=record.rights_digests,
        )
        for key, record in eligibility_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_FORK, key, record)
        for key, record in fork_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_ABANDONMENT, key, record)
        for key, record in abandonment_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_RECOVERY, key, record)
        for key, record in recovery_records.items()
    )
    sources.extend(
        _source(EvidenceSourceKind.PROCESS_TASK_PLAN, key, record)
        for key, record in task_plans.items()
    )
    return tuple(sorted(sources, key=lambda item: (item.kind.value, item.source_id)))


def _source(
    kind: EvidenceSourceKind,
    source_id: str,
    record: Any,
    *,
    rights: tuple[str, ...] = (),
) -> _Source:
    payload = cast(dict[str, Any], record.model_dump(mode="json"))
    return _Source(
        kind=kind,
        source_id=source_id,
        record=payload,
        digest=sha256_digest(payload),
        rights_digests=tuple(sorted(set(rights))),
    )


def _evidence_row(source: _Source) -> EvidenceLedgerEntry:
    return EvidenceLedgerEntry(
        row_id=_row_id("pprl-evidence", source.reference, (source.digest,)),
        source_evidence_refs=(source.reference,),
        source_record_digests=(source.digest,),
        rights_digests=source.rights_digests,
        source_kind=source.kind,
        source_id=source.source_id,
        research_role=(
            ResearchRole.TARGET
            if source.kind
            in {
                EvidenceSourceKind.PROCESS_EXECUTION,
                EvidenceSourceKind.PROCESS_EVENT,
                EvidenceSourceKind.PROCESS_ROLLOUT,
            }
            else None
        ),
        record=source.record,
        artifact_refs=_extract_artifact_refs(source.record),
    )


def _rollout_lineage(
    *,
    rollout: ProcessRolloutRecord,
    states: tuple[ProjectStateVersion, ...],
    events: tuple[ProcessEventRecord, ...],
    outcomes: tuple[ProcessOutcomeAssessment, ...],
    decisions: tuple[ProcessTrainingEligibilityDecision, ...],
    source_index: dict[tuple[EvidenceSourceKind, str], _Source],
    distribution: ProcessDistributionManifest,
    execution: ProcessExecutionManifest,
    authorization_digest: str,
    authorization_events: tuple[AmberAuthorizationEvent, ...],
    admissions: dict[str, _AdmissionEvidence],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    keys = [
        (EvidenceSourceKind.PROCESS_DISTRIBUTION, rollout.distribution_digest),
        (EvidenceSourceKind.PROCESS_PROGRAM, rollout.program_digest),
        (EvidenceSourceKind.AMBER_AUTHORIZATION, authorization_digest),
        *[
            (EvidenceSourceKind.AMBER_AUTHORIZATION_EVENT, event.event_id)
            for event in authorization_events
        ],
        (EvidenceSourceKind.PROJECT_INSTANCE, rollout.instance_id),
        (EvidenceSourceKind.PROCESS_EXECUTION, rollout.execution_digest),
        (EvidenceSourceKind.PROCESS_ROLLOUT, rollout.rollout_id),
        *[(EvidenceSourceKind.PROCESS_STATE, state.state_id) for state in states],
        *[(EvidenceSourceKind.PROCESS_EVENT, event.event_id) for event in events],
        *[
            (EvidenceSourceKind.AMBER_ADMISSION_DECISION, event.amber_decision_id)
            for event in events
            if event.amber_decision_id in admissions
        ],
        *[(EvidenceSourceKind.PROCESS_OUTCOME, outcome.assessment_id) for outcome in outcomes],
        *[
            (EvidenceSourceKind.PROCESS_TRAINING_ELIGIBILITY, decision.decision_id)
            for decision in decisions
        ],
    ]
    selected = [source_index[key] for key in keys if key in source_index]
    return (
        tuple(sorted({source.reference for source in selected})),
        tuple(sorted({source.digest for source in selected})),
        tuple(
            sorted(
                {
                    sha256_digest(distribution.rights),
                    sha256_digest(execution.output_rights),
                    *{digest for decision in decisions for digest in decision.rights_digests},
                }
            )
        ),
    )


def _rollout_reasons(
    *,
    rollout: ProcessRolloutRecord,
    events: tuple[ProcessEventRecord, ...],
    outcomes: tuple[ProcessOutcomeAssessment, ...],
    decisions: tuple[ProcessTrainingEligibilityDecision, ...],
    distribution: ProcessDistributionManifest,
    execution: ProcessExecutionManifest,
    authorization: AmberAuthorizationEnvelope,
    authorization_status: AmberStatus,
    required_lane: ProcessLearningLane,
    replication_ready: bool,
) -> dict[TrainingExclusionReason, str]:
    reasons: dict[TrainingExclusionReason, str] = {}
    effective_status = rollout.status
    if effective_status != RolloutStatus.COMPLETE or not events:
        reasons[TrainingExclusionReason.PROCESS_ROLLOUT_NOT_COMPLETE] = (
            "macro-rollout is not durably complete with at least one process event"
        )
    if rollout.split == ProjectSplit.SEALED:
        reasons[TrainingExclusionReason.SEALED_SOURCE] = (
            "sealed project rollouts are evaluation-only"
        )
    if not _authorization_permits_training(authorization, authorization_status):
        reasons[TrainingExclusionReason.PROCESS_TRAINING_NOT_AUTHORIZED] = (
            "Amber checkpoint policy or current lifecycle status does not permit training "
            f"(status={authorization_status.value}, "
            f"training_permitted={authorization.checkpoint_policy.training_permitted})"
        )
    relevant = tuple(
        decision
        for decision in decisions
        if decision.eligible and required_lane in decision.allowed_lanes
    )
    if not relevant:
        reasons[TrainingExclusionReason.PROCESS_ELIGIBILITY_MISSING] = (
            f"no process eligibility decision admits {required_lane.value}"
        )
    cited = _cited_outcomes(relevant, {outcome.assessment_id: outcome for outcome in outcomes})
    if not cited or not all(outcome.eligible_for_learning for outcome in cited):
        reasons[TrainingExclusionReason.PROCESS_OUTCOME_UNRESOLVED] = (
            "process outcomes are missing, unresolved, or ineligible for learning"
        )
    if not replication_ready:
        reasons[TrainingExclusionReason.PROCESS_REPLICATION_INSUFFICIENT] = (
            "distribution has not met its unique-instance and per-instance rollout minimums"
        )
    required_use = (
        RightsUse.RLVR
        if required_lane == ProcessLearningLane.VERIFIABLE_REPLAY
        else RightsUse.PROCESS
    )
    if not (
        distribution.rights.permits(required_use) and execution.output_rights.permits(required_use)
    ):
        reasons[TrainingExclusionReason.RIGHTS_USE_NOT_PERMITTED] = (
            f"source or output rights do not permit {required_use.value}"
        )
    return reasons


def _replication_ready_distributions(
    *,
    distributions: dict[str, ProcessDistributionManifest],
    rollouts: dict[str, ProcessRolloutRecord],
    events_by_rollout: dict[str, list[ProcessEventRecord]],
    decisions_by_rollout: dict[str, list[ProcessTrainingEligibilityDecision]],
    training_authorized_rollouts: set[str],
) -> set[str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for rollout_id, rollout in rollouts.items():
        events = events_by_rollout.get(rollout_id, [])
        complete = bool(events) and rollout.status == RolloutStatus.COMPLETE
        eligible = any(
            decision.eligible and ProcessLearningLane.TRAJECTORY in decision.allowed_lanes
            for decision in decisions_by_rollout.get(rollout_id, [])
        )
        if (
            complete
            and eligible
            and rollout.split != ProjectSplit.SEALED
            and rollout_id in training_authorized_rollouts
        ):
            counts[rollout.distribution_digest][rollout.instance_id] += 1
    ready: set[str] = set()
    for digest, distribution in distributions.items():
        qualifying_instances = sum(
            count >= distribution.replication.minimum_rollouts_per_instance
            for count in counts[digest].values()
        )
        if qualifying_instances >= distribution.replication.minimum_unique_instances:
            ready.add(digest)
    return ready


def _cited_outcomes(
    decisions: tuple[ProcessTrainingEligibilityDecision, ...],
    outcomes: dict[str, ProcessOutcomeAssessment],
) -> tuple[ProcessOutcomeAssessment, ...]:
    identifiers = sorted(
        {
            assessment_id
            for decision in decisions
            for assessment_id in decision.outcome_assessment_ids
        }
    )
    return tuple(outcomes[identifier] for identifier in identifiers if identifier in outcomes)


def _fork_lineage(
    fork: ProcessForkRecord,
    source_index: dict[tuple[EvidenceSourceKind, str], _Source],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    source = source_index[(EvidenceSourceKind.PROCESS_FORK, fork.fork_id)]
    return ((source.reference,), (source.digest,), ())


def _trajectory_lineage(
    row: PPRLTrajectoryTrainingRow,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return row.source_evidence_refs, row.source_record_digests, row.rights_digests


def _merge_lineages(
    *lineages: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return tuple(
        tuple(sorted({item for lineage in lineages for item in lineage[index]}))
        for index in range(3)
    )  # type: ignore[return-value]


def _exclusion(
    candidate_id: str,
    kind: TrainingProductKind,
    lane: TrainingLane,
    reasons: dict[TrainingExclusionReason, str],
    lineage: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    *,
    candidate_kind: str = "process_rollout",
) -> TrainingExclusionRecord:
    ordered = tuple(sorted(reasons, key=lambda reason: reason.value))
    return TrainingExclusionRecord(
        row_id=_row_id("pprl-exclusion", f"{candidate_id}:{kind.value}", lineage[1]),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        product_kind=kind,
        training_lane=lane,
        reason_codes=ordered,
        details={reason: reasons[reason] for reason in ordered},
    )


def _row_id(prefix: str, identity: str, digests: tuple[str, ...]) -> str:
    return f"row-{prefix}-{sha256_digest({'identity': identity, 'digests': digests})[7:39]}"


def _state_record(row: ProcessStateRow) -> ProjectStateVersion:
    record = cast(
        ProjectStateVersion,
        _validate_embedded_digest(
            ProjectStateVersion,
            row.record_json,
            row.state_digest,
            "state_digest",
            f"process state {row.state_id}",
        ),
    )
    if (
        record.state_id != row.state_id
        or record.rollout_id != row.rollout_id
        or record.sequence != row.sequence
        or record.parent_state_id != row.parent_state_id
        or record.triggering_event_id != row.triggering_event_id
        or _utc(record.created_at) != _utc(row.created_at)
    ):
        raise PPRLTrainingCompilationError(
            f"process state columns disagree with its record: {row.state_id}"
        )
    return record


def _event_record(row: ProcessEventRow) -> ProcessEventRecord:
    record = cast(
        ProcessEventRecord,
        _validate_embedded_digest(
            ProcessEventRecord,
            row.record_json,
            row.event_digest,
            "event_digest",
            f"process event {row.event_id}",
        ),
    )
    if (
        record.event_id != row.event_id
        or record.rollout_id != row.rollout_id
        or record.sequence != row.sequence
        or record.kind.value != row.kind
        or record.actor_id != row.actor_id
        or record.parent_state_id != row.parent_state_id
        or record.resulting_state_id != row.resulting_state_id
        or record.worker_invocation_id != row.worker_invocation_id
        or record.research_execution_digest != row.research_execution_digest
        or record.amber_authorization_digest != row.authorization_digest
        or record.amber_decision_id != row.amber_decision_id
        or _utc(record.created_at) != _utc(row.created_at)
    ):
        raise PPRLTrainingCompilationError(
            f"process event columns disagree with its record: {row.event_id}"
        )
    return record


def _authorization_record(row: AmberAuthorizationRow) -> AmberAuthorizationEnvelope:
    record = cast(
        AmberAuthorizationEnvelope,
        _validate_digest(
            AmberAuthorizationEnvelope,
            row.record_json,
            row.authorization_digest,
            f"Amber authorization {row.authorization_id}",
        ),
    )
    if (
        record.authorization_id != row.authorization_id
        or record.version != row.version
        or record.program_digest != row.program_digest
        or record.distribution_digest != row.distribution_digest
        or _utc(record.created_at) != _utc(row.created_at)
        or _utc(record.expires_at) != _utc(row.expires_at)
    ):
        raise PPRLTrainingCompilationError(
            f"Amber authorization columns disagree with its record: {row.authorization_digest}"
        )
    return record


def _authorization_event_record(row: AmberAuthorizationEventRow) -> AmberAuthorizationEvent:
    record = cast(
        AmberAuthorizationEvent,
        _validate_digest(
            AmberAuthorizationEvent,
            row.record_json,
            row.record_digest,
            f"Amber authorization event {row.event_id}",
        ),
    )
    from_status = record.from_status.value if record.from_status is not None else None
    if (
        record.event_id != row.event_id
        or record.authorization_digest != row.authorization_digest
        or record.sequence != row.sequence
        or from_status != row.from_status
        or record.to_status.value != row.to_status
        or record.actor_id != row.actor_id
        or _utc(record.created_at) != _utc(row.created_at)
    ):
        raise PPRLTrainingCompilationError(
            f"Amber event columns disagree with its record: {row.event_id}"
        )
    return record


def _admission_record(row: AmberAdmissionDecisionRow) -> _AdmissionEvidence:
    decision = cast(
        AmberAdmissionDecision,
        _validate_digest(
            AmberAdmissionDecision,
            row.record_json,
            row.record_digest,
            f"Amber admission decision {row.decision_id}",
        ),
    )
    try:
        request = AmberActionRequest.model_validate(row.request_json, strict=False)
    except ValidationError as exc:
        raise PPRLTrainingCompilationError(
            f"invalid Amber admission request {row.decision_id}: {exc}"
        ) from exc
    request_digest = sha256_digest(request)
    if (
        decision.decision_id != row.decision_id
        or decision.authorization_digest != row.authorization_digest
        or decision.authorization_sequence != row.authorization_sequence
        or decision.rollout_id != row.rollout_id
        or decision.disposition.value != row.disposition
        or decision.request_digest != row.request_digest
        or decision.request_digest != request_digest
        or _utc(decision.decided_at) != _utc(row.decided_at)
        or _utc(decision.decided_at) != _utc(request.requested_at)
    ):
        raise PPRLTrainingCompilationError(
            f"Amber admission columns or request disagree with its decision: {row.decision_id}"
        )
    record = {
        "decision": decision.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
    }
    return _AdmissionEvidence(
        decision=decision,
        request=request,
        record=record,
        digest=sha256_digest(record),
    )


def _validate_authorization_timelines(
    authorizations: dict[str, AmberAuthorizationEnvelope],
    events_by_authorization: dict[str, list[AmberAuthorizationEvent]],
) -> dict[str, AmberStatus]:
    unknown = set(events_by_authorization) - set(authorizations)
    if unknown:
        raise PPRLTrainingCompilationError(
            "Amber lifecycle events cite missing authorizations: " + ", ".join(sorted(unknown))
        )
    statuses: dict[str, AmberStatus] = {}
    for digest, authorization in authorizations.items():
        events = tuple(events_by_authorization.get(digest, ()))
        if not events:
            raise PPRLTrainingCompilationError(
                f"Amber authorization has no lifecycle history: {digest}"
            )
        prior_status: AmberStatus | None = None
        prior_time: datetime | None = None
        authorizing_reviewers: set[str] = set()
        for expected_sequence, event in enumerate(events):
            if event.authorization_digest != digest or event.sequence != expected_sequence:
                raise PPRLTrainingCompilationError(
                    f"Amber lifecycle sequence is not contiguous: {digest}"
                )
            if event.from_status != prior_status:
                raise PPRLTrainingCompilationError(
                    f"Amber lifecycle status chain is broken: {event.event_id}"
                )
            event_time = _utc(event.created_at)
            if prior_time is not None and event_time <= prior_time:
                raise PPRLTrainingCompilationError(
                    f"Amber lifecycle times are not strictly increasing: {digest}"
                )
            if expected_sequence == 0:
                if event_time != _utc(authorization.created_at):
                    raise PPRLTrainingCompilationError(
                        f"Amber preparation time disagrees with its envelope: {digest}"
                    )
            else:
                assert prior_status is not None
                try:
                    assert_amber_transition(prior_status, event.to_status)
                except ValueError as exc:
                    raise PPRLTrainingCompilationError(
                        f"invalid Amber lifecycle transition: {event.event_id}"
                    ) from exc
                if (
                    event_time >= _utc(authorization.expires_at)
                    and event.to_status != AmberStatus.EXPIRED
                ):
                    raise PPRLTrainingCompilationError(
                        f"post-expiry Amber transition is invalid: {event.event_id}"
                    )
                if event.to_status in {
                    AmberStatus.AUTHORIZED,
                    AmberStatus.RELEASE_APPROVED,
                } and (
                    event.actor_id not in authorization.required_reviewers
                    or not event.evidence_refs
                ):
                    raise PPRLTrainingCompilationError(
                        f"reviewed Amber transition lacks reviewer evidence: {event.event_id}"
                    )
                if event.to_status == AmberStatus.AUTHORIZED:
                    authorizing_reviewers.add(event.actor_id)
                if event.to_status == AmberStatus.RELEASE_APPROVED:
                    policy = authorization.checkpoint_policy
                    if (
                        not policy.checkpoint_export_permitted
                        or not policy.independent_review_required
                        or event.actor_id in authorizing_reviewers
                    ):
                        raise PPRLTrainingCompilationError(
                            f"Amber release approval is not independent: {event.event_id}"
                        )
            prior_status = event.to_status
            prior_time = event_time
        assert prior_status is not None
        statuses[digest] = prior_status
    return statuses


def _validate_rollout_governance(
    *,
    rollout: ProcessRolloutRecord,
    authorization_digest: str,
    authorization: AmberAuthorizationEnvelope | None,
    authorization_events: tuple[AmberAuthorizationEvent, ...],
    program: ProcessProgram | None,
    execution: ProcessExecutionManifest | None,
    states: tuple[ProjectStateVersion, ...],
    events: tuple[ProcessEventRecord, ...],
    admissions: dict[str, _AdmissionEvidence],
) -> None:
    if authorization is None or program is None or execution is None:
        raise PPRLTrainingCompilationError(
            f"process rollout has incomplete governed identity: {rollout.rollout_id}"
        )
    if (
        authorization.digest != authorization_digest
        or authorization.program_digest != rollout.program_digest
        or authorization.distribution_digest != rollout.distribution_digest
        or execution.amber_authorization_digest != authorization_digest
        or execution.program_digest != rollout.program_digest
        or execution.distribution_digest != rollout.distribution_digest
        or program.distribution_digest != rollout.distribution_digest
    ):
        raise PPRLTrainingCompilationError(
            f"process rollout governance identity is inconsistent: {rollout.rollout_id}"
        )
    _validate_rollout_structure(rollout=rollout, states=states, events=events)
    lifecycle_by_sequence = {event.sequence: event for event in authorization_events}
    roles = {role.role_id: role for role in program.worker_roles}
    tools = {tool.component_id: tool for tool in program.tools}
    execution_models = {sha256_digest(model) for model in execution.worker_models}
    policy = AmberPolicy()
    for event in events:
        prior_state = states[event.sequence - 1]
        resulting_state = states[event.sequence]
        admission = admissions.get(event.amber_decision_id)
        if admission is None:
            raise PPRLTrainingCompilationError(
                f"process event lacks its Amber admission evidence: {event.event_id}"
            )
        decision = admission.decision
        request = admission.request
        lifecycle_event = lifecycle_by_sequence.get(decision.authorization_sequence)
        if lifecycle_event is None or lifecycle_event.to_status != AmberStatus.ACTIVE:
            raise PPRLTrainingCompilationError(
                f"process event was not admitted under an active Amber sequence: {event.event_id}"
            )
        next_lifecycle_event = lifecycle_by_sequence.get(decision.authorization_sequence + 1)
        if (
            decision.disposition != AmberAdmissionDisposition.ADMITTED
            or decision.authorization_digest != authorization_digest
            or decision.rollout_id != rollout.rollout_id
            or request.authorization_digest != authorization_digest
            or request.rollout_id != rollout.rollout_id
            or request.rollout_sequence != event.sequence - 1
            or request.state_digest != prior_state.state_digest
            or request.lease_token_digest != event.lease_token_digest
            or request.program_digest != rollout.program_digest
            or request.distribution_digest != rollout.distribution_digest
            or request.split != rollout.split
            or request.persistence_mode != program.persistence_mode
            or request.event_kind != event.kind
            or request.environment_fingerprint != execution.environment_fingerprint
            or event.amber_authorization_digest != authorization_digest
            or _utc(request.requested_at) < _utc(lifecycle_event.created_at)
            or _utc(event.created_at) < _utc(decision.decided_at)
            or _utc(event.created_at) >= _utc(authorization.expires_at)
            or (
                next_lifecycle_event is not None
                and _utc(next_lifecycle_event.created_at) <= _utc(event.created_at)
            )
        ):
            raise PPRLTrainingCompilationError(
                f"process event and Amber admission disagree: {event.event_id}"
            )
        role = roles.get(request.role_id)
        tool = tools.get(request.tool_id) if request.tool_id is not None else None
        if (
            role is None
            or request.worker_model_digest not in execution_models
            or (request.tool_id is not None and request.tool_id not in role.allowed_tool_ids)
            or (
                request.tool_id is not None and (tool is None or tool.digest != request.tool_digest)
            )
        ):
            raise PPRLTrainingCompilationError(
                f"process event exceeds its program or execution identity: {event.event_id}"
            )
        if (
            request.projected_usage != resulting_state.payload.budget_usage
            or request.projected_usage.actions != prior_state.payload.budget_usage.actions + 1
            or not _budget_is_monotonic(prior_state.payload.budget_usage, request.projected_usage)
        ):
            raise PPRLTrainingCompilationError(
                f"process event budget differs from its Amber reservation: {event.event_id}"
            )
        reproduced = policy.decide(
            envelope=authorization,
            status=AmberStatus.ACTIVE,
            authorization_sequence=decision.authorization_sequence,
            authorization_updated_at=lifecycle_event.created_at,
            request=request,
            active_workers=0,
            decision_id=decision.decision_id,
        )
        if reproduced != decision:
            raise PPRLTrainingCompilationError(
                f"Amber admission is not reproducible from its envelope: {decision.decision_id}"
            )


def _validate_rollout_structure(
    *,
    rollout: ProcessRolloutRecord,
    states: tuple[ProjectStateVersion, ...],
    events: tuple[ProcessEventRecord, ...],
) -> None:
    if not states or len(states) != len(events) + 1:
        raise PPRLTrainingCompilationError(
            f"process rollout has an incomplete state/event chain: {rollout.rollout_id}"
        )
    if (
        tuple(state.sequence for state in states) != tuple(range(len(states)))
        or tuple(event.sequence for event in events) != tuple(range(1, len(events) + 1))
        or any(state.rollout_id != rollout.rollout_id for state in states)
        or any(event.rollout_id != rollout.rollout_id for event in events)
        or rollout.initial_state_id != states[0].state_id
        or rollout.current_state_id != states[-1].state_id
        or rollout.sequence != len(events)
    ):
        raise PPRLTrainingCompilationError(
            f"process rollout state/event sequence is invalid: {rollout.rollout_id}"
        )
    for event, prior_state, resulting_state in zip(events, states[:-1], states[1:], strict=True):
        if (
            event.parent_state_id != prior_state.state_id
            or event.resulting_state_id != resulting_state.state_id
            or resulting_state.parent_state_id != prior_state.state_id
            or resulting_state.triggering_event_id != event.event_id
            or _utc(resulting_state.created_at) != _utc(event.created_at)
        ):
            raise PPRLTrainingCompilationError(
                f"process rollout lineage is broken at event: {event.event_id}"
            )


def _budget_is_monotonic(previous: ProjectBudgetUsage, projected: ProjectBudgetUsage) -> bool:
    return (
        projected.input_tokens >= previous.input_tokens
        and projected.output_tokens >= previous.output_tokens
        and projected.artifact_bytes >= previous.artifact_bytes
        and float(projected.wall_time_seconds) >= float(previous.wall_time_seconds)
        and float(projected.cost) >= float(previous.cost)
    )


def _authorization_permits_training(
    authorization: AmberAuthorizationEnvelope, status: AmberStatus
) -> bool:
    return authorization.checkpoint_policy.training_permitted and status in {
        AmberStatus.ACTIVE,
        AmberStatus.PAUSED,
        AmberStatus.RELEASE_APPROVED,
    }


def _validate_digest(model: Any, payload: dict[str, Any], digest: str, label: str) -> Any:
    try:
        record = model.model_validate(payload, strict=False)
    except ValidationError as exc:
        raise PPRLTrainingCompilationError(f"invalid {label}: {exc}") from exc
    if sha256_digest(record) != digest:
        raise PPRLTrainingCompilationError(f"invalid digest for {label}")
    return record


def _validate_embedded_digest(
    model: Any,
    payload: dict[str, Any],
    digest: str,
    field: str,
    label: str,
) -> Any:
    try:
        record = model.model_validate(payload, strict=False)
    except ValidationError as exc:
        raise PPRLTrainingCompilationError(f"invalid {label}: {exc}") from exc
    if getattr(record, field) != digest:
        raise PPRLTrainingCompilationError(f"invalid digest for {label}")
    return record


async def _records(
    session: AsyncSession,
    table: Any,
    created_column: Any,
    order_column: Any,
    as_of: datetime,
) -> list[Any]:
    return list(
        (
            await session.scalars(
                select(table).where(created_column <= as_of).order_by(order_column)
            )
        ).all()
    )


def _group(records: Any, key: str, order: str) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        grouped[str(getattr(record, key))].append(record)
    for values in grouped.values():
        values.sort(key=lambda item: getattr(item, order))
    return grouped


def _rollout_record(
    row: ProcessRolloutRow, events: tuple[ProcessEventRecord, ...]
) -> ProcessRolloutRecord:
    latest_event = events[-1] if events else None
    return ProcessRolloutRecord(
        rollout_id=row.rollout_id,
        execution_digest=row.execution_digest,
        program_digest=row.program_digest,
        distribution_digest=row.distribution_digest,
        instance_id=row.instance_id,
        split=ProjectSplit(row.split),
        replication_index=row.replication_index,
        seed=row.seed,
        status=latest_event.rollout_status if latest_event else RolloutStatus.PLANNED,
        initial_state_id=row.initial_state_id,
        current_state_id=(
            latest_event.resulting_state_id if latest_event else row.initial_state_id
        ),
        sequence=latest_event.sequence if latest_event else 0,
        parent_rollout_id=row.parent_rollout_id,
        fork_id=row.fork_id,
        created_at=_utc(row.created_at),
        updated_at=_utc(latest_event.created_at if latest_event else row.created_at),
    )


def _policy_selected(
    policy_id: str,
    policy_version: str,
    selected_id: str | None,
    selected_version: str | None,
) -> bool:
    if selected_id is None:
        return True
    return policy_id == selected_id and policy_version == selected_version


def _extract_artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: dict[str, ArtifactRef] = {}

    def visit(candidate: Any) -> None:
        if isinstance(candidate, dict):
            required = {"artifact_id", "uri", "digest", "media_type", "size_bytes"}
            if required.issubset(candidate):
                try:
                    reference = ArtifactRef.model_validate(candidate, strict=False)
                except ValidationError:
                    pass
                else:
                    found[reference.artifact_id] = reference
                    return
            for nested in candidate.values():
                visit(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)

    visit(value)
    return tuple(found[key] for key in sorted(found))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
