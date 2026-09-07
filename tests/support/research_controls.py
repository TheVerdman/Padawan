"""Shared research controls fixture setup."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from padawan.experiments.controls import (
    ResearchWorkerConfiguration,
)
from padawan.models.contracts import ResearchRole, RunState, StudentStateRecord
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ContextPolicy,
    ContinuationPolicy,
    EvaluationSuiteManifest,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ParentStateIdentity,
    ResearchExecutionManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.orchestration.supervisor import WorkResult

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
