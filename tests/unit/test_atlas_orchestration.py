from __future__ import annotations

import pytest

from padawan.adapters.base import GenerationResult
from padawan.atlas.adapters import (
    AdapterEvaluation,
    AdapterVerifierEvidence,
    AlgebraAdapter,
)
from padawan.atlas.boundary import BoundaryCandidate
from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AuthorityKind,
    CampaignStatus,
    CampaignSuiteBinding,
    EvaluationClass,
    Factor,
    FactorLevel,
    StopRule,
    TrialStatus,
    content_id,
)
from padawan.atlas.harness import (
    build_atlas_execution_manifest,
    build_atlas_harness_profile,
    build_campaign_execution_binding,
)
from padawan.atlas.orchestration import (
    FixedRunConfiguration,
    build_trial_result,
    generation_request_for,
    materialize_adaptive_trial,
    plan_adaptive_suite_run,
    plan_fixed_suite_run,
)
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import (
    ArtifactRef,
    Capability,
    CapabilityAvailability,
    ResearchRole,
    RuntimeCapabilities,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import ParentStateIdentity, ResearchAxis
from padawan.orchestration.external_calls import _serialize_result
from tests.unit.test_atlas_harness import NOW, _base_control, _condition, _suite


def _factor(factor_id: str, axis: ResearchAxis, selected: str) -> Factor:
    alternate = "alternate" if selected != "alternate" else "control"
    return Factor(
        factor_id=factor_id,
        axis=axis,
        levels=(
            FactorLevel(level_id=selected, parameters={"value": selected}),
            FactorLevel(level_id=alternate, parameters={"value": alternate}),
        ),
    )


def _campaign(
    condition, suite, *, trials: int = 1, adaptive: bool = False
) -> AtlasCampaignManifest:
    provisional = AtlasCampaignManifest.model_construct(
        campaign_id="atlas-orchestration-test",
        version="1.0.0",
        title="Atlas orchestration test",
        description="Content-bound fixed trial planning without external execution.",
        status=CampaignStatus.EXTERNALLY_GATED,
        ontology_digest=sha256_digest("ontology"),
        source_claim_ids=(),
        suite_bindings=(
            CampaignSuiteBinding(
                suite_digest=suite.content_digest,
                evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
                planned_item_count=1,
                trials_per_item=trials,
                condition_ids=(condition.condition_id,),
                adaptive=adaptive,
            ),
        ),
        conditions=(condition,),
        factors=tuple(
            sorted(
                (
                    _factor(
                        "compaction",
                        ResearchAxis.CONTEXT_POLICY,
                        condition.factor_levels["compaction"],
                    ),
                    _factor("effort", ResearchAxis.HARNESS, condition.factor_levels["effort"]),
                    _factor(
                        "retention",
                        ResearchAxis.CONTINUATION,
                        condition.factor_levels["retention"],
                    ),
                    _factor(
                        "tool_access",
                        ResearchAxis.TOOLS,
                        condition.factor_levels["tool_access"],
                    ),
                ),
                key=lambda item: item.factor_id,
            )
        ),
        stop_rules=(
            StopRule(
                rule_id="fixed",
                minimum_trials=1,
                maximum_trials=30,
                target_interval_width=0.5,
            ),
        ),
        randomization_seed=17,
        analysis_policy_id="atlas.fixed",
        analysis_policy_version="1",
        promotion_suite_digests=(),
        adaptive_suite_digests=(suite.content_digest,) if adaptive else (),
        training_candidate_suite_digests=(),
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"manifest_digest", "status", "created_at"}
    )
    return AtlasCampaignManifest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _coordinates(
    *,
    trials: int = 1,
    authorization: str = "authorization-test",
    adaptive: bool = False,
):
    base = _base_control()
    condition = _condition().model_copy(
        update={
            "factor_levels": {
                "compaction": "disabled",
                "effort": "max",
                "retention": "disabled",
                "tool_access": "disabled",
            }
        }
    )
    parent = ParentStateIdentity(
        state_id="state-atlas-orchestration",
        state_hash=sha256_digest("state-atlas-orchestration"),
        student_id="student-atlas-orchestration",
        checkpoint_id=condition.required_checkpoint_id or "missing",
        runtime_id="inkling-vllm",
        research_role=ResearchRole.TARGET,
        branch_id="atlas-orchestration",
    )
    base_execution = base.execution_manifest(
        execution_id="base-atlas-orchestration",
        seed=17,
        created_at=NOW,
        parent_state=parent,
    )
    suite = _suite(
        base.environment_fingerprint,
        task_manifest_digest=base_execution.task.task_manifest_digest,
        corpus_digest=base_execution.task.corpus_digest,
    )
    campaign = _campaign(condition, suite, trials=trials, adaptive=adaptive)
    profile = build_atlas_harness_profile(
        base=base.profile,
        condition=condition,
        source_revision="c2e7a25",
        created_at=NOW,
    )
    execution = build_atlas_execution_manifest(
        base=base_execution,
        profile=profile,
        campaign_digest=campaign.manifest_digest,
        condition=condition,
        suite=suite,
        seed=17,
        created_at=NOW,
    )
    binding = build_campaign_execution_binding(
        campaign=campaign,
        condition=condition,
        suite=suite,
        execution=execution,
        bound_by="test.operator",
        external_authorization_ref=authorization,
        created_at=NOW,
    )
    return campaign, condition, suite, profile, execution, binding


def _configuration() -> FixedRunConfiguration:
    return FixedRunConfiguration(
        instructions="Return only the requested answer.",
        max_output_tokens_per_request=512,
        action_budget_per_request=8,
        max_cost_usd_per_request=1.0,
        temperature=0.0,
        scientific_effort=0.99,
        reasoning_effort_wire_value="high",
        effort_mapping_evidence_digest=sha256_digest("effort-mapping-preflight"),
        edge_preflight_evidence_digest=sha256_digest("edge-preflight"),
        base_seed=31,
    )


def _capabilities() -> RuntimeCapabilities:
    unavailable = Capability(
        availability=CapabilityAvailability.UNAVAILABLE,
        reason="not relevant to the captured unit-test generation",
    )
    return RuntimeCapabilities(
        responses_api=unavailable,
        streaming=unavailable,
        cancellation=unavailable,
        logprobs=unavailable,
        token_ids=unavailable,
        private_reasoning=unavailable,
        reasoning_boundaries=unavailable,
        gpu_telemetry=unavailable,
        router_telemetry=unavailable,
    )


def test_fixed_run_predeclares_every_request_and_reuses_generation_authority() -> None:
    campaign, condition, suite, profile, execution, binding = _coordinates()
    configuration = _configuration()

    plan = plan_fixed_suite_run(
        campaign=campaign,
        condition=condition,
        suite=suite,
        binding=binding,
        execution=execution,
        profile=profile,
        run_id="run-atlas-fixed",
        adapter=AlgebraAdapter.descriptor,
        configuration=configuration,
        created_at=NOW,
    )
    generation = generation_request_for(
        request=plan.requests[0],
        item=suite.items[0],
        configuration=configuration,
        profile=profile,
    )

    assert plan.manifest.planned_request_count == 1
    assert plan.manifest.predeclared_request_digests == (plan.requests[0].request_digest,)
    assert plan.requests[0].tool_manifest_digest == sha256_digest(
        {"tools": (), "tool_choice": None}
    )
    assert generation.request_id == plan.requests[0].request_id
    assert generation.sampling.reasoning_effort == "high"
    assert generation.store is False
    assert generation.previous_response_id is None


def test_fixed_run_rejects_post_hoc_replicate_and_wire_tool_expansion() -> None:
    with pytest.raises(ValueError, match="request ceiling"):
        _coordinates(trials=31)

    campaign, condition, suite, profile, execution, binding = _coordinates()
    expanded = _configuration().model_copy(
        update={
            "tool_ids": ("undeclared-shell",),
            "tools": ({"type": "function", "name": "undeclared-shell"},),
        }
    )
    with pytest.raises(ValueError, match="tool"):
        plan_fixed_suite_run(
            campaign=campaign,
            condition=condition,
            suite=suite,
            binding=binding,
            execution=execution,
            profile=profile,
            run_id="run-tool-drift",
            adapter=AlgebraAdapter.descriptor,
            configuration=expanded,
            created_at=NOW,
        )


def test_adaptive_run_freezes_policy_and_materializes_one_disclosed_decision() -> None:
    campaign, condition, suite, profile, execution, binding = _coordinates(
        trials=1,
        adaptive=True,
    )
    configuration = _configuration()
    plan = plan_adaptive_suite_run(
        campaign=campaign,
        condition=condition,
        suite=suite,
        binding=binding,
        execution=execution,
        profile=profile,
        run_id="run-atlas-adaptive",
        adapter=AlgebraAdapter.descriptor,
        configuration=configuration,
        stop_rule_id="fixed",
        created_at=NOW,
    )
    candidate = BoundaryCandidate(
        item_digest=suite.item_digests[0],
        difficulty=suite.items[0].difficulty,
        planned_trials=1,
        successes=0,
        failures=0,
        missing_trials=0,
    )
    materialized = materialize_adaptive_trial(
        plan=plan,
        campaign=campaign,
        condition=condition,
        suite=suite,
        profile=profile,
        adapter=AlgebraAdapter.descriptor,
        configuration=configuration,
        candidates=(candidate,),
        created_at=NOW,
    )

    assert plan.manifest.adaptive
    assert plan.manifest.predeclared_request_digests == ()
    assert plan.manifest.request_template_digest == sha256_digest(
        {
            "configuration": configuration.model_dump(mode="json"),
            "adapter": AlgebraAdapter.descriptor,
        }
    )
    assert materialized.allocation.decision_sequence == 0
    assert materialized.allocation.trial_index == 0
    assert materialized.request.allocation_id == materialized.allocation.allocation_id

    drifted = configuration.model_copy(update={"instructions": "Changed after results."})
    with pytest.raises(ValueError, match="request template"):
        materialize_adaptive_trial(
            plan=plan,
            campaign=campaign,
            condition=condition,
            suite=suite,
            profile=profile,
            adapter=AlgebraAdapter.descriptor,
            configuration=drifted,
            candidates=(candidate,),
            created_at=NOW,
        )


def test_captured_generation_becomes_immutable_authoritative_trial_evidence() -> None:
    campaign, condition, suite, profile, execution, binding = _coordinates()
    plan = plan_fixed_suite_run(
        campaign=campaign,
        condition=condition,
        suite=suite,
        binding=binding,
        execution=execution,
        profile=profile,
        run_id="run-atlas-result",
        adapter=AlgebraAdapter.descriptor,
        configuration=_configuration(),
        created_at=NOW,
    )
    request = plan.requests[0]
    verifier = VerifierResult(
        result_id="verifier-result-atlas",
        verifier_id="symbolic_algebra",
        verifier_version="1.0.0",
        scope=request.item_id,
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="deterministic oracle accepted the response",
        evidence={"accepted": True},
        created_at=NOW,
    )
    adapter_evidence = (
        AdapterVerifierEvidence(authority=AuthorityKind.DETERMINISTIC, result=verifier),
    )
    evaluation_identity = {
        "adapter_id": request.adapter_id,
        "adapter_version": request.adapter_version,
        "disposition": VerifierDisposition.VERIFIED,
        "score": 1.0,
        "success": True,
        "primary_authority": AuthorityKind.DETERMINISTIC,
        "evidence": adapter_evidence,
        "failure_codes": (),
    }
    evaluation = AdapterEvaluation(
        evaluation_id=content_id("atlas-evaluation", evaluation_identity),
        adapter_id=request.adapter_id,
        adapter_version=request.adapter_version,
        disposition=VerifierDisposition.VERIFIED,
        score=1.0,
        success=True,
        primary_authority=AuthorityKind.DETERMINISTIC,
        evidence=adapter_evidence,
        evidence_digest=sha256_digest(evaluation_identity),
    )
    generation = GenerationResult(
        request_id=request.request_id,
        response_id="response-atlas",
        provider="inkling",
        model_id=execution.student_model.model_id,
        protocol=execution.student_model.protocol,
        output_text="x=2",
        raw_request=b"{}",
        raw_response=b'{"output":"x=2"}',
        usage={"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
        token_ids=None,
        token_logprobs=None,
        private_reasoning=None,
        reasoning_summary=None,
        finish_reason="stop",
        latency_ms=9.0,
        capabilities=_capabilities(),
    )
    response_digest = sha256_digest(generation.raw_response)
    response = ArtifactRef(
        artifact_id="artifact-atlas-response",
        uri=f"artifact://sha256/{response_digest[7:]}",
        digest=response_digest,
        media_type="application/json",
        size_bytes=len(generation.raw_response),
        restricted=True,
        raw_data=True,
    )
    envelope = _serialize_result(generation)
    envelope_digest = sha256_digest(envelope)
    external_call_artifact = ArtifactRef(
        artifact_id="artifact-atlas-generation-envelope",
        uri=f"artifact://sha256/{envelope_digest[7:]}",
        digest=envelope_digest,
        media_type="application/vnd.padawan.generation-result+json",
        size_bytes=len(envelope),
        restricted=True,
        raw_data=True,
    )
    generation_request = generation_request_for(
        request=request,
        item=suite.items[0],
        configuration=_configuration(),
        profile=profile,
    )

    result = build_trial_result(
        request=request,
        generation=generation,
        execution=execution,
        generation_request=generation_request,
        response_artifact=response,
        external_call_artifact=external_call_artifact,
        evaluation=evaluation,
        contamination_checks={"suite_disjoint": True},
        completed_at=NOW,
        cost_usd=None,
    )

    assert result.status == TrialStatus.VERIFIED_SUCCESS
    assert result.primary_authority == AuthorityKind.DETERMINISTIC
    assert result.tokens.total_tokens == 15
    assert result.generation_model_id == execution.student_model.model_id
    assert result.raw_response_digest == response.digest
    assert result.cost_usd is None
    assert result.result_id.startswith("atlas-result-")
