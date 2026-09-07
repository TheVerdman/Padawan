from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts import ArtifactCatalog, LocalArtifactStore
from padawan.atlas.adapters import StaticQAAdapter
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.contracts import (
    AccessClassification,
    AdapterKind,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasRunKind,
    AtlasRunManifest,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    AuthorityKind,
    CampaignCondition,
    CampaignExecutionBinding,
    CampaignStatus,
    CampaignSuiteBinding,
    ContaminationClassification,
    DatasetGovernance,
    EvaluationClass,
    Factor,
    FactorLevel,
    FailureOrigin,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    OntologyManifest,
    OntologyNode,
    OutcomeEvidence,
    RedistributionClassification,
    StopRule,
    SuiteStatus,
    TokenAccounting,
    TrialAllocation,
    TrialStatus,
    content_id,
)
from padawan.atlas.registry import CapabilityAtlasRegistry
from padawan.atlas.studies import (
    ATLAS_FIXED_TRIALS_POLICY_ID,
    ATLAS_FIXED_TRIALS_POLICY_VERSION,
    AtlasFixedTrialStudyBridge,
)
from padawan.checkpoints import CheckpointRegistry
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.contracts import project_authored_internal_rights
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    EvaluationSuiteManifest,
    ResearchAxis,
    ResearchExecutionManifest,
    StudyExperimentBinding,
    StudyManifest,
    StudyStatus,
)
from padawan.models.tables import (
    ArtifactReferenceRow,
    AtlasCampaignSuiteRow,
    CheckpointEvaluationRow,
    ExperimentBlockRow,
    ExternalCallRow,
    StudyResultRow,
)
from padawan.orchestration.state_machine import RunStore
from padawan.rewards import RewardEngine
from padawan.state.store import StateStore
from padawan.studies import StudyEngine
from tests.support.research_controls import _execution, _parent_state, _profile

_NOW = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
_CONDITION = "promotion-standardized"


@dataclass(frozen=True)
class _SeededStudy:
    study_id: str
    campaign_digest: str
    suite_digest: str
    experiment_id: str
    block_ids: tuple[str, ...]
    result_digests: tuple[str, ...]


def _item(index: int) -> AtlasItemManifest:
    prompt = f"Deterministic promotion item {index}"
    identity = {
        "family_id": "atlas.study.bridge",
        "difficulty": 0.75 + index / 100,
        "adapter_kind": AdapterKind.STATIC_QA,
        "modalities": (Modality.TEXT,),
        "prompt_digest": sha256_digest(prompt),
        "verifier_id": "atlas.study.deterministic",
        "verifier_version": "1.0.0",
        "verifier_payload": {"expected": f"answer-{index}"},
        "metadata": {"sealed": True},
        "pair_id": None,
        "variant_id": None,
    }
    return AtlasItemManifest(
        item_id=content_id("atlas-item", identity),
        item_digest=sha256_digest(identity),
        family_id="atlas.study.bridge",
        difficulty=0.75 + index / 100,
        adapter_kind=AdapterKind.STATIC_QA,
        modalities=(Modality.TEXT,),
        prompt=prompt,
        prompt_digest=sha256_digest(prompt),
        verifier_id="atlas.study.deterministic",
        verifier_version="1.0.0",
        verifier_payload={"expected": f"answer-{index}"},
        metadata={"sealed": True},
    )


def _suite(
    *,
    core_suite_digest: str,
    task_digest: str,
    corpus_digest: str,
    environment_fingerprint: str,
    items: tuple[AtlasItemManifest, ...],
) -> AtlasSuiteManifest:
    gate = ModalityValidationEvidence(
        modality=Modality.TEXT,
        status=ModalityGateStatus.PASSED,
        gate_id="text-runtime-contract",
        gate_revision="1.0.0",
        evidence_digest=sha256_digest("text-runtime-contract"),
        evidence_refs=("research-execution",),
        validated_at=_NOW,
    )
    item_digests = tuple(sorted(item.item_digest for item in items))
    ordered_items = tuple(sorted(items, key=lambda item: item.item_digest))
    identity = {
        "suite_id": "atlas-sealed-study-bridge",
        "version": "1.0.0",
        "benchmark_id": "padawan.atlas.study.bridge",
        "benchmark_version": "1.0.0",
        "split": "sealed-promotion",
        "governance_id": "atlas-study-project-authored",
        "adapter_kind": AdapterKind.STATIC_QA,
        "evaluation_class": EvaluationClass.SEALED_PROMOTION,
        "item_digests": item_digests,
        "modality_gates": (gate,),
        "task_manifest_digests": (task_digest,),
        "corpus_digests": (corpus_digest,),
        "environment_fingerprints": (environment_fingerprint,),
        "evaluation_suite_manifest_digest": core_suite_digest,
    }
    return AtlasSuiteManifest(
        suite_id="atlas-sealed-study-bridge",
        version="1.0.0",
        title="Sealed Atlas Study bridge suite",
        benchmark_id="padawan.atlas.study.bridge",
        benchmark_version="1.0.0",
        split="sealed-promotion",
        governance_id="atlas-study-project-authored",
        adapter_kind=AdapterKind.STATIC_QA,
        status=SuiteStatus.SEALED,
        evaluation_class=EvaluationClass.SEALED_PROMOTION,
        item_digests=item_digests,
        items=ordered_items,
        modality_gates=(gate,),
        task_manifest_digests=(task_digest,),
        corpus_digests=(corpus_digest,),
        environment_fingerprints=(environment_fingerprint,),
        evaluation_suite_manifest_digest=core_suite_digest,
        content_digest=sha256_digest(identity),
        created_at=_NOW,
    )


def _ontology() -> OntologyManifest:
    node = OntologyNode(
        node_id="model.reasoning",
        title="Reasoning failure",
        description="A verified behavioral reasoning failure.",
        origin=FailureOrigin.MODEL,
    )
    provisional = OntologyManifest.model_construct(
        ontology_id="atlas-study-ontology",
        version="1.0.0",
        nodes=(node,),
        manifest_digest=sha256_digest("pending"),
        created_at=_NOW,
    )
    identity = provisional.model_dump(mode="json", exclude={"manifest_digest", "created_at"})
    return OntologyManifest(
        ontology_id="atlas-study-ontology",
        version="1.0.0",
        nodes=(node,),
        manifest_digest=sha256_digest(identity),
        created_at=_NOW,
    )


def _campaign(
    *,
    ontology_digest: str,
    suite_digest: str,
    execution_digest: str,
    checkpoint_id: str,
    quantization_id: str,
    harness_tier: str,
    item_count: int,
) -> AtlasCampaignManifest:
    factor = Factor(
        factor_id="effort",
        axis=ResearchAxis.HARNESS,
        levels=(
            FactorLevel(level_id="fixed", parameters={"effort": "0.99"}),
            FactorLevel(level_id="low", parameters={"effort": "0.0"}),
        ),
    )
    condition = CampaignCondition(
        condition_id=_CONDITION,
        title="Fixed sealed promotion",
        harness_tier=harness_tier,
        required_execution_digest=execution_digest,
        required_checkpoint_id=checkpoint_id,
        required_quantization_id=quantization_id,
        required_protocol="responses",
        factor_levels={"effort": "fixed"},
        max_requests=item_count * 2,
        max_input_tokens=100_000,
        max_output_tokens=50_000,
        max_actions=100,
        max_cost_usd=0.0,
        expected_runtime_minutes=10,
        externally_gated=False,
    )
    suite_binding = CampaignSuiteBinding(
        suite_digest=suite_digest,
        evaluation_class=EvaluationClass.SEALED_PROMOTION,
        planned_item_count=item_count,
        trials_per_item=2,
        condition_ids=(_CONDITION,),
        adaptive=False,
    )
    provisional = AtlasCampaignManifest.model_construct(
        campaign_id="atlas-study-campaign",
        version="1.0.0",
        title="Atlas Study bridge campaign",
        description="Predeclared fixed repeated trials for sealed promotion.",
        status=CampaignStatus.READY,
        ontology_digest=ontology_digest,
        source_claim_ids=(),
        suite_bindings=(suite_binding,),
        conditions=(condition,),
        factors=(factor,),
        stop_rules=(
            StopRule(
                rule_id="fixed-complete",
                minimum_trials=item_count * 2,
                maximum_trials=item_count * 2,
                target_interval_width=1.0,
            ),
        ),
        randomization_seed=17,
        analysis_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
        analysis_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
        promotion_suite_digests=(suite_digest,),
        adaptive_suite_digests=(),
        training_candidate_suite_digests=(),
        manifest_digest=sha256_digest("pending"),
        created_at=_NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"manifest_digest", "status", "created_at"}
    )
    return AtlasCampaignManifest(
        campaign_id="atlas-study-campaign",
        version="1.0.0",
        title="Atlas Study bridge campaign",
        description="Predeclared fixed repeated trials for sealed promotion.",
        status=CampaignStatus.READY,
        ontology_digest=ontology_digest,
        source_claim_ids=(),
        suite_bindings=(suite_binding,),
        conditions=(condition,),
        factors=(factor,),
        stop_rules=(
            StopRule(
                rule_id="fixed-complete",
                minimum_trials=item_count * 2,
                maximum_trials=item_count * 2,
                target_interval_width=1.0,
            ),
        ),
        randomization_seed=17,
        analysis_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
        analysis_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
        promotion_suite_digests=(suite_digest,),
        adaptive_suite_digests=(),
        training_candidate_suite_digests=(),
        manifest_digest=sha256_digest(identity),
        created_at=_NOW,
    )


def _execution_binding(
    *, campaign_digest: str, suite_digest: str, execution_digest: str, profile_digest: str
) -> CampaignExecutionBinding:
    provisional = CampaignExecutionBinding.model_construct(
        binding_id="pending",
        campaign_digest=campaign_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        research_execution_digest=execution_digest,
        harness_profile_digest=profile_digest,
        factor_levels={"effort": "fixed"},
        external_authorization_ref=None,
        bound_by="integration-test",
        binding_digest=sha256_digest("pending"),
        created_at=_NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"binding_id", "binding_digest", "created_at"}
    )
    return CampaignExecutionBinding(
        campaign_digest=campaign_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        research_execution_digest=execution_digest,
        harness_profile_digest=profile_digest,
        factor_levels={"effort": "fixed"},
        bound_by="integration-test",
        binding_id=content_id("atlas-binding", identity),
        binding_digest=sha256_digest(identity),
        created_at=_NOW,
    )


def _allocation(
    *,
    campaign_digest: str,
    suite_digest: str,
    item_digest: str,
    trial_index: int,
    decision_sequence: int,
) -> TrialAllocation:
    return TrialAllocation(
        allocation_id=f"allocation-{item_digest[7:19]}-{trial_index}",
        campaign_digest=campaign_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        item_digest=item_digest,
        trial_index=trial_index,
        decision_sequence=decision_sequence,
        selection_probability=1.0,
        allocation_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
        allocation_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
        decision_evidence_digest=sha256_digest(
            {"item_digest": item_digest, "trial_index": trial_index}
        ),
        created_at=_NOW,
    )


def _request(
    *,
    run_id: str,
    campaign_digest: str,
    suite_digest: str,
    execution_digest: str,
    item: AtlasItemManifest,
    allocation: TrialAllocation,
) -> AtlasTrialRequest:
    request_id = f"request-{item.item_digest[7:19]}-{allocation.trial_index}"
    provisional = AtlasTrialRequest.model_construct(
        request_id=request_id,
        run_id=run_id,
        campaign_digest=campaign_digest,
        research_execution_digest=execution_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        item_id=item.item_id,
        item_digest=item.item_digest,
        allocation_id=allocation.allocation_id,
        trial_index=allocation.trial_index,
        attempt_index=0,
        parent_request_id=None,
        prompt_digest=item.prompt_digest,
        rendered_input_digest=item.prompt_digest,
        instructions_digest=sha256_digest("study bridge instructions"),
        response_format_digest=sha256_digest({"schema_name": None, "json_schema": None}),
        wire_request_digest=sha256_digest(f"wire-{request_id}"),
        adapter_id=StaticQAAdapter.descriptor.adapter_id,
        adapter_version=StaticQAAdapter.descriptor.version,
        adapter_descriptor_digest=StaticQAAdapter.descriptor.implementation_digest,
        effort=0.99,
        effort_mapping_evidence_digest=sha256_digest("study effort mapping"),
        edge_preflight_evidence_digest=sha256_digest("study edge preflight"),
        context_limit_tokens=3072,
        max_output_tokens=256,
        action_budget=1,
        tool_ids=(),
        tool_manifest_digest=sha256_digest(()),
        sampling={"temperature": "0"},
        request_digest=sha256_digest("pending"),
        created_at=_NOW,
    )
    identity = provisional.model_dump(mode="json", exclude={"request_digest", "created_at"})
    return AtlasTrialRequest(
        request_id=request_id,
        run_id=run_id,
        campaign_digest=campaign_digest,
        research_execution_digest=execution_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        item_id=item.item_id,
        item_digest=item.item_digest,
        allocation_id=allocation.allocation_id,
        trial_index=allocation.trial_index,
        attempt_index=0,
        prompt_digest=item.prompt_digest,
        rendered_input_digest=item.prompt_digest,
        instructions_digest=sha256_digest("study bridge instructions"),
        response_format_digest=sha256_digest({"schema_name": None, "json_schema": None}),
        wire_request_digest=sha256_digest(f"wire-{request_id}"),
        adapter_id=StaticQAAdapter.descriptor.adapter_id,
        adapter_version=StaticQAAdapter.descriptor.version,
        adapter_descriptor_digest=StaticQAAdapter.descriptor.implementation_digest,
        effort=0.99,
        effort_mapping_evidence_digest=sha256_digest("study effort mapping"),
        edge_preflight_evidence_digest=sha256_digest("study edge preflight"),
        context_limit_tokens=3072,
        max_output_tokens=256,
        action_budget=1,
        tool_manifest_digest=sha256_digest(()),
        sampling={"temperature": "0"},
        request_digest=sha256_digest(identity),
        created_at=_NOW,
    )


def _run_manifest(
    *,
    run_id: str,
    campaign_digest: str,
    binding: CampaignExecutionBinding,
    suite_digest: str,
    execution_digest: str,
    profile_digest: str,
    requests: tuple[AtlasTrialRequest, ...],
) -> AtlasRunManifest:
    predeclared = tuple(sorted(request.request_digest for request in requests))
    provisional = AtlasRunManifest.model_construct(
        run_manifest_id="pending",
        run_id=run_id,
        run_kind=AtlasRunKind.MODEL_EVALUATION,
        campaign_digest=campaign_digest,
        campaign_execution_binding_digest=binding.binding_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        research_execution_digest=execution_digest,
        harness_profile_digest=profile_digest,
        evaluation_class=EvaluationClass.SEALED_PROMOTION,
        adaptive=False,
        allocation_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
        allocation_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
        planned_request_count=len(requests),
        predeclared_request_digests=predeclared,
        max_input_tokens=100_000,
        max_output_tokens=50_000,
        max_actions=100,
        max_cost_usd=0.0,
        external_execution=False,
        external_authorization_ref=None,
        manifest_digest=sha256_digest("pending"),
        created_at=_NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
    )
    return AtlasRunManifest(
        run_manifest_id=content_id("atlas-run", identity),
        run_id=run_id,
        run_kind=AtlasRunKind.MODEL_EVALUATION,
        campaign_digest=campaign_digest,
        campaign_execution_binding_digest=binding.binding_digest,
        condition_id=_CONDITION,
        suite_digest=suite_digest,
        research_execution_digest=execution_digest,
        harness_profile_digest=profile_digest,
        evaluation_class=EvaluationClass.SEALED_PROMOTION,
        adaptive=False,
        allocation_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
        allocation_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
        planned_request_count=len(requests),
        predeclared_request_digests=predeclared,
        max_input_tokens=100_000,
        max_output_tokens=50_000,
        max_actions=100,
        max_cost_usd=0.0,
        external_execution=False,
        manifest_digest=sha256_digest(identity),
        created_at=_NOW,
    )


async def _result(
    *,
    session: AsyncSession,
    tmp_path: Path,
    request: AtlasTrialRequest,
    execution_manifest: ResearchExecutionManifest,
    status: TrialStatus,
    index: int,
) -> AtlasTrialResult:
    observed = status in {
        TrialStatus.VERIFIED_SUCCESS,
        TrialStatus.VERIFIED_FAILURE,
        TrialStatus.PARTIAL,
    }
    response = None
    generation_observed = observed or status == TrialStatus.CONTAMINATED
    evidence: tuple[OutcomeEvidence, ...] = ()
    if generation_observed:
        artifact_store = LocalArtifactStore(tmp_path / "artifacts")
        catalog = ArtifactCatalog(artifact_store)
        response = artifact_store.put_text(f"response-{index}", restricted=True, raw_data=True)
        await catalog.register(session, response)
        envelope = artifact_store.put_bytes(
            f"generation-envelope-{index}".encode(),
            media_type="application/vnd.padawan.generation-result+json",
            restricted=True,
            raw_data=True,
        )
        await catalog.register(session, envelope)
        provider = execution_manifest.student_model.runtime_parameters["provider"]
        session.add(
            ExternalCallRow(
                request_id=request.request_id,
                run_id=request.run_id,
                purpose="capability_atlas",
                provider=provider,
                request_hash=request.wire_request_digest,
                request_artifact_id=response.artifact_id,
                response_artifact_id=envelope.artifact_id,
                provider_response_id=f"study-response-{index}",
                status="completed",
                error=None,
                result_envelope_digest=envelope.digest,
                result_model_id=execution_manifest.student_model.model_id,
                result_protocol=execution_manifest.student_model.protocol,
                result_raw_request_digest=sha256_digest(f"study raw request {index}"),
                result_raw_response_digest=response.digest,
                result_output_text_digest=sha256_digest("study output"),
                result_usage=(
                    {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25} if observed else {}
                ),
                result_capabilities_digest=sha256_digest("study capabilities"),
                result_latency_ms=None,
                created_at=request.created_at,
                completed_at=_NOW + timedelta(minutes=1, seconds=index),
            )
        )
        verifier = VerifierResult(
            result_id=f"verifier-atlas-study-{index}",
            verifier_id="atlas.study.deterministic",
            verifier_version="1.0.0",
            scope=request.request_id,
            disposition=(
                VerifierDisposition.VERIFIED
                if status == TrialStatus.VERIFIED_SUCCESS
                else VerifierDisposition.REJECTED
            ),
            deterministic=True,
            summary="deterministic fixed-trial grade",
            evidence={"request_digest": request.request_digest},
            created_at=_NOW + timedelta(seconds=index),
        )
        verifier_row = await RewardEngine().record_verifier_result(session, verifier)
        evidence = (
            (
                OutcomeEvidence(
                    evidence_id=verifier.result_id,
                    authority=AuthorityKind.DETERMINISTIC,
                    verifier_id=verifier.verifier_id,
                    verifier_version=verifier.verifier_version,
                    disposition=verifier.disposition.value,
                    score=(
                        1.0
                        if status == TrialStatus.VERIFIED_SUCCESS
                        else 0.5
                        if status == TrialStatus.PARTIAL
                        else 0.0
                    ),
                    success=status == TrialStatus.VERIFIED_SUCCESS,
                    evaluated_output_digest=sha256_digest("study output"),
                    deterministic=True,
                    evidence_digest=verifier_row.record_digest,
                ),
            )
            if observed
            else ()
        )
    elif status in {TrialStatus.TIMEOUT, TrialStatus.INFRASTRUCTURE_FAILURE}:
        provider = execution_manifest.student_model.runtime_parameters["provider"]
        artifact_store = LocalArtifactStore(tmp_path / "artifacts")
        failed_request = artifact_store.put_text(
            f"failed-request-{index}", restricted=True, raw_data=True
        )
        await ArtifactCatalog(artifact_store).register(session, failed_request)
        session.add(
            ExternalCallRow(
                request_id=request.request_id,
                run_id=request.run_id,
                purpose="capability_atlas",
                provider=provider,
                request_hash=request.wire_request_digest,
                request_artifact_id=failed_request.artifact_id,
                response_artifact_id=None,
                provider_response_id=None,
                status="failed_retryable",
                error={
                    "classification": (
                        "timeout" if status == TrialStatus.TIMEOUT else "provider_error"
                    ),
                    "retryable": True,
                },
                created_at=request.created_at,
                completed_at=_NOW + timedelta(minutes=1, seconds=index),
            )
        )
        await session.flush()
    failure_origin = None
    contamination_checks = {"suite_membership": True}
    if status in {
        TrialStatus.TIMEOUT,
        TrialStatus.INFRASTRUCTURE_FAILURE,
        TrialStatus.NOT_RUN,
    }:
        failure_origin = FailureOrigin.INFRASTRUCTURE
    elif status == TrialStatus.CONTAMINATED:
        failure_origin = FailureOrigin.CONTAMINATION
        contamination_checks = {"suite_membership": False}
    success = status == TrialStatus.VERIFIED_SUCCESS if observed else None
    score = (
        1.0
        if status == TrialStatus.VERIFIED_SUCCESS
        else 0.5
        if status == TrialStatus.PARTIAL
        else 0.0
        if observed
        else None
    )
    tokens = (
        TokenAccounting(
            input_tokens=20,
            output_tokens=5,
            total_tokens=25,
            counting_mode="provider_reported",
        )
        if observed
        else TokenAccounting(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            counting_mode="provider_reported_unusable",
            missing_reason="provider usage was missing or internally inconsistent",
        )
    )
    provisional = AtlasTrialResult.model_construct(
        result_id="pending",
        request_id=request.request_id,
        request_digest=request.request_digest,
        research_execution_digest=request.research_execution_digest,
        generation_provider=(
            execution_manifest.student_model.runtime_parameters["provider"]
            if generation_observed
            else None
        ),
        generation_model_id=(
            execution_manifest.student_model.model_id if generation_observed else None
        ),
        generation_protocol=(
            execution_manifest.student_model.protocol if generation_observed else None
        ),
        raw_request_digest=(
            sha256_digest(f"study raw request {index}") if generation_observed else None
        ),
        raw_response_digest=response.digest if response else None,
        capabilities_digest=(sha256_digest("study capabilities") if generation_observed else None),
        external_call_artifact=envelope if generation_observed else None,
        status=status,
        score=score,
        success=success,
        confidence=None,
        abstained=False,
        failure_origin=failure_origin,
        failure_codes=("not_run:fixed_test_design",) if status == TrialStatus.NOT_RUN else (),
        verifier_evidence=evidence,
        primary_authority=AuthorityKind.DETERMINISTIC if observed else None,
        response_digest=response.digest if response else None,
        response_artifact=response,
        grader_artifacts=(),
        tokens=tokens,
        latency_ms=None,
        wall_time_ms=None,
        cost_usd=None,
        tool_calls=(),
        retry_count=0,
        contamination_checks=contamination_checks,
        result_digest=sha256_digest("pending"),
        completed_at=_NOW + timedelta(minutes=1, seconds=index),
    )
    identity = provisional.model_dump(
        mode="json", exclude={"result_id", "result_digest", "completed_at"}
    )
    return AtlasTrialResult(
        result_id=content_id("atlas-result", identity),
        request_id=request.request_id,
        request_digest=request.request_digest,
        research_execution_digest=request.research_execution_digest,
        generation_provider=(
            execution_manifest.student_model.runtime_parameters["provider"]
            if generation_observed
            else None
        ),
        generation_model_id=(
            execution_manifest.student_model.model_id if generation_observed else None
        ),
        generation_protocol=(
            execution_manifest.student_model.protocol if generation_observed else None
        ),
        raw_request_digest=(
            sha256_digest(f"study raw request {index}") if generation_observed else None
        ),
        raw_response_digest=response.digest if response else None,
        capabilities_digest=(sha256_digest("study capabilities") if generation_observed else None),
        external_call_artifact=envelope if generation_observed else None,
        status=status,
        score=score,
        success=success,
        failure_origin=failure_origin,
        failure_codes=("not_run:fixed_test_design",) if status == TrialStatus.NOT_RUN else (),
        verifier_evidence=evidence,
        primary_authority=AuthorityKind.DETERMINISTIC if observed else None,
        response_digest=response.digest if response else None,
        response_artifact=response,
        tokens=tokens,
        contamination_checks=contamination_checks,
        result_digest=sha256_digest(identity),
        completed_at=_NOW + timedelta(minutes=1, seconds=index),
    )


async def _seed_study(
    database: Database, tmp_path: Path, statuses: tuple[TrialStatus, ...]
) -> _SeededStudy:
    catalog = ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts"))
    boundary = AtlasArtifactBoundary(catalog)
    atlas = CapabilityAtlasRegistry(artifacts=boundary)
    controls = ResearchControlRegistry()
    checkpoints = CheckpointRegistry()
    states = StateStore()
    bridge = AtlasFixedTrialStudyBridge(artifacts=boundary)
    items = (_item(0), _item(1))
    assert len(statuses) == 4
    async with database.transaction() as session:
        state = await states.create_student(
            session,
            student_id="atlas-study-student",
            checkpoint_id="checkpoint-atlas-study",
            runtime_id="runtime",
        )
        profile = _profile(profile_id="atlas.study.harness")
        await controls.register_profile(session, profile)
        execution_manifest = _execution(
            profile,
            execution_id="atlas-study-execution",
            checkpoint_id=state.checkpoint_id,
            runtime_id=state.runtime_id,
            parent_state=_parent_state(state),
        )
        student_parameters = dict(
            sorted(
                {
                    **execution_manifest.student_model.runtime_parameters,
                    "provider": "test-provider",
                }.items()
            )
        )
        execution_manifest = execution_manifest.model_copy(
            update={
                "student_model": execution_manifest.student_model.model_copy(
                    update={
                        "runtime_parameters": student_parameters,
                        "runtime_parameters_digest": sha256_digest(student_parameters),
                    }
                )
            }
        )
        execution = await controls.register_execution(
            session, execution_manifest, parent_state_id=state.state_id
        )
        core_suite = EvaluationSuiteManifest(
            suite_id="atlas-study-core-suite",
            version="1.0.0",
            task_manifest_digests=(execution.task_manifest_digest,),
            environment_fingerprints=(execution.environment_fingerprint,),
            sealed=True,
            created_at=_NOW,
        )
        core_suite_digest = await checkpoints.register_suite(session, core_suite)
        governance = DatasetGovernance(
            governance_id="atlas-study-project-authored",
            benchmark_id="padawan.atlas.study.bridge",
            benchmark_version="1.0.0",
            dataset_revision="1.0.0",
            source_url="https://example.invalid/padawan-atlas-study",
            rights=project_authored_internal_rights(reviewed_at=_NOW),
            access=AccessClassification.LOCAL,
            redistribution=RedistributionClassification.METADATA_ONLY,
            contamination=ContaminationClassification.NO_KNOWN_EXPOSURE,
            evaluation_class=EvaluationClass.SEALED_PROMOTION,
            reviewed_at=_NOW,
        )
        await atlas.register_dataset_governance(session, governance)
        suite = _suite(
            core_suite_digest=core_suite_digest,
            task_digest=execution.task_manifest_digest,
            corpus_digest=execution.corpus_digest,
            environment_fingerprint=execution.environment_fingerprint,
            items=items,
        )
        await atlas.register_suite(session, suite)
        ontology = _ontology()
        await atlas.register_ontology(session, ontology)
        campaign = _campaign(
            ontology_digest=ontology.manifest_digest,
            suite_digest=suite.content_digest,
            execution_digest=execution.execution_digest,
            checkpoint_id=execution.checkpoint_id,
            quantization_id=execution_manifest.student_model.quantization.component_id,
            harness_tier=profile.tier,
            item_count=len(items),
        )
        await atlas.register_campaign(session, campaign)
        binding = _execution_binding(
            campaign_digest=campaign.manifest_digest,
            suite_digest=suite.content_digest,
            execution_digest=execution.execution_digest,
            profile_digest=execution.harness_profile_digest,
        )
        await atlas.register_execution_binding(session, binding)
        experiment = await bridge.create_experiment(
            session,
            experiment_id="atlas-study-experiment",
            parent_state_id=state.state_id,
            campaign_digest=campaign.manifest_digest,
            suite_digest=suite.content_digest,
            condition_id=_CONDITION,
            research_execution_digest=execution.execution_digest,
            created_at=_NOW,
        )
        allocations = tuple(
            _allocation(
                campaign_digest=campaign.manifest_digest,
                suite_digest=suite.content_digest,
                item_digest=item.item_digest,
                trial_index=trial_index,
                decision_sequence=item_position * 2 + trial_index,
            )
            for item_position, item in enumerate(sorted(items, key=lambda value: value.item_digest))
            for trial_index in range(2)
        )
        for allocation in allocations:
            await atlas.record_allocation(session, allocation)
        run_id = await RunStore().create(
            session,
            run_id="atlas-study-run",
            research_execution_digest=execution.execution_digest,
            payload={
                "student_id": state.student_id,
                "state_id": state.state_id,
                "research_role": state.research_role.value,
                "domain_id": execution_manifest.task.task_id,
                "pool": execution_manifest.task.split,
                "experiment_seed": execution.seed,
                "teacher_mode": execution_manifest.harness_parameters["teacher_mode"],
                "treatment_condition": execution_manifest.harness_parameters["treatment_condition"],
                "control_condition": execution_manifest.harness_parameters["control_condition"],
            },
        )
        by_digest = {item.item_digest: item for item in items}
        requests = tuple(
            _request(
                run_id=run_id,
                campaign_digest=campaign.manifest_digest,
                suite_digest=suite.content_digest,
                execution_digest=execution.execution_digest,
                item=by_digest[allocation.item_digest],
                allocation=allocation,
            )
            for allocation in allocations
        )
        representative = requests[0]
        for kind, digest, extras in (
            ("edge", representative.edge_preflight_evidence_digest, {}),
            (
                "effort_mapping",
                representative.effort_mapping_evidence_digest,
                {
                    "scientific_effort": representative.effort,
                    "wire_effort": representative.sampling.get("reasoning_effort_wire_value"),
                },
            ),
        ):
            assert digest is not None
            preflight = catalog.backend.put_bytes(
                ("study edge preflight" if kind == "edge" else "study effort mapping").encode(),
                media_type="application/vnd.padawan.atlas-preflight+json",
                restricted=True,
                raw_data=True,
            )
            assert preflight.digest == digest
            await catalog.register(
                session,
                preflight,
                metadata={
                    "kind": kind,
                    "passed": True,
                    "research_execution_digest": execution.execution_digest,
                    "provider": execution_manifest.student_model.runtime_parameters["provider"],
                    "model_id": execution_manifest.student_model.model_id,
                    "protocol": execution_manifest.student_model.protocol,
                    **extras,
                },
            )
        await session.flush()
        await atlas.register_run_manifest(
            session,
            _run_manifest(
                run_id=run_id,
                campaign_digest=campaign.manifest_digest,
                binding=binding,
                suite_digest=suite.content_digest,
                execution_digest=execution.execution_digest,
                profile_digest=execution.harness_profile_digest,
                requests=requests,
            ),
        )
        result_digests: list[str] = []
        for index, (request, status) in enumerate(zip(requests, statuses, strict=True)):
            await atlas.record_trial_request(session, request)
            result = await _result(
                session=session,
                tmp_path=tmp_path,
                request=request,
                execution_manifest=execution_manifest,
                status=status,
                index=index,
            )
            await atlas.record_trial_result(session, result)
            await bridge.record_result(
                session,
                block_id=experiment.block_ids[index],
                result_digest=result.result_digest,
            )
            result_digests.append(result.result_digest)
        study_id = "atlas-fixed-trial-study"
        await StudyEngine().create(
            session,
            StudyManifest(
                study_id=study_id,
                version=1,
                title="Atlas fixed-trial sealed promotion",
                description="Seal immutable Atlas trials through the authoritative Study ledger.",
                seed=execution.seed,
                suite_manifest_digest=core_suite_digest,
                aggregation_policy_id=ATLAS_FIXED_TRIALS_POLICY_ID,
                aggregation_policy_version=ATLAS_FIXED_TRIALS_POLICY_VERSION,
                experiments=(
                    StudyExperimentBinding(
                        experiment_id=experiment.experiment_id,
                        condition_id=_CONDITION,
                        checkpoint_id=execution.checkpoint_id,
                        research_role=state.research_role,
                        suite_manifest_digest=core_suite_digest,
                        environment_fingerprint=execution.environment_fingerprint,
                        research_execution_digest=execution.execution_digest,
                    ),
                ),
                created_at=_NOW,
            ),
            status=StudyStatus.ACTIVE,
        )
    return _SeededStudy(
        study_id=study_id,
        campaign_digest=campaign.manifest_digest,
        suite_digest=suite.content_digest,
        experiment_id=experiment.experiment_id,
        block_ids=experiment.block_ids,
        result_digests=tuple(result_digests),
    )


async def test_fixed_trials_seal_ordinary_study_evidence_without_checkpoint_write(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.VERIFIED_FAILURE,
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.VERIFIED_FAILURE,
        ),
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        await studies.transition(
            session,
            study_id=seeded.study_id,
            to_status=StudyStatus.COMPLETE,
            completed_at=_NOW + timedelta(hours=1),
        )
        result = await studies.result(
            session,
            study_id=seeded.study_id,
            condition_id=_CONDITION,
            checkpoint_id="checkpoint-atlas-study",
        )
        checkpoint_evaluations = await session.scalar(
            select(func.count()).select_from(CheckpointEvaluationRow)
        )
        sealed_row = await session.get(StudyResultRow, result.result_id)
    metrics = {metric.metric_id: metric.value for metric in result.metrics}
    assert result.total_blocks == 4
    assert result.analyzed_blocks == 4
    assert result.causal_claim_permitted
    assert metrics["verified_success_rate"] == 0.5
    assert metrics["verified_success_rate_fixed_denominator"] == 0.5
    assert len({block.block_digest for block in result.source_blocks}) == 4
    assert sealed_row is not None
    assert sealed_row.record_json == result.model_dump(mode="json")
    assert sealed_row.record_digest == sha256_digest(sealed_row.record_json)
    assert checkpoint_evaluations == 0


async def test_fixed_trials_preserve_infrastructure_and_contamination_missingness(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.TIMEOUT,
            TrialStatus.CONTAMINATED,
            TrialStatus.NOT_RUN,
        ),
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        await studies.transition(
            session,
            study_id=seeded.study_id,
            to_status=StudyStatus.COMPLETE,
            completed_at=_NOW + timedelta(hours=1),
        )
        result = await studies.result(
            session,
            study_id=seeded.study_id,
            condition_id=_CONDITION,
            checkpoint_id="checkpoint-atlas-study",
        )
    metrics = {metric.metric_id: metric.value for metric in result.metrics}
    assert result.analyzed_blocks == 1
    assert result.excluded_infrastructure == 2
    assert result.excluded_contaminated == 1
    assert not result.causal_claim_permitted
    assert metrics["verified_success_rate"] == 1.0
    assert metrics["verified_success_rate_fixed_denominator"] == 0.25
    assert metrics["timeout_trials"] == 1.0
    assert metrics["not_run_trials"] == 1.0


async def test_fixed_trials_reject_duplicate_result_inflation_and_suite_substitution(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (TrialStatus.VERIFIED_SUCCESS,) * 4,
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        first = await session.get(ExperimentBlockRow, seeded.block_ids[0])
        second = await session.get(ExperimentBlockRow, seeded.block_ids[1])
        assert first is not None and second is not None
        second.assignment = dict(first.assignment)
        second.outcomes = dict(first.outcomes or {})
        with pytest.raises(ValueError, match="duplicate coordinate"):
            await studies.transition(
                session,
                study_id=seeded.study_id,
                to_status=StudyStatus.COMPLETE,
            )


async def test_fixed_trials_never_promote_partial_or_unaccounted_outcomes(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.PARTIAL,
            TrialStatus.VERIFIED_FAILURE,
            TrialStatus.VERIFIED_SUCCESS,
        ),
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        await studies.transition(
            session,
            study_id=seeded.study_id,
            to_status=StudyStatus.COMPLETE,
            completed_at=_NOW + timedelta(hours=1),
        )
        result = await studies.result(
            session,
            study_id=seeded.study_id,
            condition_id=_CONDITION,
            checkpoint_id="checkpoint-atlas-study",
        )
    metrics = {metric.metric_id: metric.value for metric in result.metrics}
    assert result.analyzed_blocks == result.total_blocks
    assert metrics["partial_trials"] == 1.0
    assert not result.causal_claim_permitted


async def test_fixed_trials_reject_unaccounted_fixed_block(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (TrialStatus.VERIFIED_SUCCESS,) * 4,
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        await session.execute(
            update(ExperimentBlockRow)
            .where(ExperimentBlockRow.block_id == seeded.block_ids[-1])
            .values(outcomes=None)
        )
        with pytest.raises(ValueError, match="unfinished blocks"):
            await studies.transition(
                session,
                study_id=seeded.study_id,
                to_status=StudyStatus.COMPLETE,
            )


async def test_fixed_trials_reject_adaptive_design_drift(
    database: Database, tmp_path: Path
) -> None:
    seeded = await _seed_study(
        database,
        tmp_path,
        (TrialStatus.VERIFIED_SUCCESS,) * 4,
    )
    studies = StudyEngine(artifacts=ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts")))
    async with database.transaction() as session:
        await session.execute(
            update(AtlasCampaignSuiteRow)
            .where(
                AtlasCampaignSuiteRow.campaign_digest == seeded.campaign_digest,
                AtlasCampaignSuiteRow.suite_digest == seeded.suite_digest,
            )
            .values(adaptive=True)
        )
        with pytest.raises(ValueError, match="full fixed design"):
            await studies.transition(
                session,
                study_id=seeded.study_id,
                to_status=StudyStatus.COMPLETE,
            )


@pytest.mark.parametrize("missing", ["backend", "ownership", "blob"])
async def test_atlas_study_sealing_requires_retained_physical_evidence(database, tmp_path, missing):
    seeded = await _seed_study(database, tmp_path, (TrialStatus.VERIFIED_SUCCESS,) * 4)
    catalog = ArtifactCatalog(LocalArtifactStore(tmp_path / "artifacts"))
    studies = StudyEngine(artifacts=None if missing == "backend" else catalog)
    async with database.transaction() as session:
        if missing == "ownership":
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "atlas_trial_result"
                )
            )
        elif missing == "blob":
            reference = catalog.backend.put_bytes(
                b"response-0",
                media_type="text/plain; charset=utf-8",
                restricted=True,
                raw_data=True,
            )
            catalog.backend._path_for_hex(reference.digest[7:]).unlink()
    async with database.transaction() as session:
        with pytest.raises((OSError, ValueError)):
            await studies.transition(
                session, study_id=seeded.study_id, to_status=StudyStatus.COMPLETE
            )
        assert await session.scalar(select(func.count()).select_from(StudyResultRow)) == 0
