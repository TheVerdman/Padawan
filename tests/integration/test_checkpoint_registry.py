from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.checkpoints import CheckpointRegistry
from padawan.domains.contracts import HardGateResult, VerifierDisposition, VerifierResult
from padawan.experiments.controls import ResearchControlRegistry
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    CheckpointEvaluationRecord,
    CheckpointGateEvidenceScope,
    CheckpointManifest,
    CheckpointPromotionPolicy,
    CheckpointStatus,
    EvaluationSuiteManifest,
    MetricObservation,
    PromotionMetricRule,
    ResearchAxis,
    StudyExperimentBinding,
    StudyManifest,
    StudyResultRecord,
    StudyStatus,
    checkpoint_gate_verifier_scope,
)
from padawan.models.tables import CheckpointRow
from padawan.reporting import ReportingService
from padawan.rewards import RewardEngine
from padawan.state.store import StateStore
from padawan.studies import StudyEngine
from tests.integration.test_research_controls import (
    _ENVIRONMENT as _CONTROL_ENVIRONMENT,
)
from tests.integration.test_research_controls import (
    _TASK_MANIFEST,
    _execution,
    _parent_state,
    _profile,
)

_NOW = datetime(2026, 8, 1, tzinfo=UTC)
_ENVIRONMENT = _CONTROL_ENVIRONMENT


def _checkpoint(
    checkpoint_id: str, *, model_digest_character: str, parent: str | None = None
) -> CheckpointManifest:
    return CheckpointManifest(
        checkpoint_id=checkpoint_id,
        model_id="inkling-small",
        tokenizer_id="inkling-tokenizer-v1",
        model_digest=f"sha256:{model_digest_character * 64}",
        tokenizer_digest=f"sha256:{'t' * 64}".replace("t", "a"),
        parent_checkpoint_id=parent,
        architecture={"family": "inkling", "parameter_learning": "offline_only"},
        runtime_compatibility={"responses_api": True},
        created_at=_NOW,
    )


def _metrics(result: StudyResultRecord) -> tuple[MetricObservation, ...]:
    return tuple(
        MetricObservation(
            metric_id=metric.metric_id,
            value=metric.value,
            evidence_refs=(result.result_id,),
            missing_reason=metric.missing_reason,
        )
        for metric in result.metrics
    )


def _scoped_gate(result: StudyResultRecord) -> tuple[VerifierResult, HardGateResult]:
    scope = CheckpointGateEvidenceScope(
        gate_id="release-integrity",
        study_id=result.study_id,
        study_manifest_digest=result.study_manifest_digest,
        suite_manifest_digest=result.suite_manifest_digest,
        condition_id=result.condition_id,
        checkpoint_id=result.checkpoint_id,
        study_result_id=result.result_id,
        study_result_digest=sha256_digest(result.model_dump(mode="json")),
    )
    verifier = VerifierResult(
        result_id=f"verifier-release-integrity-{result.condition_id}",
        verifier_id="release.integrity",
        verifier_version="1",
        scope=checkpoint_gate_verifier_scope(scope),
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="condition, checkpoint, suite, and immutable result integrity passed",
        evidence={"checkpoint_evaluation_scope": scope.model_dump(mode="json")},
        created_at=_NOW,
    )
    return verifier, HardGateResult(
        gate_id="release-integrity",
        passed=True,
        disposition=VerifierDisposition.VERIFIED,
        evidence_refs=(verifier.result_id,),
        reason=verifier.summary,
    )


async def test_checkpoint_comparison_promotion_and_revocation_are_governed(database) -> None:
    checkpoints = CheckpointRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    studies = StudyEngine()
    controls = ResearchControlRegistry()
    suite = EvaluationSuiteManifest(
        suite_id="sealed-release-suite",
        version="1",
        task_manifest_digests=(_TASK_MANIFEST,),
        environment_fingerprints=(_ENVIRONMENT,),
        sealed=True,
        created_at=_NOW,
    )
    active_study_gate = HardGateResult(
        gate_id="release-integrity",
        passed=True,
        disposition=VerifierDisposition.VERIFIED,
        evidence_refs=("not-yet-scoped-verifier",),
        reason="study has not sealed an immutable result",
    )

    async with database.transaction() as session:
        await checkpoints.register_checkpoint(
            session, _checkpoint("checkpoint-n", model_digest_character="b")
        )
        await checkpoints.register_checkpoint(
            session,
            _checkpoint(
                "checkpoint-n-plus-1",
                model_digest_character="c",
                parent="checkpoint-n",
            ),
        )
        await checkpoints.register_checkpoint(
            session,
            _checkpoint(
                "checkpoint-outsider",
                model_digest_character="d",
                parent="checkpoint-n",
            ),
        )
        suite_digest = await checkpoints.register_suite(session, suite)
        baseline_state = await states.create_student(
            session,
            student_id="baseline-student",
            checkpoint_id="checkpoint-n",
            runtime_id="inkling-runtime",
        )
        candidate_state = await states.create_student(
            session,
            student_id="candidate-student",
            checkpoint_id="checkpoint-n-plus-1",
            runtime_id="inkling-runtime",
        )
        profile = _profile(profile_id="checkpoint-release-harness")
        await controls.register_profile(session, profile)
        baseline_execution = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="execution-checkpoint-release-n",
                checkpoint_id="checkpoint-n",
                runtime_id="inkling-runtime",
                seed=101,
                parent_state=_parent_state(baseline_state),
            ),
            parent_state_id=baseline_state.state_id,
        )
        candidate_execution = await controls.register_execution(
            session,
            _execution(
                profile,
                execution_id="execution-checkpoint-release-n-plus-1",
                checkpoint_id="checkpoint-n-plus-1",
                runtime_id="inkling-runtime",
                seed=101,
                parent_state=_parent_state(candidate_state),
            ),
            parent_state_id=candidate_state.state_id,
        )
        baseline_experiment, baseline_assignments = await experiments.create(
            session,
            parent_state_id=baseline_state.state_id,
            seed=101,
            blocks=(MatchedBlock("baseline-group", "baseline-a", "baseline-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="experiment-checkpoint-n",
            research_execution_digest=baseline_execution.execution_digest,
        )
        baseline_replicate_experiment, baseline_replicate_assignments = await experiments.create(
            session,
            parent_state_id=baseline_state.state_id,
            seed=101,
            blocks=(
                MatchedBlock(
                    "baseline-replicate-group",
                    "baseline-replicate-a",
                    "baseline-replicate-b",
                ),
            ),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="experiment-checkpoint-n-replicate",
            research_execution_digest=baseline_execution.execution_digest,
        )
        candidate_experiment, candidate_assignments = await experiments.create(
            session,
            parent_state_id=candidate_state.state_id,
            seed=101,
            blocks=(MatchedBlock("candidate-group", "candidate-a", "candidate-b"),),
            treatment_condition="teacher",
            control_condition="none",
            experiment_id="experiment-checkpoint-n-plus-1",
            research_execution_digest=candidate_execution.execution_digest,
        )
        await studies.create(
            session,
            StudyManifest(
                study_id="study-checkpoint-release",
                version=1,
                title="Checkpoint N versus N+1",
                description="Identical sealed-suite comparison for an offline checkpoint update.",
                seed=101,
                suite_manifest_digest=suite_digest,
                aggregation_policy_id="paired-blocks",
                aggregation_policy_version="1",
                comparison_axes=(ResearchAxis.CHECKPOINT, ResearchAxis.PARENT_STATE),
                experiments=(
                    StudyExperimentBinding(
                        experiment_id=baseline_experiment,
                        condition_id="baseline",
                        checkpoint_id="checkpoint-n",
                        research_role=baseline_state.research_role,
                        suite_manifest_digest=suite_digest,
                        environment_fingerprint=_ENVIRONMENT,
                        research_execution_digest=baseline_execution.execution_digest,
                    ),
                    StudyExperimentBinding(
                        experiment_id=baseline_replicate_experiment,
                        condition_id="baseline-replicate",
                        checkpoint_id="checkpoint-n",
                        research_role=baseline_state.research_role,
                        suite_manifest_digest=suite_digest,
                        environment_fingerprint=_ENVIRONMENT,
                        research_execution_digest=baseline_execution.execution_digest,
                    ),
                    StudyExperimentBinding(
                        experiment_id=candidate_experiment,
                        condition_id="candidate",
                        checkpoint_id="checkpoint-n-plus-1",
                        research_role=candidate_state.research_role,
                        suite_manifest_digest=suite_digest,
                        environment_fingerprint=_ENVIRONMENT,
                        research_execution_digest=candidate_execution.execution_digest,
                    ),
                ),
                created_at=_NOW,
            ),
            status=StudyStatus.ACTIVE,
        )
        await experiments.record_block(
            session,
            block_id=baseline_assignments[0].block_id,
            treatment_success=False,
            control_success=False,
            treatment_score=0.0,
            control_score=0.0,
            contamination_checks={"sealed_suite": True},
        )
        await experiments.record_block(
            session,
            block_id=baseline_replicate_assignments[0].block_id,
            treatment_success=False,
            control_success=False,
            treatment_score=0.0,
            control_score=0.0,
            contamination_checks={"sealed_suite": True},
        )
        await experiments.record_block(
            session,
            block_id=candidate_assignments[0].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"sealed_suite": True},
        )
        await checkpoints.start_evaluation(
            session,
            checkpoint_id="checkpoint-n",
            actor="release-board",
            reason="establish frozen baseline evidence",
            evidence_refs=(suite_digest,),
            decision_id="decision-evaluate-baseline",
            created_at=_NOW,
        )
        replay = await checkpoints.start_evaluation(
            session,
            checkpoint_id="checkpoint-n",
            actor="release-board",
            reason="establish frozen baseline evidence",
            evidence_refs=(suite_digest,),
            decision_id="decision-evaluate-baseline",
            created_at=_NOW,
        )
        assert replay.decision_id == "decision-evaluate-baseline"
        await checkpoints.start_evaluation(
            session,
            checkpoint_id="checkpoint-n-plus-1",
            actor="release-board",
            reason="evaluate external offline update",
            evidence_refs=(suite_digest,),
            decision_id="decision-evaluate-candidate",
            created_at=_NOW,
        )
        await checkpoints.start_evaluation(
            session,
            checkpoint_id="checkpoint-outsider",
            actor="release-board",
            reason="exercise study-participation admission",
            evidence_refs=(suite_digest,),
            decision_id="decision-evaluate-outsider",
            created_at=_NOW,
        )
        active_study_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-active-study",
            checkpoint_id="checkpoint-n",
            study_id="study-checkpoint-release",
            condition_id="baseline",
            suite_manifest_digest=suite_digest,
            metrics=(
                MetricObservation(
                    metric_id="paired_gain",
                    value=0.0,
                    evidence_refs=("invented-study-result",),
                ),
            ),
            hard_gates=(active_study_gate,),
            created_at=_NOW,
        )
        with pytest.raises(ValueError, match="completed study"):
            await checkpoints.record_evaluation(session, active_study_evaluation)
        await studies.transition(
            session,
            study_id="study-checkpoint-release",
            to_status=StudyStatus.COMPLETE,
            completed_at=_NOW,
        )
        baseline_result = await studies.result(
            session,
            study_id="study-checkpoint-release",
            condition_id="baseline",
            checkpoint_id="checkpoint-n",
        )
        candidate_result = await studies.result(
            session,
            study_id="study-checkpoint-release",
            condition_id="candidate",
            checkpoint_id="checkpoint-n-plus-1",
        )
        baseline_replicate_result = await studies.result(
            session,
            study_id="study-checkpoint-release",
            condition_id="baseline-replicate",
            checkpoint_id="checkpoint-n",
        )
        baseline_verifier, baseline_gate = _scoped_gate(baseline_result)
        candidate_verifier, candidate_gate = _scoped_gate(candidate_result)
        baseline_replicate_verifier, baseline_replicate_gate = _scoped_gate(
            baseline_replicate_result
        )
        await RewardEngine().record_verifier_result(session, baseline_verifier)
        await RewardEngine().record_verifier_result(session, candidate_verifier)
        await RewardEngine().record_verifier_result(session, baseline_replicate_verifier)
        baseline_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-checkpoint-n",
            checkpoint_id="checkpoint-n",
            study_id="study-checkpoint-release",
            condition_id="baseline",
            suite_manifest_digest=suite_digest,
            metrics=_metrics(baseline_result),
            hard_gates=(baseline_gate,),
            created_at=_NOW,
        )
        candidate_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-checkpoint-n-plus-1",
            checkpoint_id="checkpoint-n-plus-1",
            study_id="study-checkpoint-release",
            condition_id="candidate",
            suite_manifest_digest=suite_digest,
            metrics=_metrics(candidate_result),
            hard_gates=(candidate_gate,),
            created_at=_NOW,
        )
        baseline_replicate_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-checkpoint-n-replicate",
            checkpoint_id="checkpoint-n",
            study_id="study-checkpoint-release",
            condition_id="baseline-replicate",
            suite_manifest_digest=suite_digest,
            metrics=_metrics(baseline_replicate_result),
            hard_gates=(baseline_replicate_gate,),
            created_at=_NOW,
        )
        outsider_evaluation = candidate_evaluation.model_copy(
            update={
                "evaluation_id": "evaluation-checkpoint-outsider",
                "checkpoint_id": "checkpoint-outsider",
            }
        )
        with pytest.raises(ValueError, match="does not participate"):
            await checkpoints.record_evaluation(session, outsider_evaluation)
        forged_evaluation = baseline_evaluation.model_copy(
            update={
                "evaluation_id": "evaluation-forged-metric-lineage",
                "metrics": (
                    baseline_evaluation.metrics[0].model_copy(
                        update={"evidence_refs": ("invented-study-result",)}
                    ),
                ),
            }
        )
        with pytest.raises(ValueError, match="unknown study result"):
            await checkpoints.record_evaluation(session, forged_evaluation)
        await checkpoints.record_evaluation(session, baseline_evaluation)
        await checkpoints.record_evaluation(session, baseline_replicate_evaluation)
        wrong_gate_evaluation = candidate_evaluation.model_copy(
            update={"hard_gates": (baseline_gate,)}
        )
        with pytest.raises(ValueError, match="another evaluation scope"):
            await checkpoints.record_evaluation(session, wrong_gate_evaluation)
        await checkpoints.record_evaluation(session, candidate_evaluation)
        policy = CheckpointPromotionPolicy(
            policy_id="paired-block-promotion",
            version="1.0.0",
            description="Promote only from immutable paired-block study results.",
            metric_rules=(
                PromotionMetricRule(metric_id="control_success_rate"),
                PromotionMetricRule(metric_id="paired_gain", minimum_delta=0.0),
                PromotionMetricRule(metric_id="treatment_success_rate", minimum_delta=0.0),
            ),
            created_at=_NOW,
        )
        await checkpoints.register_promotion_policy(session, policy)
        comparison = await checkpoints.compare(
            session,
            comparison_id="comparison-n-to-n-plus-1",
            baseline_evaluation_id=baseline_evaluation.evaluation_id,
            candidate_evaluation_id=candidate_evaluation.evaluation_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            created_at=_NOW,
        )
        promotion = await checkpoints.finalize(
            session,
            checkpoint_id="checkpoint-n-plus-1",
            comparison_id=comparison.comparison_id,
            promote=True,
            actor="release-board",
            reason="all required lexicographic gates passed",
            decision_id="decision-promote-candidate",
            created_at=_NOW,
        )
        revocation = await checkpoints.revoke(
            session,
            checkpoint_id="checkpoint-n-plus-1",
            actor="release-board",
            reason="exercise registry-level rollback path",
            evidence_refs=(promotion.decision_id,),
            decision_id="decision-revoke-candidate",
            created_at=_NOW,
        )
        comparison_recomputation = await checkpoints.recompute_comparison(
            session, comparison_id=comparison.comparison_id
        )
        promotion_verification = await checkpoints.verify_decision(
            session, decision_id=promotion.decision_id
        )
        revocation_verification = await checkpoints.verify_decision(
            session, decision_id=revocation.decision_id
        )
        stored = await session.get(CheckpointRow, "checkpoint-n-plus-1")
    durable_report = await ReportingService(database, experiments).checkpoint("checkpoint-n-plus-1")

    assert comparison.promotion_recommended
    assert comparison.missing_required_metrics == ()
    assert promotion.to_status == CheckpointStatus.PROMOTED
    assert revocation.to_status == CheckpointStatus.REVOKED
    assert comparison_recomputation.valid
    assert comparison_recomputation.recomputed_recommendation is True
    assert promotion_verification.valid
    assert revocation_verification.valid
    assert stored is not None and stored.status == CheckpointStatus.REVOKED.value
    assert durable_report["format"] == "padawan.checkpoint_report"
    assert durable_report["status"] == CheckpointStatus.REVOKED.value
    assert all(check["valid"] for check in durable_report["decision_verifications"])


async def test_checkpoint_evaluation_cannot_switch_suite_manifests(database) -> None:
    registry = CheckpointRegistry()
    wrong_suite = EvaluationSuiteManifest(
        suite_id="wrong-suite",
        version="1",
        task_manifest_digests=(sha256_digest("wrong-task"),),
        environment_fingerprints=(_ENVIRONMENT,),
        sealed=True,
        created_at=_NOW,
    )
    async with database.transaction() as session:
        digest = await registry.register_suite(session, wrong_suite)
        assert digest != sha256_digest("not-the-suite")
        with pytest.raises(ValueError, match="unknown checkpoint"):
            await registry.record_evaluation(
                session,
                CheckpointEvaluationRecord(
                    evaluation_id="invalid-evaluation",
                    checkpoint_id="unregistered-checkpoint",
                    study_id="unregistered-study",
                    suite_manifest_digest=digest,
                    metrics=(
                        MetricObservation(
                            metric_id="held_out_capability",
                            value=1.0,
                            evidence_refs=("unknown-study-result",),
                        ),
                    ),
                    hard_gates=(
                        HardGateResult(
                            gate_id="integrity",
                            passed=True,
                            disposition=VerifierDisposition.VERIFIED,
                            evidence_refs=("missing-verifier",),
                            reason="synthetic",
                        ),
                    ),
                    created_at=_NOW,
                ),
            )
