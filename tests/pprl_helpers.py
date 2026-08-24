from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from padawan.governance.amber import (
    AmberAuthorizationEnvelope,
    AmberBudgetCaps,
    AmberCheckpointPolicy,
    AmberEgressMode,
    AmberEnvironmentBoundary,
    AmberReleaseClass,
    AmberToolGrant,
)
from padawan.models.contracts import ResearchRole, project_authored_internal_rights
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    IdentityEvidenceStatus,
    ModelServingIdentity,
    VersionedComponentIdentity,
)
from padawan.pprl.contracts import (
    DistributionPartition,
    PersistenceMode,
    ProcessDistributionManifest,
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectInstance,
    ProjectSplit,
    ReplicationPolicy,
    RewardAuthorityKind,
    RewardAuthoritySpec,
    WorkerRoleSpec,
)
from padawan.pprl.distributions import GeneratedProject

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def component(component_id: str, *, digest_source: str | None = None) -> VersionedComponentIdentity:
    return VersionedComponentIdentity(
        component_id=component_id,
        version="1.0.0",
        digest=sha256_digest(digest_source or component_id),
        evidence_status=IdentityEvidenceStatus.PINNED,
        evidence="test fixture identity",
    )


def distribution() -> ProcessDistributionManifest:
    return ProcessDistributionManifest(
        distribution_id="test.scientific-projects",
        version="1.0.0",
        title="Scientific project fixtures",
        population="deterministic project tasks",
        generator=component("test.project-generator"),
        rights=project_authored_internal_rights(reviewed_at=NOW),
        partitions=tuple(
            DistributionPartition(
                split=split,
                sampling_weight=0.25,
                generator_parameters={"family": split.value},
                contamination_scope=f"test-{split.value}",
            )
            for split in sorted(ProjectSplit, key=lambda item: item.value)
        ),
        replication=ReplicationPolicy(
            minimum_unique_instances=2,
            minimum_rollouts_per_instance=2,
            maximum_rollouts_per_instance=4,
            paired_checkpoint_forks=True,
        ),
        difficulty_strata=("standard",),
        seed_namespace="padawan-test-pprl",
        created_at=NOW,
    )


def program(distribution_digest: str) -> ProcessProgram:
    return ProcessProgram(
        program_id="test.pprl-program",
        version="1.0.0",
        title="Test persistent process",
        objective_family="solve deterministic scientific projects",
        persistence_mode=PersistenceMode.EPISODIC,
        distribution_digest=distribution_digest,
        reward_authority=RewardAuthoritySpec(
            authority_id="test.verifier",
            version="1.0.0",
            kind=RewardAuthorityKind.VERIFIABLE,
            description="deterministic test verifier",
        ),
        worker_roles=(
            WorkerRoleSpec(
                role_id="researcher",
                description="executes one deterministic research action",
                required_capabilities=("reasoning",),
                allowed_tool_ids=("calculator",),
                minimum_instances=1,
                maximum_instances=1,
            ),
        ),
        tools=(component("calculator"),),
        maximum_concurrent_workers=1,
        created_at=NOW,
    )


def worker_model() -> ModelServingIdentity:
    parameters = {"max_model_len": "4096"}
    return ModelServingIdentity(
        purpose="process_worker",
        research_role=ResearchRole.TARGET,
        model_id="test-open-weight-model",
        checkpoint=component("test.worker-checkpoint"),
        quantization=component("test.worker-quantization"),
        runtime=component("test.worker-runtime"),
        serving_artifact=component("test.worker-serving"),
        protocol="responses",
        runtime_parameters=parameters,
        runtime_parameters_digest=sha256_digest(parameters),
    )


def environment_parameters() -> dict[str, str]:
    return {"sandbox": "deterministic"}


def envelope(
    *,
    program_digest: str,
    distribution_digest: str,
    model: ModelServingIdentity | None = None,
) -> AmberAuthorizationEnvelope:
    selected_model = model or worker_model()
    parameters = environment_parameters()
    fingerprint = sha256_digest(parameters)
    return AmberAuthorizationEnvelope(
        authorization_id="test.amber",
        version="1.0.0",
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        allowed_splits=tuple(sorted(ProjectSplit, key=lambda item: item.value)),
        allowed_target_classes=("scientific_math",),
        allowed_worker_model_digests=(sha256_digest(selected_model),),
        tool_grants=(
            AmberToolGrant(
                role_id="researcher",
                tool_id="calculator",
                tool_digest=component("calculator").digest,
                operations=("evaluate",),
                target_classes=("scientific_math",),
            ),
        ),
        environment=AmberEnvironmentBoundary(
            environment_fingerprint=fingerprint,
            sandbox_id="test-sandbox",
            sandbox_version="1.0.0",
            sandbox_digest=sha256_digest("test-sandbox"),
            reset_between_rollouts=True,
            network_enabled=False,
            egress_mode=AmberEgressMode.DISABLED,
            filesystem_scopes=("project-artifacts",),
        ),
        budgets=AmberBudgetCaps(
            actions=20,
            input_tokens=100_000,
            output_tokens=100_000,
            wall_time_seconds=3600.0,
            cost=100.0,
            artifact_bytes=10_000_000,
            concurrent_workers=1,
        ),
        persistence_modes=(PersistenceMode.EPISODIC,),
        cross_project_memory_permitted=False,
        checkpoint_policy=AmberCheckpointPolicy(
            training_permitted=True,
            checkpoint_retention_permitted=True,
            checkpoint_export_permitted=False,
            independent_review_required=True,
            release_class=AmberReleaseClass.RESTRICTED_INTERNAL,
        ),
        required_reviewers=("reviewer-a",),
        stop_conditions=("environment_drift", "sandbox_failure"),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def execution(
    *,
    program_digest: str,
    distribution_digest: str,
    instance: ProjectInstance,
    authorization_digest: str,
    model: ModelServingIdentity | None = None,
    execution_id: str = "test-process-execution",
    seed: int = 41,
) -> ProcessExecutionManifest:
    selected_model = model or worker_model()
    parameters = environment_parameters()
    fingerprint = sha256_digest(parameters)
    return ProcessExecutionManifest(
        execution_id=execution_id,
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        instance_digest=sha256_digest(instance),
        amber_authorization_digest=authorization_digest,
        process_policy=component("test.process-policy"),
        worker_models=(selected_model,),
        output_rights=project_authored_internal_rights(reviewed_at=NOW),
        environment=VersionedComponentIdentity(
            component_id="test.process-environment",
            version="1.0.0",
            digest=fingerprint,
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="test environment parameters",
        ),
        environment_parameters=parameters,
        environment_fingerprint=fingerprint,
        seed=seed,
        created_at=NOW,
    )


class DeterministicProjectGenerator:
    generator_id = "test.project-generator"
    generator_version = "1.0.0"
    generator_digest = sha256_digest(generator_id)

    def generate(
        self,
        *,
        manifest: ProcessDistributionManifest,
        split: ProjectSplit,
        seed: int,
    ) -> GeneratedProject:
        parameters = environment_parameters()
        return GeneratedProject(
            task={"seed": seed, "split": split.value, "target": seed * 2},
            difficulty=0.5,
            difficulty_stratum="standard",
            environment_fingerprint=sha256_digest(parameters),
            metadata={"generator": manifest.generator.component_id},
        )


def with_budget(state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    return {**state, **updates}
