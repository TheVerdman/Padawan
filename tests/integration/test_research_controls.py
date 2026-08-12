from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from padawan.agent.loop import AutonomousResearchLoop
from padawan.checkpoints import CheckpointRegistry
from padawan.experiments.controls import (
    ResearchControlConfiguration,
    ResearchControlRegistry,
    ResearchWorkerConfiguration,
)
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.models.contracts import ResearchRole, RunState, StudentStateRecord, TeacherMode
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ComparabilityDisposition,
    ContextPolicy,
    ContinuationPolicy,
    EvaluationSuiteManifest,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ParentStateIdentity,
    ResearchAxis,
    ResearchExecutionManifest,
    ResearchInstrumentationSeams,
    StudyExperimentBinding,
    StudyManifest,
    StudyStatus,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.models.tables import ExperimentRow, RunRow
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor, WorkResult
from padawan.reporting import ReportingService
from padawan.state.store import StateStore
from padawan.studies import StudyEngine

_NOW = datetime(2026, 8, 12, tzinfo=UTC)
_ENVIRONMENT_PARAMETERS = {
    "code_revision": "test-revision",
    "python": "3.12",
}
_ENVIRONMENT = sha256_digest(_ENVIRONMENT_PARAMETERS)
_TASK_MANIFEST = sha256_digest("task-manifest")
_SUITE_MANIFEST = EvaluationSuiteManifest(
    suite_id="research-control-suite",
    version="1.0.0",
    task_manifest_digests=(_TASK_MANIFEST,),
    environment_fingerprints=(_ENVIRONMENT,),
    sealed=True,
    created_at=_NOW,
)
_SUITE = sha256_digest(_SUITE_MANIFEST.model_dump(mode="json"))


def _component(
    name: str, *, status: IdentityEvidenceStatus = IdentityEvidenceStatus.PINNED
) -> VersionedComponentIdentity:
    return VersionedComponentIdentity(
        component_id=name,
        version="1.0.0",
        digest=sha256_digest(name),
        evidence_status=status,
        evidence=f"test identity for {name}",
    )


def _limit(
    disposition: BudgetDisposition,
    *,
    scope: str,
    unit: str,
    value: float | None = None,
) -> BudgetLimit:
    return BudgetLimit(
        disposition=disposition,
        scope=scope,
        unit=unit,
        value=value,
    )


def _profile(*, profile_id: str = "test.harness") -> HarnessProfile:
    return HarnessProfile(
        profile_id=profile_id,
        version="1.0.0",
        tier="diagnostic_boundary",
        purpose="capability_boundary_mapping",
        continuation=ContinuationPolicy(
            continuation_mode="none",
            response_storage_enabled=False,
            previous_response_id_enabled=False,
            reasoning_retention_enabled=False,
            reasoning_retention_mode="none",
            private_reasoning_capture_enabled=True,
            private_reasoning_used_as_context=False,
        ),
        context=ContextPolicy(
            policy_id="test.context",
            version="1.0.0",
            configured_context_window_tokens=4_096,
            effective_input_limit_tokens=3_072,
            context_limit_evidence="test context limit",
            token_counting_mode="test_tokenizer",
            history_selection="explicit_messages",
            truncation_enabled=False,
            truncation_strategy="none",
            compaction_enabled=False,
            compaction_strategy="none",
        ),
        prompt_templates=(_component("prompt.student"),),
        tools=(_component("tool.none"),),
        budgets=HarnessBudgets(
            actions=_limit(BudgetDisposition.CAPPED, scope="run", unit="actions", value=256),
            input_tokens=_limit(BudgetDisposition.UNBOUNDED, scope="request", unit="tokens"),
            output_tokens=_limit(
                BudgetDisposition.CAPPED, scope="request", unit="tokens", value=2_000
            ),
            latency=_limit(BudgetDisposition.CAPPED, scope="request", unit="seconds", value=120),
            wall_time=_limit(BudgetDisposition.UNBOUNDED, scope="run", unit="seconds"),
            retries=_limit(BudgetDisposition.CAPPED, scope="run", unit="retries", value=3),
            cost=_limit(BudgetDisposition.UNBOUNDED, scope="run", unit="usd"),
        ),
        created_at=_NOW,
    )


def _execution(
    profile: HarnessProfile,
    *,
    execution_id: str,
    checkpoint_id: str = "checkpoint",
    runtime_id: str = "runtime",
    quantization_id: str = "quantization-a",
    seed: int = 17,
    parent_state: ParentStateIdentity | None = None,
) -> ResearchExecutionManifest:
    return ResearchExecutionManifest(
        execution_id=execution_id,
        harness_profile_id=profile.profile_id,
        harness_profile_version=profile.version,
        harness_profile_digest=sha256_digest(profile.model_dump(mode="json")),
        student_model=ModelServingIdentity(
            purpose="student",
            research_role=ResearchRole.TARGET,
            model_id="model",
            checkpoint=_component(checkpoint_id),
            quantization=_component(quantization_id),
            runtime=_component(runtime_id),
            serving_artifact=_component("serving-artifact"),
            protocol="responses",
            runtime_parameters={"batch_size": "1", "response_storage": "false"},
            runtime_parameters_digest=sha256_digest(
                {"batch_size": "1", "response_storage": "false"}
            ),
        ),
        task=TaskCorpusIdentity(
            task_id="task",
            task_version="1.0.0",
            task_manifest_digest=_TASK_MANIFEST,
            corpus_id="corpus",
            corpus_version="1.0.0",
            corpus_digest=sha256_digest("corpus"),
            split="evaluation",
            evidence_status=IdentityEvidenceStatus.PINNED,
        ),
        parent_state=(
            parent_state
            if parent_state is not None
            else ParentStateIdentity(
                state_id="unbound-test-state",
                state_hash=sha256_digest("unbound-test-state"),
                student_id="unbound-test-student",
                checkpoint_id=checkpoint_id,
                runtime_id=runtime_id,
                research_role=ResearchRole.TARGET,
                branch_id="unbound-test-branch",
            )
        ),
        harness_parameters={
            "control_condition": "none",
            "domain_id": "task",
            "pool": "evaluation",
            "teacher_mode": "diagnostic_critique",
            "treatment_condition": "teacher",
            "workflow": "test.developmental_episode",
        },
        environment=VersionedComponentIdentity(
            component_id="environment",
            version="1.0.0",
            digest=_ENVIRONMENT,
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="test environment fingerprint",
        ),
        environment_parameters=_ENVIRONMENT_PARAMETERS,
        environment_fingerprint=_ENVIRONMENT,
        seed=seed,
        created_at=_NOW,
    )


def _parent_state(state: StudentStateRecord) -> ParentStateIdentity:
    return ParentStateIdentity(
        state_id=state.state_id,
        state_hash=state.state_hash,
        student_id=state.student_id,
        checkpoint_id=state.checkpoint_id,
        runtime_id=state.runtime_id,
        research_role=state.research_role,
        parent_state_id=state.parent_state_id,
        branch_id=state.branch_id,
    )


def _worker(
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
) -> ResearchWorkerConfiguration:
    return ResearchWorkerConfiguration(
        profile=profile,
        task=execution.task,
        corpus_competency_ids=(),
        harness_parameters=dict(execution.harness_parameters),
        student_model=execution.student_model,
        auxiliary_models=execution.auxiliary_models,
        environment=execution.environment,
        environment_parameters=dict(execution.environment_parameters),
        environment_fingerprint=execution.environment_fingerprint,
        domain_id=execution.task.task_id,
        workflow=str(execution.harness_parameters["workflow"]),
    )


class _CountingHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, run: Any) -> WorkResult:
        self.calls += 1
        return WorkResult(
            to_state=RunState.ITEMS_LEASED,
            payload_updates={
                "episode_id": "worker-admission-episode",
                "leases": [{"item": {"item_id": "worker-admission-item"}}],
            },
            details={"admitted_execution": run.research_execution_digest},
        )


def test_capture_and_retention_are_separate_and_harness_tiers_are_extensible() -> None:
    profile = _profile()
    assert profile.tier == "diagnostic_boundary"
    assert profile.continuation.private_reasoning_capture_enabled
    assert not profile.continuation.reasoning_retention_enabled
    assert not profile.context.compaction_enabled
    with pytest.raises(ValueError, match="requires response storage"):
        ContinuationPolicy(
            continuation_mode="previous_response_id",
            response_storage_enabled=False,
            previous_response_id_enabled=True,
            reasoning_retention_enabled=False,
            reasoning_retention_mode="none",
            private_reasoning_capture_enabled=False,
            private_reasoning_used_as_context=False,
        )


async def test_execution_binding_and_pairwise_comparability_are_fail_closed(
    database,
) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    experiments = ExperimentEngine(states)
    runs = RunStore()
    profile = _profile()
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="controlled-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        left = _execution(
            profile,
            execution_id="execution-left",
            parent_state=_parent_state(state),
        )
        right = _execution(
            profile,
            execution_id="execution-right",
            quantization_id="quantization-b",
            parent_state=_parent_state(state),
        )
        await controls.register_profile(session, profile)
        left_row = await controls.register_execution(session, left, parent_state_id=state.state_id)
        right_row = await controls.register_execution(
            session, right, parent_state_id=state.state_id
        )
        blocked = await controls.compare(
            session,
            left_execution_digest=left_row.execution_digest,
            right_execution_digest=right_row.execution_digest,
        )
        assert blocked.disposition == ComparabilityDisposition.NOT_COMPARABLE
        assert blocked.blocking_differences == (ResearchAxis.QUANTIZATION,)
        controlled = await controls.compare(
            session,
            left_execution_digest=left_row.execution_digest,
            right_execution_digest=right_row.execution_digest,
            allowed_differences=(ResearchAxis.QUANTIZATION,),
        )
        assert controlled.comparable
        assert controlled.disposition == ComparabilityDisposition.CONTROLLED_DIFFERENCE

        controlled_payload = {
            "student_id": state.student_id,
            "state_id": state.state_id,
            "research_role": ResearchRole.TARGET.value,
            "experiment_seed": left.seed,
            "domain_id": "task",
            "pool": "evaluation",
            "teacher_mode": "diagnostic_critique",
            "treatment_condition": "teacher",
            "control_condition": "none",
        }
        with pytest.raises(ValueError, match="teacher_mode differs"):
            await runs.create(
                session,
                run_id="run-contradictory-payload",
                payload={**controlled_payload, "teacher_mode": "generic_check"},
                research_execution_digest=left_row.execution_digest,
            )
        with pytest.raises(ValueError, match="retry budget differs"):
            await runs.create(
                session,
                run_id="run-contradictory-budget",
                payload=controlled_payload,
                retry_budget=2,
                research_execution_digest=left_row.execution_digest,
            )
        run_id = await runs.create(
            session,
            run_id="run-controlled",
            payload=controlled_payload,
            research_execution_digest=left_row.execution_digest,
        )
        with pytest.raises(ValueError, match="differs"):
            await runs.create(
                session,
                run_id="run-controlled",
                payload={**controlled_payload, "treatment_condition": "changed"},
                research_execution_digest=left_row.execution_digest,
            )
        with pytest.raises(ValueError, match="treatment condition differs"):
            await experiments.create(
                session,
                parent_state_id=state.state_id,
                seed=left.seed,
                blocks=(MatchedBlock("wrong-group", "wrong-a", "wrong-b"),),
                treatment_condition="contradictory-treatment",
                control_condition="none",
                experiment_id="experiment-contradictory",
                research_execution_digest=left_row.execution_digest,
            )
        experiment_id, blocks = await experiments.create(
            session,
            parent_state_id=state.state_id,
            seed=left.seed,
            blocks=(MatchedBlock("group", "item-a", "item-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="experiment-controlled",
            research_execution_digest=left_row.execution_digest,
        )
        await experiments.record_block(
            session,
            block_id=blocks[0].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"matched": True},
        )
        run = await session.get(RunRow, run_id)
        experiment = await session.get(ExperimentRow, experiment_id)
        assert run is not None and run.research_execution_digest == left_row.execution_digest
        assert (
            experiment is not None
            and experiment.research_execution_digest == left_row.execution_digest
        )
    report = await ReportingService(database, experiments).experiment(experiment_id)
    assert report["causal_claim_permitted"]
    assert report["research_binding_consistent"]
    assert report["research_execution_digest"] == left_row.execution_digest
    assert report["harness_profile"]["tier"] == "diagnostic_boundary"


async def test_supervisor_leases_controlled_work_only_to_matching_worker(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    runs = RunStore()
    profile = _profile(profile_id="worker-admission.profile")
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="worker-admission-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        execution = _execution(
            profile,
            execution_id="worker-admission.execution",
            parent_state=_parent_state(state),
        )
        await controls.register_profile(session, profile)
        execution_row = await controls.register_execution(
            session,
            execution,
            parent_state_id=state.state_id,
        )
        await runs.create(
            session,
            run_id="worker-admission.run",
            payload={
                "student_id": state.student_id,
                "state_id": state.state_id,
                "research_role": ResearchRole.TARGET.value,
                "experiment_seed": execution.seed,
                "domain_id": "task",
                "pool": "evaluation",
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "teacher",
                "control_condition": "none",
            },
            research_execution_digest=execution_row.execution_digest,
        )

    matching = _worker(execution, profile)
    wrong_model = execution.student_model.model_copy(update={"model_id": "different-model"})
    mismatched = replace(matching, student_model=wrong_model)
    rejected_handler = _CountingHandler()
    rejected = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=rejected_handler,
        worker_id="mismatched-worker",
        lease_for=timedelta(seconds=30),
        idle_poll_seconds=0,
        research_worker=mismatched,
        research_controls=controls,
    )
    assert await rejected.run(budget=1) == 0
    assert rejected_handler.calls == 0

    wrong_profile = profile.model_copy(
        update={
            "context": profile.context.model_copy(
                update={"history_selection": "different-history-policy"}
            )
        }
    )
    wrong_harness_handler = _CountingHandler()
    wrong_harness = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=wrong_harness_handler,
        worker_id="wrong-harness-worker",
        lease_for=timedelta(seconds=30),
        idle_poll_seconds=0,
        research_worker=replace(matching, profile=wrong_profile),
        research_controls=controls,
    )
    assert await wrong_harness.run(budget=1) == 0
    assert wrong_harness_handler.calls == 0

    wrong_task_handler = _CountingHandler()
    wrong_task = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=wrong_task_handler,
        worker_id="wrong-task-worker",
        lease_for=timedelta(seconds=30),
        idle_poll_seconds=0,
        research_worker=replace(
            matching,
            task=matching.task.model_copy(
                update={"corpus_digest": sha256_digest("different-corpus")}
            ),
        ),
        research_controls=controls,
    )
    assert await wrong_task.run(budget=1) == 0
    assert wrong_task_handler.calls == 0

    admitted_handler = _CountingHandler()
    admitted = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=admitted_handler,
        worker_id="matching-worker",
        lease_for=timedelta(seconds=30),
        idle_poll_seconds=0,
        research_worker=matching,
        research_controls=controls,
    )
    assert await admitted.run(budget=1) == 1
    assert admitted_handler.calls == 1
    async with database.transaction() as session:
        continued = await runs.claim_next(
            session,
            worker_id="matching-continuation-worker",
            lease_for=timedelta(seconds=30),
            eligible_research_execution_digests=(),
            continuation_research_execution_digests=(execution_row.execution_digest,),
        )
        assert continued is not None
        assert continued.state == RunState.ITEMS_LEASED


async def test_factorial_profiles_compare_the_semantics_not_profile_labels(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    baseline_profile = _profile(profile_id="factorial.baseline")
    factorial_profile = baseline_profile.model_copy(
        update={
            "profile_id": "factorial.retention-and-compaction",
            "version": "2.0.0",
            "continuation": baseline_profile.continuation.model_copy(
                update={
                    "continuation_mode": "explicit_reasoning_summary",
                    "reasoning_retention_enabled": True,
                    "reasoning_retention_mode": "restricted_summary",
                }
            ),
            "context": baseline_profile.context.model_copy(
                update={
                    "compaction_enabled": True,
                    "compaction_strategy": "deterministic_state_summary",
                    "compactor": _component("compactor.state-summary"),
                }
            ),
        }
    )
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="factorial-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        baseline = _execution(
            baseline_profile,
            execution_id="factorial-baseline",
            parent_state=_parent_state(state),
        )
        factorial = _execution(
            factorial_profile,
            execution_id="factorial-both",
            parent_state=_parent_state(state),
        )
        await controls.register_profile(session, baseline_profile)
        await controls.register_profile(session, factorial_profile)
        baseline_row = await controls.register_execution(
            session, baseline, parent_state_id=state.state_id
        )
        factorial_row = await controls.register_execution(
            session, factorial, parent_state_id=state.state_id
        )
        assessment = await controls.compare(
            session,
            left_execution_digest=baseline_row.execution_digest,
            right_execution_digest=factorial_row.execution_digest,
            allowed_differences=(
                ResearchAxis.CONTEXT_POLICY,
                ResearchAxis.CONTINUATION,
            ),
        )
    assert assessment.comparable
    assert assessment.differing_axes == (
        ResearchAxis.CONTEXT_POLICY,
        ResearchAxis.CONTINUATION,
    )


async def test_learned_parent_state_is_an_explicit_comparison_axis(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    profile = _profile(profile_id="learned-state.profile")
    async with database.transaction() as session:
        initial = await states.create_student(
            session,
            student_id="learned-state-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        learned = await states.append_state(
            session,
            parent_state_id=initial.state_id,
            compacted_working_state={"validated_lessons": ["lesson-1"]},
            creation_reason="validated lesson incorporated",
        )
        await controls.register_profile(session, profile)
        initial_row = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="learned-state.initial",
                parent_state=_parent_state(initial),
            ),
            parent_state_id=initial.state_id,
        )
        learned_row = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="learned-state.updated",
                parent_state=_parent_state(learned),
            ),
            parent_state_id=learned.state_id,
        )
        blocked = await controls.compare(
            session,
            left_execution_digest=initial_row.execution_digest,
            right_execution_digest=learned_row.execution_digest,
        )
        controlled = await controls.compare(
            session,
            left_execution_digest=initial_row.execution_digest,
            right_execution_digest=learned_row.execution_digest,
            allowed_differences=(ResearchAxis.PARENT_STATE,),
        )
    assert blocked.blocking_differences == (ResearchAxis.PARENT_STATE,)
    assert controlled.comparable
    assert controlled.differing_axes == (ResearchAxis.PARENT_STATE,)


async def test_unknown_compactor_and_instrumentation_fail_provenance(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    base = _profile(profile_id="unknown-instrumentation.profile")
    unknown_compactor = _component(
        "compactor.unknown",
        status=IdentityEvidenceStatus.UNKNOWN,
    )
    unknown_telemetry = _component(
        "telemetry.unknown",
        status=IdentityEvidenceStatus.UNKNOWN,
    )
    profile = base.model_copy(
        update={
            "context": base.context.model_copy(
                update={
                    "compaction_enabled": True,
                    "compaction_strategy": "unverified_summary",
                    "compactor": unknown_compactor,
                }
            ),
            "instrumentation": ResearchInstrumentationSeams(
                mechanistic_telemetry_schema=unknown_telemetry
            ),
        }
    )
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="unknown-instrumentation-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        await controls.register_profile(session, profile)
        execution = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="unknown-instrumentation.execution",
                parent_state=_parent_state(state),
            ),
            parent_state_id=state.state_id,
        )
        assessment = await controls.compare(
            session,
            left_execution_digest=execution.execution_digest,
            right_execution_digest=execution.execution_digest,
        )
    assert not assessment.provenance_complete
    assert assessment.disposition == ComparabilityDisposition.INSUFFICIENT_PROVENANCE
    assert any("compactor.unknown" in gap for gap in assessment.provenance_gaps)
    assert any("telemetry.unknown" in gap for gap in assessment.provenance_gaps)


async def test_declared_transport_identity_cannot_support_a_causal_claim(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    profile = _profile(profile_id="declared-transport.profile")
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="declared-transport-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        execution = _execution(
            profile,
            execution_id="declared-transport.execution",
            parent_state=_parent_state(state),
        )
        execution = execution.model_copy(
            update={
                "student_model": execution.student_model.model_copy(
                    update={
                        "transport_artifact": _component(
                            "responses-edge",
                            status=IdentityEvidenceStatus.DECLARED,
                        )
                    }
                )
            }
        )
        await controls.register_profile(session, profile)
        row = await controls.register_execution(
            session,
            execution,
            parent_state_id=state.state_id,
        )
        assessment = await controls.compare(
            session,
            left_execution_digest=row.execution_digest,
            right_execution_digest=row.execution_digest,
        )
    assert not assessment.provenance_complete
    assert not assessment.comparable
    assert "not endpoint-verified" in " ".join(assessment.provenance_gaps)


async def test_resuming_with_changed_harness_parameters_fails_closed(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    profile = _profile(profile_id="resume.profile")
    template = _execution(profile, execution_id="resume.execution")
    configuration = ResearchControlConfiguration(
        profile=profile,
        student_model=template.student_model,
        auxiliary_models=template.auxiliary_models,
        task=template.task,
        harness_parameters=dict(template.harness_parameters),
        environment=template.environment,
        environment_parameters=dict(template.environment_parameters),
        environment_fingerprint=template.environment_fingerprint,
    )
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="resume-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        manifest = configuration.execution_manifest(
            execution_id=template.execution_id,
            seed=template.seed,
            created_at=template.created_at,
            parent_state=_parent_state(state),
        )
        await controls.register_profile(session, profile)
        row = await controls.register_execution(session, manifest, parent_state_id=state.state_id)
        await controls.validate_execution_configuration(
            session,
            execution_digest=row.execution_digest,
            configuration=configuration,
            seed=template.seed,
        )
        drifted_parameters = dict(configuration.harness_parameters)
        drifted_parameters["teacher_mode"] = "minimal_repair"
        with pytest.raises(ValueError, match="differ from this invocation"):
            await controls.validate_execution_configuration(
                session,
                execution_digest=row.execution_digest,
                configuration=replace(
                    configuration,
                    harness_parameters=dict(sorted(drifted_parameters.items())),
                ),
                seed=template.seed,
            )


async def test_legacy_active_run_can_resume_but_remains_uncontrolled(database) -> None:
    states = StateStore()
    runs = RunStore()
    profile = _profile(profile_id="legacy-resume.profile")
    template = _execution(profile, execution_id="legacy-resume.template", seed=29)
    parameters = {
        "control_condition": "no_intervention",
        "domain_id": "math.algebra",
        "pool": "curriculum",
        "teacher_mode": "diagnostic_critique",
        "treatment_condition": "frontier_teacher_critique",
        "workflow": "padawan.developmental_episode",
    }
    configuration = ResearchControlConfiguration(
        profile=profile,
        student_model=template.student_model,
        auxiliary_models=template.auxiliary_models,
        task=template.task,
        harness_parameters=parameters,
        environment=template.environment,
        environment_parameters=dict(template.environment_parameters),
        environment_fingerprint=template.environment_fingerprint,
    )
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="legacy-resume-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        legacy_run_id = await runs.create(
            session,
            run_id="legacy-active-run",
            payload={
                "student_id": state.student_id,
                "domain_id": "math.algebra",
                "research_role": ResearchRole.TARGET.value,
                "state_id": state.state_id,
                "pool": "curriculum",
                "experiment_seed": 29,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )
    loop = AutonomousResearchLoop(
        database=database,
        runs=runs,
        supervisor=cast(Any, object()),
        student_id="legacy-resume-student",
        domain_id="math.algebra",
        research_controls=ResearchControlRegistry(),
        control_configuration=configuration,
    )
    assert (
        await loop._resume_or_create(  # noqa: SLF001 - verifies compatibility boundary
            seed=29,
            teacher_mode=TeacherMode.DIAGNOSTIC_CRITIQUE,
        )
        == legacy_run_id
    )
    async with database.transaction() as session:
        row = await session.get(RunRow, legacy_run_id)
        assert row is not None and row.research_execution_digest is None


async def test_legacy_experiment_remains_readable_but_cannot_make_a_new_claim(database) -> None:
    states = StateStore()
    experiments = ExperimentEngine(states)
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="legacy-student",
            checkpoint_id="legacy-checkpoint",
            runtime_id="legacy-runtime",
        )
        experiment_id, blocks = await experiments.create(
            session,
            parent_state_id=state.state_id,
            seed=3,
            blocks=(MatchedBlock("legacy-group", "legacy-a", "legacy-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="legacy-experiment",
        )
        await experiments.record_block(
            session,
            block_id=blocks[0].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"matched": True},
        )
    report = await ReportingService(database, experiments).experiment(experiment_id)
    assert report["statistics"]["analyzed_blocks"] == 1
    assert not report["causal_claim_permitted"]
    assert not report["research_binding_consistent"]
    assert report["research_control_assessment"]["disposition"] == (
        ComparabilityDisposition.INSUFFICIENT_PROVENANCE.value
    )


async def test_controlled_study_rejects_task_or_environment_outside_suite(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    experiments = ExperimentEngine(states)
    studies = StudyEngine(controls)
    checkpoints = CheckpointRegistry()
    profile = _profile(profile_id="suite-membership.profile")
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="suite-membership-student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        await controls.register_profile(session, profile)
        execution = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="suite-membership.execution",
                parent_state=_parent_state(state),
            ),
            parent_state_id=state.state_id,
        )
        experiment_id, _ = await experiments.create(
            session,
            parent_state_id=state.state_id,
            seed=17,
            blocks=(MatchedBlock("suite-group", "suite-a", "suite-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="suite-membership.experiment",
            research_execution_digest=execution.execution_digest,
        )
        invalid_suites = (
            (
                "task",
                EvaluationSuiteManifest(
                    suite_id="wrong-task-suite",
                    version="1.0.0",
                    task_manifest_digests=(sha256_digest("wrong-task"),),
                    environment_fingerprints=(_ENVIRONMENT,),
                    sealed=True,
                    created_at=_NOW,
                ),
            ),
            (
                "environment",
                EvaluationSuiteManifest(
                    suite_id="wrong-environment-suite",
                    version="1.0.0",
                    task_manifest_digests=(_TASK_MANIFEST,),
                    environment_fingerprints=(sha256_digest("wrong-environment"),),
                    sealed=True,
                    created_at=_NOW,
                ),
            ),
        )
        for label, suite in invalid_suites:
            suite_digest = await checkpoints.register_suite(session, suite)
            binding = StudyExperimentBinding(
                experiment_id=experiment_id,
                condition_id="candidate",
                checkpoint_id="checkpoint",
                research_role=ResearchRole.TARGET,
                suite_manifest_digest=suite_digest,
                environment_fingerprint=_ENVIRONMENT,
                research_execution_digest=execution.execution_digest,
            )
            with pytest.raises(ValueError, match=f"study {label} is outside"):
                await studies.create(
                    session,
                    StudyManifest(
                        study_id=f"wrong-{label}-study",
                        version=1,
                        title=f"Wrong {label} study",
                        description=f"Reject {label} outside the suite.",
                        seed=17,
                        suite_manifest_digest=suite_digest,
                        aggregation_policy_id="paired-blocks",
                        aggregation_policy_version="1",
                        experiments=(binding,),
                        created_at=_NOW,
                    ),
                )


async def test_study_declares_exactly_which_research_axis_may_change(database) -> None:
    states = StateStore()
    controls = ResearchControlRegistry()
    experiments = ExperimentEngine(states)
    studies = StudyEngine(controls)
    profile = _profile()
    async with database.transaction() as session:
        baseline_state = await states.create_student(
            session,
            student_id="student-n",
            checkpoint_id="checkpoint-n",
            runtime_id="runtime",
        )
        candidate_state = await states.create_student(
            session,
            student_id="student-n-plus-1",
            checkpoint_id="checkpoint-n-plus-1",
            runtime_id="runtime",
        )
        baseline = _execution(
            profile,
            execution_id="execution-checkpoint-n",
            checkpoint_id="checkpoint-n",
            seed=41,
            parent_state=_parent_state(baseline_state),
        )
        candidate = _execution(
            profile,
            execution_id="execution-checkpoint-n-plus-1",
            checkpoint_id="checkpoint-n-plus-1",
            seed=41,
            parent_state=_parent_state(candidate_state),
        )
        assert await CheckpointRegistry().register_suite(session, _SUITE_MANIFEST) == _SUITE
        await controls.register_profile(session, profile)
        baseline_row = await controls.register_execution(
            session, baseline, parent_state_id=baseline_state.state_id
        )
        candidate_row = await controls.register_execution(
            session, candidate, parent_state_id=candidate_state.state_id
        )
        baseline_experiment, _ = await experiments.create(
            session,
            parent_state_id=baseline_state.state_id,
            seed=41,
            blocks=(MatchedBlock("baseline-group", "baseline-a", "baseline-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="controlled-baseline-experiment",
            research_execution_digest=baseline_row.execution_digest,
        )
        candidate_experiment, _ = await experiments.create(
            session,
            parent_state_id=candidate_state.state_id,
            seed=41,
            blocks=(MatchedBlock("candidate-group", "candidate-a", "candidate-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="controlled-candidate-experiment",
            research_execution_digest=candidate_row.execution_digest,
        )
        bindings = (
            StudyExperimentBinding(
                experiment_id=baseline_experiment,
                condition_id="baseline",
                checkpoint_id="checkpoint-n",
                research_role=ResearchRole.TARGET,
                suite_manifest_digest=_SUITE,
                environment_fingerprint=_ENVIRONMENT,
                research_execution_digest=baseline_row.execution_digest,
                factor_values={"checkpoint": "n"},
            ),
            StudyExperimentBinding(
                experiment_id=candidate_experiment,
                condition_id="candidate",
                checkpoint_id="checkpoint-n-plus-1",
                research_role=ResearchRole.TARGET,
                suite_manifest_digest=_SUITE,
                environment_fingerprint=_ENVIRONMENT,
                research_execution_digest=candidate_row.execution_digest,
                factor_values={"checkpoint": "n_plus_1"},
            ),
        )
        await studies.create(
            session,
            StudyManifest(
                study_id="controlled-checkpoint-study",
                version=1,
                title="Controlled checkpoint comparison",
                description="Only checkpoint identity may vary.",
                seed=41,
                suite_manifest_digest=_SUITE,
                aggregation_policy_id="paired-blocks",
                aggregation_policy_version="1",
                comparison_axes=(ResearchAxis.CHECKPOINT, ResearchAxis.PARENT_STATE),
                experiments=bindings,
                created_at=_NOW,
            ),
            status=StudyStatus.ACTIVE,
        )
        controlled = await studies.aggregate(session, study_id="controlled-checkpoint-study")
        assert controlled.research_controls_complete
        assert controlled.blocking_research_differences == ()
        assert controlled.differing_research_axes == (
            ResearchAxis.CHECKPOINT.value,
            ResearchAxis.PARENT_STATE.value,
        )

        await studies.create(
            session,
            StudyManifest(
                study_id="undeclared-checkpoint-study",
                version=1,
                title="Undeclared checkpoint comparison",
                description="This study omits its changed axis.",
                seed=41,
                suite_manifest_digest=_SUITE,
                aggregation_policy_id="paired-blocks",
                aggregation_policy_version="1",
                experiments=bindings,
                created_at=_NOW,
            ),
            status=StudyStatus.ACTIVE,
        )
        blocked = await studies.aggregate(session, study_id="undeclared-checkpoint-study")
        assert blocked.blocking_research_differences == (
            ResearchAxis.CHECKPOINT.value,
            ResearchAxis.PARENT_STATE.value,
        )
        assert not blocked.causal_claim_permitted

        conflicting = bindings[0].model_copy(
            update={"research_execution_digest": candidate_row.execution_digest}
        )
        with pytest.raises(ValueError, match="differs from experiment"):
            await studies.create(
                session,
                StudyManifest(
                    study_id="conflicting-control-study",
                    version=1,
                    title="Conflicting controls",
                    description="Binding digest must match the experiment.",
                    seed=41,
                    suite_manifest_digest=_SUITE,
                    aggregation_policy_id="paired-blocks",
                    aggregation_policy_version="1",
                    experiments=(conflicting,),
                    created_at=_NOW,
                ),
            )
