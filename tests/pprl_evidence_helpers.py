from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import func, select

from padawan.artifacts.information import (
    ArtifactInformationStore,
    ForensicArtifactRef,
    InformationClass,
    ProcessArtifactRef,
)
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDisposition,
    AmberEgressMode,
    AmberStatus,
)
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import (
    project_authored_internal_rights,
)
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ProcessContentAdmissionRow,
    ProcessEventRow,
    ProcessExecutionRow,
    ProcessForkRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessWorkerInvocationRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.evidence import ProcessEvidenceStore
from padawan.pprl.evidence_contracts import (
    EvidenceAdmissionPolicy,
    ProcessEvidenceAdmission,
    ProcessEvidenceUse,
)
from padawan.pprl.generation import ProcessGenerationExecutor
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ProcessStore
from tests.helpers import CallbackGenerationClient
from tests.pprl_generation_helpers import generation_boundary
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


@dataclass
class EvidenceContext:
    database: Database
    artifacts: LocalArtifactStore
    catalog: ArtifactCatalog
    information: ArtifactInformationStore
    evidence: ProcessEvidenceStore
    amber: AmberStore
    process: ProcessStore
    execution: ProcessExecutionManifest
    program: ProcessProgram
    split: ProjectSplit
    clock: Callable[[], datetime]

    @property
    def execution_digest(self) -> str:
        return sha256_digest(self.execution)


@pytest_asyncio.fixture
async def evidence_context(database, tmp_path, pprl_now, request) -> EvidenceContext:
    configured = getattr(request, "param", ProjectSplit.TRAIN)
    split = configured["split"] if isinstance(configured, dict) else configured
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    process = ProcessStore(amber)
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=split,
            seed=93,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(
            program_digest=program_digest, distribution_digest=distribution_digest
        )
        if isinstance(configured, dict):
            # Only test declarations; HTTP tests supply MockTransport, never a live endpoint.
            authorization = authorization.model_copy(
                update={
                    "environment": authorization.environment.model_copy(
                        update={
                            "network_enabled": True,
                            "egress_mode": AmberEgressMode.ALLOWLIST,
                            "allowed_destinations": (configured["destination"],),
                        }
                    )
                }
            )
        await amber.prepare(session, envelope=authorization, actor_id="preparer")
        for status, minute in ((AmberStatus.AUTHORIZED, 1), (AmberStatus.ACTIVE, 2)):
            await amber.transition(
                session,
                authorization_digest=authorization.digest,
                to_status=status,
                actor_id="reviewer-a",
                reason="synthetic offline fixture",
                evidence_refs=("review:test",),
                occurred_at=NOW + timedelta(minutes=minute),
            )
        manifest = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization.digest,
        )
        execution_digest = await process.register_execution(session, manifest)
        await process.create_rollout(
            session,
            execution_digest=execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(objective="exercise reviewed evidence admission"),
            rollout_id="evidence-rollout",
            created_at=NOW + timedelta(minutes=3),
        )
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalog = ArtifactCatalog(artifacts)
    policy = EvidenceAdmissionPolicy(
        policy_id="test.evidence-admission",
        version="1.0.0",
        reviewer_ids=("reviewer-a", "reviewer-not-in-amber"),
        maximum_bytes=100_000,
        maximum_forensic_sources=4,
        maximum_forensic_source_bytes=1_000_000,
    )
    evidence = ProcessEvidenceStore(catalog=catalog, amber=amber, policy=policy)
    return EvidenceContext(
        database,
        artifacts,
        catalog,
        ArtifactInformationStore(catalog),
        evidence,
        amber,
        ProcessStore(amber, evidence=evidence),
        manifest,
        process_program,
        split,
        pprl_now,
    )


async def _review(
    ctx: EvidenceContext,
    *,
    content: str = "The checked calculation gives 42.",
    sources: tuple[ForensicArtifactRef, ...] = (),
    training: bool = False,
) -> ProcessEvidenceAdmission:
    artifact = ctx.artifacts.put_text(content, restricted=True)
    async with ctx.database.transaction() as session:
        classified = await ctx.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.PROCESS_CANDIDATE,
            classified_by="test.broker",
            reason="synthetic candidate awaiting an explicit review",
            classified_at=ctx.clock(),
        )
    return ProcessEvidenceAdmission(
        process_reference=ProcessArtifactRef(
            process_artifact_id=f"process-artifact-{uuid4().hex}",
            execution_digest=ctx.execution_digest,
            content_digest=artifact.digest,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
        ),
        candidate_artifact_id=artifact.artifact_id,
        candidate_classification_digest=classified.digest,
        forensic_sources=sources,
        admission_policy=ctx.evidence.policy,
        policy_digest=ctx.evidence.policy.digest,
        reviewer_id="reviewer-a",
        rationale="Synthetic review of exact candidate bytes and the declared source set.",
        contamination_scope=f"test-{ctx.split.value}",
        rights=project_authored_internal_rights(reviewed_at=NOW),
        allowed_uses=(ProcessEvidenceUse.PROCESS, ProcessEvidenceUse.TRAINING_PROJECTION)
        if training
        else (ProcessEvidenceUse.PROCESS,),
        reviewed_at=ctx.clock(),
    )


async def _model_sources(
    ctx: EvidenceContext, client: CallbackGenerationClient | None = None
) -> tuple[ForensicArtifactRef, ...]:
    instant = ctx.clock()
    async with ctx.database.transaction() as session:
        claimed = await ctx.process.claim_next(
            session, worker_id="synthetic-worker", lease_for=timedelta(minutes=5), now=instant
        )
        observations = ProcessObservationStore(ctx.process)
        assert claimed is not None
        observed = await observations.observe_claim(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            worker_id="synthetic-worker",
            now=instant,
        )
        assert claimed is not None
        admission = await ctx.amber.admit(
            session,
            request=AmberActionRequest(
                authorization_digest=ctx.execution.amber_authorization_digest,
                rollout_id=claimed.rollout.rollout_id,
                rollout_sequence=claimed.rollout.sequence,
                state_digest=claimed.state.state_digest,
                lease_token_digest=sha256_digest(claimed.lease_token),
                program_digest=ctx.execution.program_digest,
                distribution_digest=ctx.execution.distribution_digest,
                split=ctx.split,
                persistence_mode=ctx.program.persistence_mode,
                event_kind=ProcessEventKind.CLAIM_UPDATED,
                role_id="researcher",
                worker_model_digest=sha256_digest(worker_model()),
                target_class="scientific_math",
                environment_fingerprint=ctx.execution.environment_fingerprint,
                projected_usage=ProjectBudgetUsage(
                    actions=1,
                    input_tokens=10,
                    output_tokens=12,
                    artifact_bytes=1_000_000,
                    wall_time_seconds=30.0,
                ),
                projected_artifact_bytes=1_000_000,
                requested_at=instant,
            ),
            active_workers=0,
        )
        await observations.bind_decision(
            session,
            observation_id=observed.observation_id,
            decision_id=admission.decision_id,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            worker_id="synthetic-worker",
            now=instant,
        )
    client = client or CallbackGenerationClient(
        lambda _request: "synthetic raw model output", "test-open-weight"
    )
    external = IdempotentGenerationExecutor(
        database=ctx.database, artifacts=ctx.artifacts, client=client
    )
    boundary = generation_boundary(ctx.process, external)
    generation = ProcessGenerationExecutor(
        database=ctx.database,
        executor=external,
        boundary=boundary,
    )
    result = await generation.execute(
        rollout_id=claimed.rollout.rollout_id,
        lease_token=claimed.lease_token,
        amber_decision_id=admission.decision_id,
        invocation_id="evidence-source-invocation",
        role_id="researcher",
        worker_model_digest=sha256_digest(worker_model()),
        purpose="process_worker",
        provider="test-open-weight",
        request=boundary.request(
            request_id="evidence-source-request", observation=observed.observation
        ),
    )
    assert len(client.calls) == 1
    async with ctx.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, result.invocation_id)
        assert invocation is not None
        assert invocation.request_artifact_id is not None
        assert invocation.response_artifact_id is not None
        return tuple(
            [
                await ctx.information.forensic_reference(session, artifact_id=artifact_id)
                for artifact_id in sorted(
                    (invocation.request_artifact_id, invocation.response_artifact_id)
                )
            ]
        )


async def _counts(session):
    return tuple(
        [
            await session.scalar(select(func.count()).select_from(table))
            for table in (
                ProcessRolloutRow,
                ProcessStateRow,
                ProcessEventRow,
                ProcessForkRow,
                ProcessExecutionRow,
                ArtifactReferenceRow,
                ProcessContentAdmissionRow,
            )
        ]
    )


async def _prepare_action(ctx, *, kind=ProcessEventKind.ARTIFACT_ADMITTED, artifact_bytes=0):
    now = ctx.clock()
    async with ctx.database.transaction() as session:
        claimed = await ctx.process.claim_next(
            session, worker_id="test.worker", lease_for=timedelta(minutes=5), now=now
        )
        assert claimed is not None
        previous = claimed.state.payload.budget_usage
        usage = previous.model_copy(
            update={
                "actions": previous.actions + 1,
                "artifact_bytes": previous.artifact_bytes + artifact_bytes,
                "wall_time_seconds": float(previous.wall_time_seconds) + 1.0,
            }
        )
        request = AmberActionRequest(
            authorization_digest=ctx.execution.amber_authorization_digest,
            rollout_id=claimed.rollout.rollout_id,
            rollout_sequence=claimed.rollout.sequence,
            state_digest=claimed.state.state_digest,
            lease_token_digest=sha256_digest(claimed.lease_token),
            program_digest=ctx.execution.program_digest,
            distribution_digest=ctx.execution.distribution_digest,
            split=ctx.split,
            persistence_mode=ctx.program.persistence_mode,
            event_kind=kind,
            role_id="researcher",
            worker_model_digest=sha256_digest(ctx.execution.worker_models[0]),
            target_class="scientific_math",
            environment_fingerprint=ctx.execution.environment_fingerprint,
            projected_usage=usage,
            projected_artifact_bytes=usage.artifact_bytes,
            requested_at=now,
        )
        decision = await ctx.amber.admit(session, request=request, active_workers=0)
        assert decision.disposition == AmberAdmissionDisposition.ADMITTED
    return claimed, decision, usage


async def _admit(ctx, *, sources=()):
    review = await _review(ctx, sources=sources)
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
    return review
