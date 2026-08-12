from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from padawan.atlas.contracts import (
    AdapterKind,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasSuiteManifest,
    AtlasTrialResult,
    AuthorityKind,
    CampaignCondition,
    CampaignStatus,
    CampaignSuiteBinding,
    EvaluationClass,
    ExploratoryFailureProposal,
    Factor,
    FactorLevel,
    MemoryInterventionEligibility,
    MetricEstimate,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    OutcomeEvidence,
    StopRule,
    SuiteStatus,
    TokenAccounting,
    TrainingFailureEligibility,
    TrialStatus,
    content_id,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import ResearchAxis

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _item(*, modalities: tuple[Modality, ...] = (Modality.TEXT,)) -> AtlasItemManifest:
    prompt = "Solve the deterministic boundary probe."
    identity = {
        "family_id": "algebra.boundary",
        "difficulty": 0.8,
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "modalities": modalities,
        "prompt_digest": sha256_digest(prompt),
        "verifier_id": "symbolic_algebra",
        "verifier_version": "padawan-sympy-v1",
        "verifier_payload": {"expected": "x=2"},
        "metadata": {"lane": "development"},
        "pair_id": None,
        "variant_id": None,
    }
    return AtlasItemManifest(
        item_id=content_id("atlas-item", identity),
        item_digest=sha256_digest(identity),
        family_id="algebra.boundary",
        difficulty=0.8,
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        modalities=modalities,
        prompt=prompt,
        prompt_digest=sha256_digest(prompt),
        verifier_id="symbolic_algebra",
        verifier_version="padawan-sympy-v1",
        verifier_payload={"expected": "x=2"},
        metadata={"lane": "development"},
    )


def _gate(
    modality: Modality,
    status: ModalityGateStatus = ModalityGateStatus.PASSED,
) -> ModalityValidationEvidence:
    return ModalityValidationEvidence(
        modality=modality,
        status=status,
        gate_id=f"{modality.value}-gate",
        gate_revision="1",
        evidence_digest=sha256_digest(f"{modality.value}-gate")
        if status == ModalityGateStatus.PASSED
        else None,
        evidence_refs=(f"{modality.value}-evidence",)
        if status == ModalityGateStatus.PASSED
        else (),
        validated_at=NOW if status == ModalityGateStatus.PASSED else None,
    )


def _suite(
    *,
    item: AtlasItemManifest,
    gates: tuple[ModalityValidationEvidence, ...],
    evaluation_class: EvaluationClass = EvaluationClass.DEVELOPMENT,
) -> AtlasSuiteManifest:
    identity = {
        "suite_id": "atlas-local-algebra",
        "version": "1.0.0",
        "benchmark_id": "padawan.algebra",
        "benchmark_version": "1.0.0",
        "split": "development",
        "governance_id": "project-authored",
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "evaluation_class": evaluation_class,
        "item_digests": (item.item_digest,),
        "modality_gates": gates,
        "task_manifest_digests": (sha256_digest("task"),),
        "corpus_digests": (sha256_digest("corpus"),),
        "environment_fingerprints": (sha256_digest("environment"),),
        "evaluation_suite_manifest_digest": None,
    }
    return AtlasSuiteManifest(
        suite_id="atlas-local-algebra",
        version="1.0.0",
        title="Local deterministic algebra boundary suite",
        benchmark_id="padawan.algebra",
        benchmark_version="1.0.0",
        split="development",
        governance_id="project-authored",
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        status=SuiteStatus.READY,
        evaluation_class=evaluation_class,
        item_digests=(item.item_digest,),
        items=(item,),
        modality_gates=gates,
        task_manifest_digests=(sha256_digest("task"),),
        corpus_digests=(sha256_digest("corpus"),),
        environment_fingerprints=(sha256_digest("environment"),),
        evaluation_suite_manifest_digest=None,
        content_digest=sha256_digest(identity),
        created_at=NOW,
    )


def _result(*, primary: AuthorityKind = AuthorityKind.DETERMINISTIC) -> AtlasTrialResult:
    response_digest = sha256_digest("response")
    response = ArtifactRef(
        artifact_id="art-response",
        uri=f"artifact://sha256/{response_digest[7:]}",
        digest=response_digest,
        media_type="application/json",
        size_bytes=8,
        restricted=True,
        raw_data=True,
    )
    evidence = (
        OutcomeEvidence(
            evidence_id="deterministic-grade",
            authority=AuthorityKind.DETERMINISTIC,
            verifier_id="symbolic_algebra",
            verifier_version="1",
            disposition="verified",
            score=1.0,
            success=True,
            evaluated_output_digest=sha256_digest("contract output"),
            deterministic=True,
            evidence_digest=sha256_digest("grade"),
        ),
        OutcomeEvidence(
            evidence_id="model-grade",
            authority=AuthorityKind.MODEL,
            verifier_id="rubric-model",
            verifier_version="1",
            disposition="verified",
            score=1.0,
            success=True,
            evaluated_output_digest=sha256_digest("contract output"),
            deterministic=False,
            evidence_digest=sha256_digest("model-grade"),
        ),
    )
    provisional = AtlasTrialResult.model_construct(
        result_id="pending",
        request_id="atlas-request-1",
        request_digest=sha256_digest("request"),
        research_execution_digest=sha256_digest("execution"),
        generation_provider="test-provider",
        generation_model_id="test-model",
        generation_protocol="responses",
        raw_request_digest=sha256_digest("raw-request"),
        raw_response_digest=response.digest,
        capabilities_digest=sha256_digest("capabilities"),
        external_call_artifact=ArtifactRef(
            artifact_id="external-call-envelope",
            uri=f"artifact://sha256/{sha256_digest('envelope')[7:]}",
            digest=sha256_digest("envelope"),
            media_type="application/vnd.padawan.generation-result+json",
            size_bytes=8,
            restricted=True,
            raw_data=True,
        ),
        status=TrialStatus.VERIFIED_SUCCESS,
        score=1.0,
        success=True,
        confidence=0.8,
        abstained=False,
        failure_origin=None,
        failure_codes=(),
        verifier_evidence=evidence,
        primary_authority=primary,
        response_digest=response.digest,
        response_artifact=response,
        grader_artifacts=(),
        tokens=TokenAccounting(
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
            counting_mode="provider_reported",
        ),
        latency_ms=10.0,
        wall_time_ms=11.0,
        cost_usd=None,
        tool_calls=(),
        retry_count=0,
        contamination_checks={"suite_membership": True},
        result_digest=sha256_digest("pending"),
        completed_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"result_id", "result_digest", "completed_at"}
    )
    return AtlasTrialResult(
        **{
            **provisional.model_dump(mode="python"),
            "result_id": content_id("atlas-result", identity),
            "result_digest": sha256_digest(identity),
        }
    )


def _campaign(*, promotion: str, adaptive: str, training: str) -> AtlasCampaignManifest:
    factor = Factor(
        factor_id="effort",
        axis=ResearchAxis.HARNESS,
        levels=(
            FactorLevel(level_id="none", parameters={"reasoning_effort": "none"}),
            FactorLevel(level_id="max", parameters={"reasoning_effort": "max"}),
        ),
    )
    condition = CampaignCondition(
        condition_id="standardized-max",
        title="Standardized max effort",
        harness_tier="standardized",
        required_execution_digest=sha256_digest("execution"),
        required_checkpoint_id="conversion-e747e8121d5cd12c54c9",
        required_quantization_id="w8a16-balanced-v1",
        required_protocol="responses",
        factor_levels={"effort": "max"},
        max_requests=90,
        max_input_tokens=300_000,
        max_output_tokens=150_000,
        max_actions=60,
        max_cost_usd=100.0,
        expected_runtime_minutes=120,
        externally_gated=False,
    )
    digests = (promotion, adaptive, training)
    bindings = tuple(
        CampaignSuiteBinding(
            suite_digest=digest,
            evaluation_class=evaluation_class,
            planned_item_count=10,
            trials_per_item=3,
            condition_ids=(condition.condition_id,),
            adaptive=evaluation_class == EvaluationClass.ADAPTIVE_SEARCH,
        )
        for digest, evaluation_class in zip(
            digests,
            (
                EvaluationClass.SEALED_PROMOTION,
                EvaluationClass.ADAPTIVE_SEARCH,
                EvaluationClass.DEVELOPMENT,
            ),
            strict=True,
        )
    )
    provisional = AtlasCampaignManifest.model_construct(
        campaign_id="inkling-w8a16-atlas-v0",
        version="1.0.0",
        title="Inkling W8A16 Capability Atlas v0",
        description="Predeclared local and externally gated boundary campaign.",
        status=CampaignStatus.READY,
        ontology_digest=sha256_digest("ontology"),
        source_claim_ids=(),
        suite_bindings=bindings,
        conditions=(condition,),
        factors=(factor,),
        stop_rules=(
            StopRule(
                rule_id="fixed-precision",
                minimum_trials=10,
                maximum_trials=30,
                target_interval_width=0.2,
            ),
        ),
        randomization_seed=20260812,
        analysis_policy_id="atlas.fixed-and-adaptive",
        analysis_policy_version="1",
        promotion_suite_digests=(promotion,),
        adaptive_suite_digests=(adaptive,),
        training_candidate_suite_digests=(training,),
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"manifest_digest", "status", "created_at"}
    )
    return AtlasCampaignManifest(
        **{
            **provisional.model_dump(mode="python"),
            "manifest_digest": sha256_digest(identity),
        }
    )


def test_media_suite_fails_closed_until_independent_modality_gate_passes() -> None:
    item = _item(modalities=(Modality.IMAGE, Modality.TEXT))
    with pytest.raises(ValidationError, match="unvalidated media"):
        _suite(
            item=item,
            gates=(
                _gate(Modality.IMAGE, ModalityGateStatus.UNVALIDATED),
                _gate(Modality.TEXT),
            ),
        )


def test_suite_item_substitution_changes_content_digest() -> None:
    item = _item()
    suite = _suite(item=item, gates=(_gate(Modality.TEXT),))
    substituted = item.model_copy(update={"prompt": "A favorable replacement prompt."})
    with pytest.raises(ValidationError, match="prompt digest"):
        AtlasSuiteManifest.model_validate(
            {**suite.model_dump(mode="python"), "items": (substituted,)}
        )


def test_deterministic_authority_cannot_be_overridden_by_model_grader() -> None:
    assert _result().success is True
    with pytest.raises(ValidationError, match="cannot outrank"):
        _result(primary=AuthorityKind.MODEL)


def test_metric_evidence_cannot_drop_unfavorable_observed_trials() -> None:
    with pytest.raises(ValidationError, match="every observed trial"):
        MetricEstimate(
            metric_id="success_rate",
            value=0.5,
            lower=0.1,
            upper=0.9,
            confidence_level=0.95,
            planned_trials=2,
            observed_trials=2,
            missing_trials=0,
            infrastructure_failures=0,
            contaminated_trials=0,
            evidence_result_digests=(sha256_digest("favorable-only"),),
        )


def test_campaign_partitions_prevent_adaptive_or_training_leakage_into_promotion() -> None:
    shared = sha256_digest("shared-suite")
    with pytest.raises(ValidationError, match="must be disjoint"):
        _campaign(
            promotion=shared,
            adaptive=shared,
            training=sha256_digest("training-suite"),
        )


def test_interaction_trace_contract_cannot_directly_promote_raw_chat() -> None:
    trace_digest = sha256_digest("trace")
    artifact = ArtifactRef(
        artifact_id="art-trace",
        uri=f"artifact://sha256/{trace_digest[7:]}",
        digest=trace_digest,
        media_type="application/json",
        size_bytes=12,
        restricted=True,
        raw_data=True,
    )
    with pytest.raises(ValidationError, match="False"):
        ExploratoryFailureProposal(
            proposal_id="proposal-1",
            consent_evidence_digest=sha256_digest("consent"),
            consent_lane="research_reproduction",
            source_trace_digest=artifact.digest,
            source_trace_artifact=artifact,
            redacted_excerpt_digest=sha256_digest("redacted"),
            proposed_phenomenon="context-loss",
            proposed_failure_node_ids=("context.state_use",),
            deduplication_key=sha256_digest("dedupe"),
            raw_chat_promoted=True,
            created_at=NOW,
        )


def test_failure_candidates_cannot_bypass_developmental_or_compiler_governance() -> None:
    memory = MemoryInterventionEligibility(
        assessment_id="memory-candidate",
        failure_cluster_digest=sha256_digest("cluster"),
        research_execution_digest=sha256_digest("execution"),
        source_suite_digest=sha256_digest("development-suite"),
        source_evaluation_class=EvaluationClass.DEVELOPMENT,
        independently_reproduced=True,
        stable=True,
        model_failure_confirmed=True,
        harness_effects_ruled_out=True,
        verifier_authority_confirmed=True,
        rights_permit_internal_research=True,
        contamination_cleared=True,
        sealed_content_excluded=True,
        eligible=True,
        blocking_reasons=(),
        evidence_refs=("reproduction-1",),
        created_at=NOW,
    )
    training = TrainingFailureEligibility(
        assessment_id="training-candidate",
        failure_cluster_digest=sha256_digest("cluster"),
        source_suite_digest=sha256_digest("development-suite"),
        source_evaluation_class=EvaluationClass.DEVELOPMENT,
        independently_reproduced=True,
        stable=True,
        model_failure_confirmed=True,
        harness_effects_ruled_out=True,
        verifier_authority_confirmed=True,
        rights_permit_training=True,
        contamination_cleared=True,
        promotion_suite_excluded=True,
        adaptive_search_excluded=True,
        eligible=True,
        allowed_lanes=("sft", "rlvr"),
        blocking_reasons=(),
        evidence_refs=("reproduction-1",),
        created_at=NOW,
    )
    assert memory.requires_developmental_episode
    assert not memory.direct_memory_write_permitted
    assert training.requires_governed_corpus_materialization
    assert not training.direct_compiler_ingestion_permitted

    with pytest.raises(ValidationError, match="training eligibility"):
        TrainingFailureEligibility.model_validate(
            {
                **training.model_dump(mode="python"),
                "harness_effects_ruled_out": False,
            }
        )
