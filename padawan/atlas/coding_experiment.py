"""Register one finite coding run using existing state, research and Atlas authorities."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from padawan.adapters.prepared import PreparedGenerationClient
from padawan.artifacts.store import (
    ArtifactBackend,
    ArtifactCatalog,
    artifact_put_bytes,
    artifact_read_bytes,
)
from padawan.atlas.activation import AtlasActivation
from padawan.atlas.adapters import CodingAgenticAdapter
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.coding_manifests import coding_suite
from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AtlasItemManifest,
    CampaignCondition,
    CampaignStatus,
    CampaignSuiteBinding,
    DatasetGovernance,
    EvaluationClass,
    Factor,
    FactorLevel,
    FailureOrigin,
    ModalityValidationEvidence,
    OntologyManifest,
    OntologyNode,
    StopRule,
)
from padawan.atlas.harness import (
    build_atlas_execution_manifest,
    build_atlas_harness_profile,
    build_campaign_execution_binding,
)
from padawan.atlas.orchestration import (
    FixedRunConfiguration,
    generation_request_for,
    plan_fixed_suite_run,
)
from padawan.atlas.registry import AtlasRegistry
from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.contracts import ArtifactRef
from padawan.models.database import Database
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import (
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ParentStateIdentity,
    ResearchAxis,
    ResearchExecutionManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.orchestration.state_machine import RunStore
from padawan.state.store import StateStore


async def register_coding_run(
    *,
    database: Database,
    artifacts: ArtifactBackend,
    client: PreparedGenerationClient,
    base_profile: HarnessProfile,
    model: ModelServingIdentity,
    environment: VersionedComponentIdentity,
    environment_parameters: dict[str, str],
    governance: DatasetGovernance,
    items: tuple[AtlasItemManifest, ...],
    text_gate: ModalityValidationEvidence,
    preflight_artifact: ArtifactRef,
    configuration: FixedRunConfiguration,
    repetitions: int,
    run_id: str,
    authorization_ref: str,
    max_in_flight: int,
    starts_at: datetime,
    expires_at: datetime,
    created_at: datetime,
) -> dict[str, Any]:
    """No inference or cloud mutation. Call only after the source preflight is retained.

    The trusted operator supplies the real serving/profile evidence and explicit authorization;
    record construction is not an authentication or attestation mechanism.
    """
    if not items or repetitions < 1 or not authorization_ref:
        raise ValueError("coding registration needs a finite population and explicit authority")
    if not preflight_artifact.restricted or not preflight_artifact.raw_data:
        raise ValueError("source preflight must remain privileged")
    source_preflight = json.loads(
        await artifact_read_bytes(artifacts, preflight_artifact, allow_restricted=True)
    )
    required_preflight = {
        "passed": True,
        "provider": model.runtime_parameters.get("provider"),
        "model_id": model.model_id,
        "protocol": model.protocol,
        "checkpoint_revision": model.checkpoint.version,
        "serving_artifact_digest": model.serving_artifact.digest,
    }
    if "server_configuration_digest" in model.runtime_parameters:
        required_preflight["server_configuration_digest"] = model.runtime_parameters[
            "server_configuration_digest"
        ]
    if not isinstance(source_preflight, dict) or any(
        source_preflight.get(key) != value for key, value in required_preflight.items()
    ):
        raise ValueError(
            "retained source preflight must pass for the exact model, image "
            "and serving configuration"
        )
    if configuration.max_retry_requests:
        raise ValueError("coding experiments have no automatic model retries")
    if configuration.tools:
        from padawan.atlas.coding_tool_contracts import (
            COMPILER_TOOL,
            COMPILER_TOOL_V2,
            SUBMIT_TOOL,
            TOOL_ID,
        )

        if (
            configuration.tools not in ((COMPILER_TOOL,), (COMPILER_TOOL_V2, SUBMIT_TOOL))
            or configuration.tool_ids != (TOOL_ID,)
            or not source_preflight.get("compiler_tool_preflight", {}).get("passed")
        ):
            raise ValueError("compiler trajectories require their exact real tool preflight")
        if configuration.tools == (COMPILER_TOOL_V2, SUBMIT_TOOL) and (
            source_preflight.get("compiler_tool_preflight", {}).get("tool_manifest_digest")
            != configuration.tool_manifest_digest
        ):
            raise ValueError("explicit-submission preflight differs from its exact tool schema")
    if not base_profile.continuation.private_reasoning_capture_enabled:
        raise ValueError("coding scout must capture exposed private reasoning separately")
    count = len(items) * repetitions
    context = base_profile.context.effective_input_limit_tokens
    if context is None:
        raise ValueError("coding scout needs a validated input limit")
    input_allocation = context
    if any(
        tool.component_id == "tool.compile_and_run" and tool.version == "2"
        for tool in base_profile.tools
    ):
        input_allocation = int(base_profile.budgets.input_tokens.value or context)
    env_digest = sha256_digest(environment_parameters)
    if environment.digest != env_digest:
        raise ValueError("coding environment parameters differ from its pinned identity")
    corpus_digest = sha256_digest(tuple(sorted(item.item_digest for item in items)))
    task_digest = sha256_digest(
        {"corpus": corpus_digest, "adapter": CodingAgenticAdapter.descriptor}
    )
    suite = coding_suite(
        items=items,
        governance=governance,
        task_digest=task_digest,
        corpus_digest=corpus_digest,
        environment_fingerprint=env_digest,
        text_gate=text_gate,
        created_at=created_at,
    )
    ontology_payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "ontology_id": "coding-scout.failures",
        "version": "1",
        "nodes": tuple(
            OntologyNode(
                node_id=f"coding.{origin.value}",
                title=origin.value,
                description=f"Coding scout {origin.value} outcome",
                origin=origin,
            )
            for origin in sorted(
                (FailureOrigin.INFRASTRUCTURE, FailureOrigin.MODEL, FailureOrigin.VERIFIER),
                key=lambda value: value.value,
            )
        ),
    }
    ontology = OntologyManifest(
        **ontology_payload, manifest_digest=sha256_digest(ontology_payload), created_at=created_at
    )
    stochastic = configuration.temperature not in {None, 0.0}
    condition = CampaignCondition(
        condition_id="compiler-coding" if configuration.tools else "native-coding",
        title="Compiler-assisted code generation"
        if configuration.tools
        else "Native reasoning code generation",
        harness_tier=base_profile.tier,
        required_checkpoint_id=model.checkpoint.component_id,
        required_quantization_id=model.quantization.component_id,
        required_protocol=model.protocol,
        factor_levels={
            "effort": "native",
            "sampling": "stochastic" if stochastic else "deterministic",
            "tool_access": "compiler" if configuration.tools else "disabled",
        },
        max_requests=count,
        max_input_tokens=count * input_allocation,
        max_output_tokens=count * configuration.max_output_tokens_per_request,
        max_actions=count * configuration.action_budget_per_request,
        max_cost_usd=count * configuration.max_cost_usd_per_request,
        expected_runtime_minutes=max(1, int((expires_at - starts_at).total_seconds() / 60)),
        externally_gated=True,
        blocking_reasons=("explicit operator activation required",),
    )
    campaign_payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "campaign_id": f"coding-{run_id}",
        "version": "1",
        "title": "Frozen coding frontier scout",
        "description": "Exploratory public benchmark diagnostic; no unseen-task or PPRL claim.",
        "ontology_digest": ontology.manifest_digest,
        "source_claim_ids": (),
        "suite_bindings": (
            CampaignSuiteBinding(
                suite_digest=suite.content_digest,
                evaluation_class=EvaluationClass.DEVELOPMENT,
                planned_item_count=len(items),
                trials_per_item=repetitions,
                condition_ids=(condition.condition_id,),
            ),
        ),
        "conditions": (condition,),
        "factors": (
            Factor(
                factor_id="tool_access",
                axis=ResearchAxis.HARNESS,
                levels=tuple(
                    FactorLevel(level_id=value, parameters={"tool_access": value})
                    for value in ("compiler", "disabled")
                ),
            ),
            Factor(
                factor_id="effort",
                axis=ResearchAxis.HARNESS,
                levels=tuple(
                    FactorLevel(level_id=level, parameters={"effort": level})
                    for level in ("native", "disabled")
                ),
            ),
            Factor(
                factor_id="sampling",
                axis=ResearchAxis.HARNESS,
                levels=tuple(
                    FactorLevel(level_id=level, parameters={"sampling": level})
                    for level in ("stochastic", "deterministic")
                ),
            ),
        ),
        "stop_rules": (
            StopRule(
                rule_id="finite",
                minimum_trials=count,
                maximum_trials=count,
                target_interval_width=0.2,
            ),
        ),
        "randomization_seed": configuration.base_seed,
        "analysis_policy_id": "diagnostic-fixed-denominator",
        "analysis_policy_version": "1",
        "promotion_suite_digests": (),
        "adaptive_suite_digests": (),
        "training_candidate_suite_digests": (),
    }
    campaign = AtlasCampaignManifest(
        **campaign_payload,
        status=CampaignStatus.READY,
        manifest_digest=sha256_digest(campaign_payload),
        created_at=created_at,
    )
    profile = build_atlas_harness_profile(
        base=base_profile,
        condition=condition,
        source_revision=environment.digest,
        created_at=created_at,
    )
    catalog = ArtifactCatalog(artifacts)
    registry = AtlasRegistry(artifacts=AtlasArtifactBoundary(catalog))
    controls = ResearchControlRegistry()
    async with database.transaction() as session:
        state = await StateStore().create_student(
            session,
            student_id=f"coding-{run_id}",
            checkpoint_id=model.checkpoint.component_id,
            runtime_id=model.runtime.component_id,
            research_role=model.research_role,
        )
        base = ResearchExecutionManifest(
            execution_id=f"base-{run_id}",
            harness_profile_id=profile.profile_id,
            harness_profile_version=profile.version,
            harness_profile_digest=sha256_digest(profile),
            student_model=model,
            task=TaskCorpusIdentity(
                task_id=suite.suite_id,
                task_version=suite.version,
                task_manifest_digest=task_digest,
                corpus_id=suite.benchmark_id,
                corpus_version=suite.benchmark_version,
                corpus_digest=corpus_digest,
                split="development",
                evidence_status=IdentityEvidenceStatus.PINNED,
            ),
            parent_state=ParentStateIdentity(
                state_id=state.state_id,
                state_hash=state.state_hash,
                student_id=state.student_id,
                checkpoint_id=state.checkpoint_id,
                runtime_id=state.runtime_id,
                research_role=state.research_role,
                branch_id=state.branch_id,
            ),
            harness_parameters={"workflow": "padawan.capability_atlas"},
            environment=environment,
            environment_parameters=environment_parameters,
            environment_fingerprint=env_digest,
            seed=configuration.base_seed,
            created_at=created_at,
        )
        execution = build_atlas_execution_manifest(
            base=base,
            profile=profile,
            campaign_digest=campaign.manifest_digest,
            condition=condition,
            suite=suite,
            seed=configuration.base_seed,
            created_at=created_at,
        )
        await controls.register_profile(session, profile)
        await controls.register_execution(session, execution, parent_state_id=state.state_id)
        await registry.register_dataset_governance(session, governance)
        await registry.register_suite(session, suite)
        await registry.register_ontology(session, ontology)
        await registry.register_campaign(session, campaign)
        binding = build_campaign_execution_binding(
            campaign=campaign,
            condition=condition,
            suite=suite,
            execution=execution,
            bound_by=authorization_ref,
            external_authorization_ref=authorization_ref,
            created_at=created_at,
        )
        await registry.register_execution_binding(session, binding)
        await catalog.register(session, preflight_artifact)
        wrapped_preflight = await artifact_put_bytes(
            artifacts,
            canonical_json_bytes(
                {
                    "source": preflight_artifact,
                    "research_execution_digest": sha256_digest(execution),
                    "purpose": "operator-reviewed serving preflight",
                }
            ),
            media_type="application/vnd.padawan.atlas-preflight+json",
            restricted=True,
            raw_data=True,
        )
        provider = model.runtime_parameters["provider"]
        await catalog.register(
            session,
            wrapped_preflight,
            metadata={
                "kind": "edge",
                "passed": True,
                "research_execution_digest": sha256_digest(execution),
                "provider": provider,
                "model_id": model.model_id,
                "protocol": model.protocol,
            },
        )
        await catalog.reference(
            session, preflight_artifact, owner_type="atlas_preflight_source", owner_id=run_id
        )
        configuration = configuration.model_copy(
            update={"edge_preflight_evidence_digest": wrapped_preflight.digest}
        )
        if configuration.tools:
            tool_preflight = await artifact_put_bytes(
                artifacts,
                canonical_json_bytes(
                    {
                        "source": preflight_artifact,
                        "research_execution_digest": sha256_digest(execution),
                        "tool_manifest_digest": configuration.tool_manifest_digest,
                        "scope": "actual compiler call and public-feedback continuation preflight",
                    }
                ),
                media_type="application/vnd.padawan.atlas-tool-preflight+json",
                restricted=True,
                raw_data=True,
            )
            await catalog.register(
                session,
                tool_preflight,
                metadata={
                    "kind": "tool",
                    "passed": True,
                    "research_execution_digest": sha256_digest(execution),
                    "provider": provider,
                    "model_id": model.model_id,
                    "protocol": model.protocol,
                    "tool_manifest_digest": configuration.tool_manifest_digest,
                },
            )
            configuration = configuration.model_copy(
                update={"tool_preflight_evidence_digest": tool_preflight.digest}
            )
        await RunStore().create(
            session,
            run_id=run_id,
            retry_budget=0,
            research_execution_digest=sha256_digest(execution),
            payload={
                "student_id": state.student_id,
                "state_id": state.state_id,
                "research_role": state.research_role.value,
                "experiment_seed": configuration.base_seed,
                **{
                    key: execution.harness_parameters[key]
                    for key in (
                        "workflow",
                        "atlas_campaign_digest",
                        "atlas_condition_id",
                        "atlas_suite_digest",
                    )
                },
            },
        )
        plan = plan_fixed_suite_run(
            campaign=campaign,
            condition=condition,
            suite=suite,
            binding=binding,
            execution=execution,
            profile=profile,
            run_id=run_id,
            adapter=CodingAgenticAdapter.descriptor,
            configuration=configuration,
            created_at=created_at,
        )
        await registry.register_run_manifest(session, plan.manifest)
        for allocation, request in zip(plan.allocations, plan.requests, strict=True):
            await registry.record_allocation(session, allocation)
            await registry.record_trial_request(session, request)
        first = plan.requests[0]
        generation = generation_request_for(
            request=first,
            item=next(i for i in items if i.item_digest == first.item_digest),
            configuration=configuration,
            profile=profile,
        )
        prepared = client.prepare_generation(generation)
        if prepared.destination is None:
            raise ValueError("coding run requires an explicit prepared HTTP destination")
        if (
            source_preflight.get("destination") != prepared.destination
            or source_preflight.get("configuration_digest") != prepared.configuration_digest
        ):
            raise ValueError(
                "serving preflight differs from the exact prepared endpoint configuration"
            )
        activation = AtlasActivation(
            run_manifest_digest=plan.manifest.manifest_digest,
            authorization_ref=authorization_ref,
            provider=provider,
            destination=prepared.destination,
            configuration_digest=prepared.configuration_digest,
            max_in_flight=max_in_flight,
            starts_at=starts_at,
            expires_at=expires_at,
        )
        activation_ref = await artifact_put_bytes(
            artifacts,
            canonical_json_bytes(activation),
            media_type="application/vnd.padawan.atlas-activation+json",
            restricted=True,
            raw_data=True,
        )
        await catalog.register(session, activation_ref)
    return {
        "manifest": plan.manifest.model_dump(mode="json"),
        "requests": [r.model_dump(mode="json") for r in plan.requests],
        "items": [i.model_dump(mode="json") for i in items],
        "profile": profile.model_dump(mode="json"),
        "execution": execution.model_dump(mode="json"),
        "configuration": configuration.model_dump(mode="json"),
        "readiness": {
            "required_modalities": ["text"],
            "modality_gates": [text_gate.model_dump(mode="json")],
        },
        "activation": activation_ref.model_dump(mode="json"),
    }
