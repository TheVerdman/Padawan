from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.checkpoints import CheckpointRegistry, default_promotion_policy
from padawan.domains.contracts import HardGateResult, VerifierDisposition, VerifierResult
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    CheckpointEvaluationRecord,
    CheckpointManifest,
    CheckpointStatus,
    EvaluationSuiteManifest,
    MetricObservation,
    StudyExperimentBinding,
    StudyManifest,
    StudyStatus,
)
from padawan.models.tables import CheckpointRow
from padawan.reporting import ReportingService
from padawan.rewards import RewardEngine
from padawan.state.store import StateStore
from padawan.studies import StudyEngine

_NOW = datetime(2026, 8, 1, tzinfo=UTC)
_ENVIRONMENT = f"sha256:{'e' * 64}"


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


def _metrics(values: dict[str, float | None]) -> tuple[MetricObservation, ...]:
    return tuple(
        MetricObservation(
            metric_id=metric_id,
            value=value,
            evidence_refs=(f"study-metric:{metric_id}",),
            missing_reason=("metric window incomplete" if value is None else None),
        )
        for metric_id, value in values.items()
    )


async def test_checkpoint_comparison_promotion_and_revocation_are_governed(database) -> None:
    checkpoints = CheckpointRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    studies = StudyEngine()
    suite = EvaluationSuiteManifest(
        suite_id="sealed-release-suite",
        version="1",
        task_manifest_digests=(f"sha256:{'1' * 64}",),
        environment_fingerprints=(_ENVIRONMENT,),
        sealed=True,
        created_at=_NOW,
    )
    verifier = VerifierResult(
        result_id="verifier-release-integrity",
        verifier_id="release.integrity",
        verifier_version="1",
        scope="sealed-release-suite",
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="suite and environment integrity passed",
        evidence={"sealed": True},
        created_at=_NOW,
    )
    gate = HardGateResult(
        gate_id="release-integrity",
        passed=True,
        disposition=VerifierDisposition.VERIFIED,
        evidence_refs=(verifier.result_id,),
        reason=verifier.summary,
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
        baseline_experiment, _ = await experiments.create(
            session,
            parent_state_id=baseline_state.state_id,
            seed=101,
            blocks=(MatchedBlock("baseline-group", "baseline-a", "baseline-b"),),
            treatment_condition="checkpoint-n",
            control_condition="matched-control",
            experiment_id="experiment-checkpoint-n",
        )
        candidate_experiment, _ = await experiments.create(
            session,
            parent_state_id=candidate_state.state_id,
            seed=101,
            blocks=(MatchedBlock("candidate-group", "candidate-a", "candidate-b"),),
            treatment_condition="checkpoint-n-plus-1",
            control_condition="matched-control",
            experiment_id="experiment-checkpoint-n-plus-1",
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
                experiments=(
                    StudyExperimentBinding(
                        experiment_id=baseline_experiment,
                        condition_id="baseline",
                        checkpoint_id="checkpoint-n",
                        research_role=baseline_state.research_role,
                        suite_manifest_digest=suite_digest,
                        environment_fingerprint=_ENVIRONMENT,
                    ),
                    StudyExperimentBinding(
                        experiment_id=candidate_experiment,
                        condition_id="candidate",
                        checkpoint_id="checkpoint-n-plus-1",
                        research_role=candidate_state.research_role,
                        suite_manifest_digest=suite_digest,
                        environment_fingerprint=_ENVIRONMENT,
                    ),
                ),
                created_at=_NOW,
            ),
            status=StudyStatus.ACTIVE,
        )
        await RewardEngine().record_verifier_result(session, verifier)
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
        baseline_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-checkpoint-n",
            checkpoint_id="checkpoint-n",
            study_id="study-checkpoint-release",
            suite_manifest_digest=suite_digest,
            metrics=_metrics(
                {
                    "held_out_capability": 0.70,
                    "unseen_transfer": 0.60,
                    "delayed_retention": 0.60,
                    "non_interference": 0.90,
                    "efficiency": 0.80,
                }
            ),
            hard_gates=(gate,),
            created_at=_NOW,
        )
        candidate_evaluation = CheckpointEvaluationRecord(
            evaluation_id="evaluation-checkpoint-n-plus-1",
            checkpoint_id="checkpoint-n-plus-1",
            study_id="study-checkpoint-release",
            suite_manifest_digest=suite_digest,
            metrics=_metrics(
                {
                    "held_out_capability": 0.75,
                    "unseen_transfer": 0.62,
                    "delayed_retention": 0.61,
                    "non_interference": 0.91,
                    "efficiency": None,
                }
            ),
            hard_gates=(gate,),
            created_at=_NOW,
        )
        await checkpoints.record_evaluation(session, baseline_evaluation)
        await checkpoints.record_evaluation(session, candidate_evaluation)
        policy = default_promotion_policy(created_at=_NOW)
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
    assert (
        next(
            metric for metric in comparison.metric_deltas if metric.metric_id == "efficiency"
        ).missing_reason
        == "candidate: metric window incomplete"
    )
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
                    metrics=_metrics({"held_out_capability": 1.0}),
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
