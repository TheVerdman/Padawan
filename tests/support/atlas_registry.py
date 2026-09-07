"""Shared atlas registry fixture setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.atlas.adapters import AlgebraAdapter
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.contracts import (
    AccessClassification,
    AdapterKind,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasRunKind,
    AtlasRunManifest,
    AtlasSnapshot,
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
from padawan.atlas.registry import AtlasRegistry
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import (
    ArtifactRef,
    DistributionScope,
    ResearchRole,
    RightsBasis,
    RightsReviewStatus,
    RightsUse,
    SourceRights,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ContextPolicy,
    ContinuationPolicy,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ParentStateIdentity,
    ResearchAxis,
    ResearchExecutionManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.models.tables import (
    HarnessProfileRow,
    ResearchExecutionRow,
    RunRow,
    StudentRow,
    StudentStateRow,
)

NOW = datetime(2026, 8, 12, tzinfo=UTC)


TASK_DIGEST = sha256_digest("atlas-task")


CORPUS_DIGEST = sha256_digest("atlas-corpus")


ENVIRONMENT_PARAMETERS = {"code_revision": "atlas-test", "python": "3.12"}


ENVIRONMENT_DIGEST = sha256_digest(ENVIRONMENT_PARAMETERS)


def _verifier_record() -> VerifierResult:
    return VerifierResult(
        result_id="atlas-grade",
        verifier_id="exact-match",
        verifier_version="1",
        scope="atlas-test",
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="deterministic exact-match grade",
        evidence={"evaluated_output_digest": sha256_digest("registry output")},
        created_at=NOW,
    )


def _component(component_id: str) -> VersionedComponentIdentity:
    return VersionedComponentIdentity(
        component_id=component_id,
        version="1.0.0",
        digest=sha256_digest(component_id),
        evidence_status=IdentityEvidenceStatus.PINNED,
        evidence=f"pinned test identity for {component_id}",
    )


def _limit(unit: str, value: float) -> BudgetLimit:
    return BudgetLimit(
        disposition=BudgetDisposition.CAPPED,
        scope="request",
        unit=unit,
        value=value,
    )


def _profile() -> HarnessProfile:
    return HarnessProfile(
        profile_id="atlas.harness",
        version="1.0.0",
        tier="standardized",
        purpose="Atlas registry integration test",
        continuation=ContinuationPolicy(
            continuation_mode="none",
            response_storage_enabled=False,
            previous_response_id_enabled=False,
            reasoning_retention_enabled=False,
            reasoning_retention_mode="none",
            private_reasoning_capture_enabled=False,
            private_reasoning_used_as_context=False,
        ),
        context=ContextPolicy(
            policy_id="atlas.context",
            version="1.0.0",
            configured_context_window_tokens=4096,
            effective_input_limit_tokens=3072,
            context_limit_evidence="test configuration",
            token_counting_mode="test",
            history_selection="explicit_messages",
            truncation_enabled=False,
            truncation_strategy="none",
            compaction_enabled=False,
            compaction_strategy="none",
        ),
        prompt_templates=(_component("prompt.atlas"),),
        tools=(_component("tool.none"),),
        budgets=HarnessBudgets(
            actions=_limit("actions", 4),
            input_tokens=_limit("tokens", 4096),
            output_tokens=_limit("tokens", 1024),
            latency=_limit("seconds", 60),
            wall_time=_limit("seconds", 120),
            retries=_limit("retries", 1),
            cost=_limit("usd", 0),
        ),
        created_at=NOW,
    )


def _execution(profile: HarnessProfile) -> ResearchExecutionManifest:
    return ResearchExecutionManifest(
        execution_id="atlas-execution",
        harness_profile_id=profile.profile_id,
        harness_profile_version=profile.version,
        harness_profile_digest=sha256_digest(profile),
        student_model=ModelServingIdentity(
            purpose="student",
            research_role=ResearchRole.TARGET,
            model_id="student-model",
            checkpoint=_component("checkpoint-w8a16"),
            quantization=_component("w8a16"),
            runtime=_component("runtime"),
            serving_artifact=_component("serving-artifact"),
            protocol="responses",
            runtime_parameters={"batch_size": "1", "provider": "test-provider"},
            runtime_parameters_digest=sha256_digest(
                {"batch_size": "1", "provider": "test-provider"}
            ),
        ),
        task=TaskCorpusIdentity(
            task_id="atlas-task",
            task_version="1.0.0",
            task_manifest_digest=TASK_DIGEST,
            corpus_id="atlas-corpus",
            corpus_version="1.0.0",
            corpus_digest=CORPUS_DIGEST,
            split="development",
            evidence_status=IdentityEvidenceStatus.PINNED,
        ),
        parent_state=ParentStateIdentity(
            state_id="atlas-state",
            state_hash=sha256_digest("atlas-state"),
            student_id="atlas-student",
            checkpoint_id="checkpoint-w8a16",
            runtime_id="runtime",
            research_role=ResearchRole.TARGET,
            branch_id="atlas-branch",
        ),
        harness_parameters={"effort": "standard"},
        environment=VersionedComponentIdentity(
            component_id="atlas-environment",
            version="1.0.0",
            digest=ENVIRONMENT_DIGEST,
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="test environment",
        ),
        environment_parameters=ENVIRONMENT_PARAMETERS,
        environment_fingerprint=ENVIRONMENT_DIGEST,
        seed=17,
        created_at=NOW,
    )


def _rights() -> SourceRights:
    return SourceRights(
        rights_id="atlas-test-rights",
        version="1.0.0",
        basis=RightsBasis.PROJECT_AUTHORED,
        basis_detail="project-authored deterministic test item",
        permitted_uses=(RightsUse.EVALUATION, RightsUse.INTERNAL_RESEARCH, RightsUse.SFT),
        distribution_scope=DistributionScope.INTERNAL_ONLY,
        review_status=RightsReviewStatus.CONFIRMED,
        reviewed_by="test-reviewer",
        reviewed_at=NOW,
    )


def _governance(*, access: AccessClassification = AccessClassification.LOCAL) -> DatasetGovernance:
    return DatasetGovernance(
        governance_id=f"governance-{access.value}",
        benchmark_id="padawan.atlas-test",
        benchmark_version="1.0.0",
        dataset_revision=access.value,
        source_url="https://example.test/atlas-suite",
        rights=_rights(),
        access=access,
        redistribution=RedistributionClassification.METADATA_ONLY,
        contamination=ContaminationClassification.NO_KNOWN_EXPOSURE,
        evaluation_class=EvaluationClass.DEVELOPMENT,
        reviewed_at=NOW,
    )


def _item() -> AtlasItemManifest:
    prompt = "Solve 2 + 2."
    identity = {
        "family_id": "arithmetic.boundary",
        "difficulty": 0.5,
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "modalities": (Modality.TEXT,),
        "prompt_digest": sha256_digest(prompt),
        "verifier_id": "exact-match",
        "verifier_version": "1",
        "verifier_payload": {"answer": "4"},
        "metadata": {"source": "project-authored"},
        "pair_id": None,
        "variant_id": None,
    }
    return AtlasItemManifest(
        item_id=content_id("atlas-item", identity),
        item_digest=sha256_digest(identity),
        family_id="arithmetic.boundary",
        difficulty=0.5,
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        modalities=(Modality.TEXT,),
        prompt=prompt,
        prompt_digest=sha256_digest(prompt),
        verifier_id="exact-match",
        verifier_version="1",
        verifier_payload={"answer": "4"},
        metadata={"source": "project-authored"},
    )


def _gate() -> ModalityValidationEvidence:
    return ModalityValidationEvidence(
        modality=Modality.TEXT,
        status=ModalityGateStatus.PASSED,
        gate_id="text-gate",
        gate_revision="1",
        evidence_digest=sha256_digest("text-gate"),
        evidence_refs=("text-gate-evidence",),
        validated_at=NOW,
    )


def _suite(governance: DatasetGovernance, item: AtlasItemManifest) -> AtlasSuiteManifest:
    identity = {
        "suite_id": "atlas-test-suite",
        "version": "1.0.0",
        "benchmark_id": governance.benchmark_id,
        "benchmark_version": governance.benchmark_version,
        "split": "development",
        "governance_id": governance.governance_id,
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "evaluation_class": EvaluationClass.DEVELOPMENT,
        "item_digests": (item.item_digest,),
        "modality_gates": (_gate(),),
        "task_manifest_digests": (TASK_DIGEST,),
        "corpus_digests": (CORPUS_DIGEST,),
        "environment_fingerprints": (ENVIRONMENT_DIGEST,),
        "evaluation_suite_manifest_digest": None,
    }
    return AtlasSuiteManifest(
        suite_id="atlas-test-suite",
        version="1.0.0",
        title="Atlas registry test suite",
        benchmark_id=governance.benchmark_id,
        benchmark_version=governance.benchmark_version,
        split="development",
        governance_id=governance.governance_id,
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        status=SuiteStatus.READY,
        evaluation_class=EvaluationClass.DEVELOPMENT,
        item_digests=(item.item_digest,),
        items=(item,),
        modality_gates=(_gate(),),
        task_manifest_digests=(TASK_DIGEST,),
        corpus_digests=(CORPUS_DIGEST,),
        environment_fingerprints=(ENVIRONMENT_DIGEST,),
        content_digest=sha256_digest(identity),
        created_at=NOW,
    )


def _ontology() -> OntologyManifest:
    provisional = OntologyManifest.model_construct(
        ontology_id="atlas-failure-ontology",
        version="1.0.0",
        nodes=(
            OntologyNode(
                node_id="model.reasoning",
                title="Reasoning failure",
                description="Verified model reasoning failure.",
                origin=FailureOrigin.MODEL,
            ),
        ),
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(mode="json", exclude={"manifest_digest", "created_at"})
    return OntologyManifest(
        **{**provisional.model_dump(mode="python"), "manifest_digest": sha256_digest(identity)}
    )


def _condition(execution_digest: str | None, *, max_requests: int = 2) -> CampaignCondition:
    return CampaignCondition(
        condition_id="standardized",
        title="Standardized harness",
        harness_tier="standardized",
        required_execution_digest=execution_digest,
        required_checkpoint_id="checkpoint-w8a16" if execution_digest else None,
        required_quantization_id="w8a16" if execution_digest else None,
        required_protocol="responses",
        factor_levels={"effort": "standard"},
        max_requests=max_requests,
        max_input_tokens=4096,
        max_output_tokens=1024,
        max_actions=4,
        max_cost_usd=0,
        expected_runtime_minutes=5,
        externally_gated=False,
    )


def _campaign(
    suite_digest: str,
    ontology_digest: str,
    *,
    execution_digest: str | None,
    planned_item_count: int = 1,
    trials_per_item: int = 1,
) -> AtlasCampaignManifest:
    condition = _condition(
        execution_digest,
        max_requests=max(2, planned_item_count * trials_per_item),
    )
    factor = Factor(
        factor_id="effort",
        axis=ResearchAxis.HARNESS,
        levels=(
            FactorLevel(level_id="standard", parameters={"effort": "standard"}),
            FactorLevel(level_id="high", parameters={"effort": "high"}),
        ),
    )
    provisional = AtlasCampaignManifest.model_construct(
        campaign_id=f"campaign-{suite_digest[7:15]}",
        version="1.0.0",
        title="Atlas registry campaign",
        description="Immutable integration-test campaign.",
        status=CampaignStatus.READY,
        ontology_digest=ontology_digest,
        source_claim_ids=(),
        suite_bindings=(
            CampaignSuiteBinding(
                suite_digest=suite_digest,
                evaluation_class=EvaluationClass.DEVELOPMENT,
                planned_item_count=planned_item_count,
                trials_per_item=trials_per_item,
                condition_ids=(condition.condition_id,),
            ),
        ),
        conditions=(condition,),
        factors=(factor,),
        stop_rules=(
            StopRule(
                rule_id="fixed",
                minimum_trials=1,
                maximum_trials=1,
                target_interval_width=0.5,
            ),
        ),
        randomization_seed=17,
        analysis_policy_id="fixed-denominator",
        analysis_policy_version="1",
        promotion_suite_digests=(),
        adaptive_suite_digests=(),
        training_candidate_suite_digests=(suite_digest,),
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"manifest_digest", "status", "created_at"}
    )
    return AtlasCampaignManifest(
        **{**provisional.model_dump(mode="python"), "manifest_digest": sha256_digest(identity)}
    )


async def _seed_controls(session) -> tuple[HarnessProfile, ResearchExecutionManifest]:
    profile = _profile()
    execution = _execution(profile)
    profile_payload = profile.model_dump(mode="json")
    execution_payload = execution.model_dump(mode="json")
    session.add(
        StudentRow(
            student_id="atlas-student",
            research_role="target",
            canonical_state_id=None,
            created_at=NOW,
        )
    )
    session.add(
        StudentStateRow(
            state_id="atlas-state",
            student_id="atlas-student",
            checkpoint_id="checkpoint-w8a16",
            runtime_id="runtime",
            research_role="target",
            parent_state_id=None,
            branch_id="atlas-branch",
            compacted_working_state={},
            lesson_memory_refs=[],
            unresolved_hypotheses=[],
            competency_estimates=[],
            active_experiment_id=None,
            state_hash=sha256_digest("atlas-state"),
            creation_reason="Atlas integration test",
            lifecycle_status="active",
            created_at=NOW,
        )
    )
    session.add(
        HarnessProfileRow(
            profile_digest=sha256_digest(profile_payload),
            profile_id=profile.profile_id,
            version=profile.version,
            tier=profile.tier,
            purpose=profile.purpose,
            record_json=profile_payload,
            created_at=NOW,
        )
    )
    await session.flush()
    session.add(
        ResearchExecutionRow(
            execution_digest=sha256_digest(execution_payload),
            execution_id=execution.execution_id,
            harness_profile_digest=sha256_digest(profile_payload),
            checkpoint_id="checkpoint-w8a16",
            runtime_id="runtime",
            research_role="target",
            parent_state_id="atlas-state",
            parent_state_hash=sha256_digest("atlas-state"),
            task_id="atlas-task",
            task_manifest_digest=TASK_DIGEST,
            corpus_digest=CORPUS_DIGEST,
            environment_fingerprint=ENVIRONMENT_DIGEST,
            seed=17,
            record_json=execution_payload,
            created_at=NOW,
        )
    )
    await session.flush()
    return profile, execution


def _binding(
    campaign: AtlasCampaignManifest,
    suite: AtlasSuiteManifest,
    profile: HarnessProfile,
    execution: ResearchExecutionManifest,
) -> CampaignExecutionBinding:
    identity = {
        "schema_version": "1.0.0",
        "campaign_digest": campaign.manifest_digest,
        "condition_id": "standardized",
        "suite_digest": suite.content_digest,
        "research_execution_digest": sha256_digest(execution),
        "harness_profile_digest": sha256_digest(profile),
        "factor_levels": {"effort": "standard"},
        "external_authorization_ref": None,
        "bound_by": "atlas-test",
    }
    return CampaignExecutionBinding(
        binding_id=content_id("atlas-binding", identity),
        campaign_digest=campaign.manifest_digest,
        condition_id="standardized",
        suite_digest=suite.content_digest,
        research_execution_digest=sha256_digest(execution),
        harness_profile_digest=sha256_digest(profile),
        factor_levels={"effort": "standard"},
        bound_by="atlas-test",
        binding_digest=sha256_digest(identity),
        created_at=NOW,
    )


def _allocation(campaign: AtlasCampaignManifest, suite: AtlasSuiteManifest) -> TrialAllocation:
    return TrialAllocation(
        allocation_id="allocation-1",
        campaign_digest=campaign.manifest_digest,
        condition_id="standardized",
        suite_digest=suite.content_digest,
        item_digest=suite.item_digests[0],
        trial_index=0,
        decision_sequence=0,
        selection_probability=1.0,
        allocation_policy_id="fixed",
        allocation_policy_version="1",
        decision_evidence_digest=sha256_digest("predeclared allocation"),
        created_at=NOW + timedelta(seconds=1),
    )


def _request(
    campaign: AtlasCampaignManifest,
    suite: AtlasSuiteManifest,
    item: AtlasItemManifest,
    execution: ResearchExecutionManifest,
) -> AtlasTrialRequest:
    provisional = AtlasTrialRequest.model_construct(
        request_id="request-1",
        run_id="atlas-run",
        campaign_digest=campaign.manifest_digest,
        research_execution_digest=sha256_digest(execution),
        condition_id="standardized",
        suite_digest=suite.content_digest,
        item_id=item.item_id,
        item_digest=item.item_digest,
        allocation_id="allocation-1",
        trial_index=0,
        attempt_index=0,
        parent_request_id=None,
        prompt_digest=item.prompt_digest,
        rendered_input_digest=item.prompt_digest,
        instructions_digest=sha256_digest("registry test instructions"),
        response_format_digest=sha256_digest({"schema_name": None, "json_schema": None}),
        wire_request_digest=sha256_digest("registry wire request"),
        adapter_id=AlgebraAdapter.descriptor.adapter_id,
        adapter_version=AlgebraAdapter.descriptor.version,
        adapter_descriptor_digest=AlgebraAdapter.descriptor.implementation_digest,
        effort=0.5,
        effort_mapping_evidence_digest=sha256_digest("registry effort mapping"),
        edge_preflight_evidence_digest=sha256_digest("registry edge preflight"),
        context_limit_tokens=1024,
        max_output_tokens=128,
        action_budget=1,
        tool_ids=(),
        tool_manifest_digest=sha256_digest(()),
        sampling={"temperature": "0"},
        request_digest=sha256_digest("pending"),
        created_at=NOW + timedelta(seconds=2),
    )
    identity = provisional.model_dump(mode="json", exclude={"request_digest", "created_at"})
    return AtlasTrialRequest(
        **{**provisional.model_dump(mode="python"), "request_digest": sha256_digest(identity)}
    )


def _run_manifest(
    campaign: AtlasCampaignManifest,
    suite: AtlasSuiteManifest,
    binding: CampaignExecutionBinding,
    profile: HarnessProfile,
    execution: ResearchExecutionManifest,
    request: AtlasTrialRequest,
) -> AtlasRunManifest:
    provisional = AtlasRunManifest.model_construct(
        run_manifest_id="pending",
        run_id="atlas-run",
        run_kind=AtlasRunKind.MODEL_EVALUATION,
        campaign_digest=campaign.manifest_digest,
        campaign_execution_binding_digest=binding.binding_digest,
        condition_id="standardized",
        suite_digest=suite.content_digest,
        research_execution_digest=sha256_digest(execution),
        harness_profile_digest=sha256_digest(profile),
        evaluation_class=EvaluationClass.DEVELOPMENT,
        adaptive=False,
        allocation_policy_id="fixed",
        allocation_policy_version="1",
        planned_request_count=1,
        max_retry_requests=0,
        predeclared_request_digests=(request.request_digest,),
        max_input_tokens=4096,
        max_output_tokens=1024,
        max_actions=4,
        max_cost_usd=0,
        external_execution=False,
        external_authorization_ref=None,
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
    )
    return AtlasRunManifest(
        **{
            **provisional.model_dump(mode="python"),
            "run_manifest_id": content_id("atlas-run", identity),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _rehash_run_manifest(manifest: AtlasRunManifest, **updates: object) -> AtlasRunManifest:
    provisional = manifest.model_copy(
        update={
            **updates,
            "run_manifest_id": "pending",
            "manifest_digest": sha256_digest("pending"),
        }
    )
    identity = provisional.model_dump(
        mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
    )
    return AtlasRunManifest(
        **{
            **provisional.model_dump(mode="python"),
            "run_manifest_id": content_id("atlas-run", identity),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _response_artifact() -> ArtifactRef:
    digest = sha256_digest("4")
    return ArtifactRef(
        artifact_id=f"art-{digest[7:]}",
        uri=f"artifact://sha256/{digest[7:]}",
        digest=digest,
        media_type="text/plain",
        size_bytes=1,
        restricted=True,
        raw_data=True,
    )


def _run_row(run_id: str, execution: ResearchExecutionManifest) -> RunRow:
    return RunRow(
        run_id=run_id,
        episode_id=None,
        student_id=None,
        active_student_id=None,
        research_role="target",
        research_execution_digest=sha256_digest(execution),
        state="created",
        sequence=0,
        payload={},
        retry_count=0,
        retry_budget=1,
        paused=False,
        lease_owner=None,
        lease_token=None,
        lease_expires_at=None,
        last_error=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _trial_result(
    execution: ResearchExecutionManifest,
    request: AtlasTrialRequest,
    *,
    result_id_seed: str = "primary",
    score: float = 1.0,
    retry_count: int = 0,
) -> AtlasTrialResult:
    artifact = _response_artifact()
    verifier_record = _verifier_record()
    evidence = OutcomeEvidence(
        evidence_id="atlas-grade",
        authority=AuthorityKind.DETERMINISTIC,
        verifier_id="exact-match",
        verifier_version="1",
        disposition="verified",
        score=score,
        success=True,
        evaluated_output_digest=sha256_digest("registry output"),
        deterministic=True,
        evidence_digest=sha256_digest(verifier_record.model_dump(mode="json")),
    )
    provisional = AtlasTrialResult.model_construct(
        result_id="pending",
        request_id=request.request_id,
        request_digest=request.request_digest,
        research_execution_digest=sha256_digest(execution),
        generation_provider="test-provider",
        generation_model_id=execution.student_model.model_id,
        generation_protocol=execution.student_model.protocol,
        raw_request_digest=sha256_digest("registry raw request"),
        raw_response_digest=artifact.digest,
        capabilities_digest=sha256_digest("registry capabilities"),
        external_call_artifact=ArtifactRef(
            artifact_id=f"art-{sha256_digest('registry envelope')[7:]}",
            uri=f"artifact://sha256/{sha256_digest('registry envelope')[7:]}",
            digest=sha256_digest("registry envelope"),
            media_type="application/vnd.padawan.generation-result+json",
            size_bytes=17,
            restricted=True,
            raw_data=True,
        ),
        status=TrialStatus.VERIFIED_SUCCESS,
        score=score,
        success=True,
        confidence=1.0,
        abstained=False,
        failure_origin=None,
        failure_codes=(),
        verifier_evidence=(evidence,),
        primary_authority=AuthorityKind.DETERMINISTIC,
        response_digest=artifact.digest,
        response_artifact=artifact,
        grader_artifacts=(),
        tokens=TokenAccounting(
            input_tokens=4,
            output_tokens=1,
            total_tokens=5,
            counting_mode="provider_reported",
        ),
        latency_ms=1,
        wall_time_ms=1,
        cost_usd=0,
        tool_calls=(),
        retry_count=retry_count,
        contamination_checks={"suite_membership": True},
        result_digest=sha256_digest(result_id_seed),
        completed_at=NOW + timedelta(seconds=3),
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


def _snapshot(
    campaign: AtlasCampaignManifest,
    execution: ResearchExecutionManifest,
    *,
    unknown: str,
) -> AtlasSnapshot:
    provisional = AtlasSnapshot.model_construct(
        snapshot_id="pending",
        campaign_digest=campaign.manifest_digest,
        research_execution_digest=sha256_digest(execution),
        harness_profile_digest=execution.harness_profile_digest,
        ontology_digest=campaign.ontology_digest,
        upstream_claim_ids=(),
        local_observations=(),
        curves=(),
        failure_cluster_digests=(),
        unknowns=(unknown,),
        complete=False,
        promotion_eligible=False,
        snapshot_digest=sha256_digest("pending"),
        created_at=NOW + timedelta(seconds=4),
    )
    identity = provisional.model_dump(
        mode="json", exclude={"snapshot_id", "snapshot_digest", "created_at"}
    )
    return AtlasSnapshot(
        **{
            **provisional.model_dump(mode="python"),
            "snapshot_id": content_id("atlas-snapshot", identity),
            "snapshot_digest": sha256_digest(identity),
        }
    )


async def _register_executable_chain(session, *, artifacts=None, governance=None):
    artifacts = artifacts or _artifact_store(session)
    registry = AtlasRegistry(artifacts=AtlasArtifactBoundary(ArtifactCatalog(artifacts)))
    profile, execution = await _seed_controls(session)
    governance = governance or _governance()
    item = _item()
    suite = _suite(governance, item)
    ontology = _ontology()
    await registry.register_dataset_governance(session, governance)
    await registry.register_suite(session, suite)
    await registry.register_ontology(session, ontology)
    campaign = _campaign(
        suite.content_digest,
        ontology.manifest_digest,
        execution_digest=sha256_digest(execution),
    )
    await registry.register_campaign(session, campaign)
    binding = _binding(campaign, suite, profile, execution)
    await registry.register_execution_binding(session, binding)
    allocation = _allocation(campaign, suite)
    await registry.record_allocation(session, allocation)
    request = _request(campaign, suite, item, execution)
    await _seed_preflight_artifacts(
        session, execution=execution, request=request, artifacts=artifacts
    )
    session.add(_run_row(request.run_id, execution))
    await session.flush()
    run_manifest = _run_manifest(campaign, suite, binding, profile, execution, request)
    await registry.register_run_manifest(session, run_manifest)
    return registry, execution, campaign, suite, item, request, run_manifest


def _artifact_store(session) -> LocalArtifactStore:
    return LocalArtifactStore(Path(session.bind.url.database).parent / "atlas-artifacts")


async def _seed_preflight_artifacts(
    session, *, execution: ResearchExecutionManifest, request: AtlasTrialRequest, artifacts=None
) -> None:
    artifacts = artifacts or _artifact_store(session)
    common = {
        "passed": True,
        "research_execution_digest": request.research_execution_digest,
        "provider": "test-provider",
        "model_id": execution.student_model.model_id,
        "protocol": execution.student_model.protocol,
    }
    for kind, digest, extras in (
        ("edge", request.edge_preflight_evidence_digest, {}),
        (
            "effort_mapping",
            request.effort_mapping_evidence_digest,
            {
                "scientific_effort": request.effort,
                "wire_effort": request.sampling.get("reasoning_effort_wire_value"),
            },
        ),
    ):
        if digest is None:
            continue
        artifact = artifacts.put_text(
            "registry edge preflight" if kind == "edge" else "registry effort mapping",
            media_type="application/vnd.padawan.atlas-preflight+json",
            restricted=True,
            raw_data=True,
        )
        assert artifact.digest == digest
        await ArtifactCatalog(artifacts).register(
            session, artifact, metadata={"kind": kind, **common, **extras}
        )
