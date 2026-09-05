from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import delete

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.artifacts.information import ArtifactInformationStore
from padawan.artifacts.store import LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import SamplingConfiguration
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ExternalCallRow, ProcessWorkerInvocationRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.generation import ProcessGenerationExecutor, ProcessGenerationUnavailableError
from padawan.pprl.store import ProcessInvariantError, ProcessStore
from tests.helpers import CallbackGenerationClient
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


async def test_process_generation_is_admission_bound_and_idempotent(
    database, tmp_path, pprl_now
) -> None:
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    store = ProcessStore(amber)
    model = worker_model()
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=61,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            model=model,
        )
        authorization_digest = await amber.prepare(
            session, envelope=authorization, actor_id="preparer"
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.AUTHORIZED,
            actor_id="reviewer-a",
            reason="test authorization approved",
            evidence_refs=("review:test",),
            occurred_at=NOW + timedelta(minutes=1),
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.ACTIVE,
            actor_id="operator",
            reason="test authorization activated",
            occurred_at=NOW + timedelta(minutes=2),
        )
        process_execution = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization_digest,
            model=model,
            execution_id="process-generation-execution",
        )
        execution_digest = await store.register_execution(session, process_execution)
        await store.create_rollout(
            session,
            execution_digest=execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(objective="perform one admitted model action"),
            rollout_id="process-generation-rollout",
            created_at=NOW + timedelta(minutes=3),
        )

    action_time = pprl_now()
    async with database.transaction() as session:
        claimed = await store.claim_next(
            session,
            worker_id="process-worker",
            lease_for=timedelta(minutes=5),
            now=action_time,
        )
        assert claimed is not None
        action = AmberActionRequest(
            authorization_digest=authorization_digest,
            rollout_id=claimed.rollout.rollout_id,
            rollout_sequence=claimed.rollout.sequence,
            state_digest=claimed.state.state_digest,
            lease_token_digest=sha256_digest(claimed.lease_token),
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            persistence_mode=process_program.persistence_mode,
            event_kind=ProcessEventKind.CLAIM_UPDATED,
            role_id="researcher",
            worker_model_digest=sha256_digest(model),
            target_class="scientific_math",
            environment_fingerprint=instance.environment_fingerprint,
            projected_usage=ProjectBudgetUsage(
                actions=1,
                input_tokens=10,
                output_tokens=12,
                artifact_bytes=1_000_000,
                wall_time_seconds=30.0,
            ),
            projected_artifact_bytes=1_000_000,
            requested_at=action_time,
        )
        admission = await amber.admit(
            session,
            request=action,
            active_workers=0,
            decision_id="process-generation-admission",
        )

    class PrivateTraceClient(CallbackGenerationClient):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            result = await super().generate(request)
            return replace(
                result,
                raw_request=b"SYNTHETIC_RAW_REQUEST",
                raw_response=b"SYNTHETIC_RAW_RESPONSE",
                private_reasoning="SYNTHETIC_PRIVATE_REASONING",
                reasoning_summary="SYNTHETIC_PRIVATE_SUMMARY",
                provider_metadata={"private": "SYNTHETIC_PROVIDER_METADATA"},
                telemetry={"trace": "SYNTHETIC_FORENSIC_TELEMETRY"},
                usage={**result.usage, "SYNTHETIC_USAGE_EXTENSION": 1},
            )

    client = PrivateTraceClient(lambda _request: "candidate result", "test-open-weight")
    executor = ProcessGenerationExecutor(
        database=database,
        executor=IdempotentGenerationExecutor(
            database=database,
            artifacts=LocalArtifactStore(tmp_path / "process-artifacts"),
            client=client,
        ),
    )
    request = GenerationRequest(
        request_id="process-generation-request",
        instructions="work only inside the admitted project",
        input="derive a candidate result",
        sampling=SamplingConfiguration(max_output_tokens=12),
        store=False,
    )
    with pytest.raises(ProcessGenerationUnavailableError):
        await executor.execute(
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            amber_decision_id=admission.decision_id,
            invocation_id="blocked-hosted-tool-invocation",
            role_id="researcher",
            worker_model_digest=sha256_digest(model),
            purpose="process_worker",
            provider="test-open-weight",
            request=request.model_copy(
                update={
                    "request_id": "blocked-hosted-tool-request",
                    "tools": (
                        {
                            "type": "function",
                            "name": "unmediated_tool",
                        },
                    ),
                }
            ),
        )
    assert client.calls == []
    first = await executor.execute(
        rollout_id=claimed.rollout.rollout_id,
        lease_token=claimed.lease_token,
        amber_decision_id=admission.decision_id,
        invocation_id="process-worker-invocation",
        role_id="researcher",
        worker_model_digest=sha256_digest(model),
        purpose="process_worker",
        provider="test-open-weight",
        request=request,
    )
    replayed = await executor.execute(
        rollout_id=claimed.rollout.rollout_id,
        lease_token=claimed.lease_token,
        amber_decision_id=admission.decision_id,
        invocation_id="process-worker-invocation",
        role_id="researcher",
        worker_model_digest=sha256_digest(model),
        purpose="process_worker",
        provider="test-open-weight",
        request=request,
    )
    assert len(client.calls) == 1
    assert replayed.output == first.output
    assert first.output.output_text == "candidate result"
    assert first.output.input_tokens == 10
    assert first.output.output_tokens == 12
    assert b"SYNTHETIC_" not in canonical_json_bytes(first.output)
    assert not hasattr(first, "generation") and not hasattr(first, "artifact_refs")

    async with database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, first.invocation_id)
        call = await session.get(ExternalCallRow, request.request_id)
        assert invocation is not None and invocation.status == "completed"
        assert invocation.amber_decision_id == admission.decision_id
        assert call is not None and call.process_rollout_id == claimed.rollout.rollout_id
        assert invocation.response_artifact_id is not None
        forensic = await ArtifactInformationStore(executor.executor.catalog).forensic_reference(
            session, artifact_id=invocation.response_artifact_id
        )
        retained = executor.executor.artifacts.read_bytes(forensic.artifact, allow_restricted=True)
        assert b"SYNTHETIC_PRIVATE_REASONING" in retained
        assert b"SYNTHETIC_FORENSIC_TELEMETRY" in retained
        with pytest.raises(ProcessInvariantError, match="forensic/raw"):
            await store.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=admission.decision_id,
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="process-worker",
                payload={"result": first.output.output_text},
                resulting_state=ProjectStatePayload(
                    objective="perform one admitted model action",
                    budget_usage=action.projected_usage,
                ),
                artifact_refs=(forensic.artifact,),
                worker_invocation_id=first.invocation_id,
                occurred_at=pprl_now(),
            )
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_worker_invocation",
                ArtifactReferenceRow.owner_id == first.invocation_id,
                ArtifactReferenceRow.artifact_id == forensic.artifact.artifact_id,
            )
        )
        with pytest.raises(ProcessInvariantError, match="retention ownership"):
            await store.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=admission.decision_id,
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="process-worker",
                payload={"result": first.output.output_text},
                resulting_state=ProjectStatePayload(
                    objective="perform one admitted model action",
                    budget_usage=action.projected_usage,
                ),
                worker_invocation_id=first.invocation_id,
                occurred_at=pprl_now(),
            )
        await executor.executor.catalog.reference(
            session,
            forensic.artifact,
            owner_type="process_worker_invocation",
            owner_id=first.invocation_id,
        )
        oversized = executor.executor.artifacts.put_text("x" * 1_000_000)
        await executor.executor.catalog.register(session, oversized)
        with pytest.raises(PermissionError, match="Amber reservation"):
            await store.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=admission.decision_id,
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="process-worker",
                payload={"result": first.output.output_text},
                resulting_state=ProjectStatePayload(
                    objective="perform one admitted model action",
                    budget_usage=action.projected_usage,
                    artifact_refs=(oversized,),
                ),
                worker_invocation_id=first.invocation_id,
                occurred_at=pprl_now(),
            )
        with pytest.raises(ProcessInvariantError, match="bind its admitted worker invocation"):
            await store.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=admission.decision_id,
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="process-worker",
                payload={"result": first.output.output_text},
                resulting_state=ProjectStatePayload(
                    objective="perform one admitted model action",
                    budget_usage=action.projected_usage,
                ),
                occurred_at=pprl_now(),
            )
        unchanged = await store.get_rollout(session, rollout_id=claimed.rollout.rollout_id)
        assert unchanged.sequence == 0
        event, successor = await store.append_event(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            amber_decision_id=admission.decision_id,
            kind=ProcessEventKind.CLAIM_UPDATED,
            actor_id="process-worker",
            payload={"result": first.output.output_text},
            resulting_state=ProjectStatePayload(
                objective="perform one admitted model action",
                extension_state={"candidate": first.output.output_text},
                budget_usage=action.projected_usage,
            ),
            worker_invocation_id=first.invocation_id,
            occurred_at=pprl_now(),
        )
        assert not event.artifact_refs
        assert forensic.artifact.artifact_id.encode() not in canonical_json_bytes(event)
        assert forensic.artifact.artifact_id.encode() not in canonical_json_bytes(successor)

    async with database.transaction() as session:
        next_claim = await store.claim_next(
            session,
            worker_id="process-worker-next",
            lease_for=timedelta(minutes=5),
            now=pprl_now(),
        )
        assert next_claim is not None
    with pytest.raises(ProcessGenerationUnavailableError):
        await executor.execute(
            rollout_id=next_claim.rollout.rollout_id,
            lease_token=next_claim.lease_token,
            amber_decision_id=admission.decision_id,
            invocation_id="process-worker-invocation",
            role_id="researcher",
            worker_model_digest=sha256_digest(model),
            purpose="process_worker",
            provider="test-open-weight",
            request=request,
        )
    assert len(client.calls) == 1
