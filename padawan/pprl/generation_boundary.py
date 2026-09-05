"""Trusted local admission of exact observed inputs and prepared generation effects."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.adapters.base import GenerationRequest
from padawan.adapters.prepared import PreparedGeneration, PreparedGenerationClient
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, artifact_put_bytes, artifact_read_bytes
from padawan.governance.amber import AmberActionRequest, AmberAdmissionDisposition
from padawan.models.contracts import RightsUse
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ArtifactReferenceRow,
    ProcessGenerationWorkloadRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.generation_contracts import (
    ProcessGenerationPolicy,
    ProcessGenerationWorkload,
)
from padawan.pprl.generation_contracts import (
    generation_workload_from_row as _receipt,
)
from padawan.pprl.observation_contracts import (
    ProcessObservationDecisionBinding,
    ProcessObservationReceipt,
    ProcessWorkerObservation,
)
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.worker_contracts import ProcessWorkerAccess


class ProcessGenerationBoundary:
    def __init__(
        self,
        *,
        observations: ProcessObservationStore,
        catalog: ArtifactCatalog,
        client: PreparedGenerationClient,
        policy: ProcessGenerationPolicy,
    ) -> None:
        self.observations = observations
        self.catalog = catalog
        self.client = client
        self.information = ArtifactInformationStore(catalog)
        self._policy = ProcessGenerationPolicy.model_validate_json(policy.model_dump_json())
        evidence = observations.store.evidence
        if evidence is not None and evidence.catalog.backend is not catalog.backend:
            raise ValueError("generation and observation evidence must share their owned backend")

    @property
    def policy(self) -> ProcessGenerationPolicy:
        return self._policy.model_copy(deep=True)

    def request(
        self, *, request_id: str, observation: ProcessWorkerObservation
    ) -> GenerationRequest:
        """Deterministic request construction; no admission or provider I/O."""
        return GenerationRequest(
            request_id=request_id,
            instructions=self._policy.instructions,
            input=canonical_json_bytes(observation).decode("utf-8"),
            sampling=self._policy.sampling.model_copy(deep=True),
            schema_name=self._policy.schema_name,
            json_schema=self._policy.model_copy(deep=True).json_schema,
        )

    async def _expected(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        decision_id: str,
        request: GenerationRequest,
        now: datetime,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> tuple[ProcessObservationReceipt, ProcessObservationDecisionBinding, PreparedGeneration]:
        if now.tzinfo is None:
            raise ValueError("generation admission requires timezone-aware time")
        request = GenerationRequest.model_validate_json(request.model_dump_json())
        if len(canonical_json_bytes(request)) > self._policy.maximum_request_bytes:
            raise ValueError("generation request exceeds its policy byte bound")
        binding = await self.observations.inspect_decision_binding(session, decision_id=decision_id)
        if binding.disposition != AmberAdmissionDisposition.ADMITTED:
            raise PermissionError("generation requires an admitted observation-bound decision")
        observation = await self.observations.read(
            session,
            observation_id=binding.observation_id,
            rollout_id=rollout_id,
            lease_token=lease_token,
            worker_id=worker_id,
            now=now,
            worker_access=worker_access,
        )
        receipt = await self.observations.inspect_receipt(
            session, observation_id=binding.observation_id
        )
        decision_row = await session.get(AmberAdmissionDecisionRow, decision_id)
        assert decision_row is not None  # Binding inspection validates the full decision record.
        action = AmberActionRequest.model_validate(decision_row.request_json, strict=False)
        authority = await self.observations.store.amber.get(
            session, authorization_digest=receipt.authorization_digest
        )
        rights = self._policy.instructions_rights
        if (
            binding.declared_role_id != self._policy.role_id
            or binding.declared_worker_model_digest != self._policy.worker_model_digest
            or self._policy.reviewed_at > action.requested_at
            or self._policy.reviewed_by not in authority.required_reviewers
            or not rights.permits(RightsUse.INTERNAL_RESEARCH)
            or not rights.permits(RightsUse.EVIDENCE_RETENTION)
            or rights.reviewed_at is None
            or rights.reviewed_at > self._policy.reviewed_at
            or binding.bound_at > now
            or self.request(request_id=request.request_id, observation=observation) != request
        ):
            raise PermissionError("generation request differs from its observed input or policy")
        # Observation contents already have scoped admission. Fixed instructions and schema are
        # separately configured inputs; reject discoverable forensic identifiers in them too.
        content = self.observations.store.content
        strings = content._bounded_strings(
            {
                "instructions": request.instructions,
                "schema": request.json_schema,
                "sampling": request.sampling.model_dump(mode="json"),
            },
            maximum_bytes=self._policy.maximum_request_bytes,
        )
        await content._check_identifiers(session, strings, allowed=set(), admitted_digests=set())
        prepared = PreparedGeneration.model_validate_json(
            self.client.prepare_generation(request).model_dump_json()
        )
        if (
            prepared.request_id != request.request_id
            or prepared.request_digest != sha256_digest(request)
            or prepared.configuration_digest != self._policy.transport_configuration_digest
            or prepared.provider != self._policy.provider
            or action.requested_destination != prepared.destination
            or len(canonical_json_bytes(prepared)) > self._policy.maximum_prepared_bytes
        ):
            raise PermissionError(
                "generation transport differs from its declared authority or policy"
            )
        return receipt, binding, prepared

    async def admit(
        self,
        session: AsyncSession,
        *,
        invocation_id: str,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        decision_id: str,
        request: GenerationRequest,
        purpose: str,
        is_new: bool,
        maximum_artifact_bytes: int,
        now: datetime,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> ProcessGenerationWorkload:
        """Broker-only receipt, atomically retained with the new invocation intent."""
        async with session.begin_nested():
            observation, binding, prepared = await self._expected(
                session,
                rollout_id=rollout_id,
                lease_token=lease_token,
                worker_id=worker_id,
                decision_id=decision_id,
                request=request,
                now=now,
                worker_access=worker_access,
            )
            row = await session.get(ProcessGenerationWorkloadRow, invocation_id)
            if row is not None:
                receipt = _receipt(row)
                await self.validate_current(
                    session, receipt, lease_token=lease_token, now=now, worker_access=worker_access
                )
                if (
                    is_new
                    or receipt.request_json != canonical_json_bytes(request).decode("utf-8")
                    or receipt.rollout_id != rollout_id
                    or receipt.decision_id != decision_id
                    or receipt.worker_id != worker_id
                    or receipt.purpose != purpose
                ):
                    raise ValueError("generation invocation cannot change its admitted workload")
                return receipt
            if not is_new:
                raise PermissionError("legacy invocation has no exact generation admission")
            data = canonical_json_bytes(prepared)
            if len(data) + len(canonical_json_bytes(request)) > maximum_artifact_bytes:
                raise PermissionError("prepared generation artifacts exceed the action allowance")
            artifact = await artifact_put_bytes(
                self.catalog.backend,
                data,
                media_type="application/vnd.padawan.prepared-generation+json",
                restricted=True,
                raw_data=True,
            )
            await self.information.classify(
                session,
                artifact=artifact,
                information_class=InformationClass.FORENSIC,
                classified_by="padawan.pprl.generation-boundary",
                reason="Exact privileged transport input retained before provider I/O",
                classified_at=now,
            )
            await self.catalog.reference(
                session, artifact, owner_type="process_generation_workload", owner_id=invocation_id
            )
            reference = await self.information.forensic_reference(
                session, artifact_id=artifact.artifact_id
            )
            receipt = ProcessGenerationWorkload(
                invocation_id=invocation_id,
                request_id=request.request_id,
                rollout_id=rollout_id,
                execution_digest=observation.execution_digest,
                worker_id=worker_id,
                role_id=binding.declared_role_id,
                worker_model_digest=binding.declared_worker_model_digest,
                purpose=purpose,
                lease_token_digest=observation.lease_token_digest,
                observation_id=observation.observation_id,
                observation_receipt_digest=observation.digest,
                observation_decision_digest=binding.digest,
                decision_id=decision_id,
                decision_digest=binding.decision_digest,
                policy=self._policy,
                policy_digest=self._policy.digest,
                request_json=canonical_json_bytes(request).decode("utf-8"),
                request_digest=sha256_digest(request),
                prepared=prepared,
                prepared_artifact=reference,
                admitted_at=now,
            )
            session.add(
                ProcessGenerationWorkloadRow(
                    invocation_id=invocation_id,
                    request_id=request.request_id,
                    decision_id=decision_id,
                    observation_id=observation.observation_id,
                    prepared_artifact_id=artifact.artifact_id,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                    created_at=now,
                )
            )
            await session.flush()
            await self._retained(session, receipt)
            return receipt

    async def validate_current(
        self,
        session: AsyncSession,
        receipt: ProcessGenerationWorkload,
        *,
        lease_token: str,
        now: datetime,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> None:
        row = await session.get(ProcessGenerationWorkloadRow, receipt.invocation_id)
        if row is None or _receipt(row) != receipt or receipt.policy_digest != self._policy.digest:
            raise PermissionError("generation workload is missing, damaged or has stale policy")
        invocation = await session.get(ProcessWorkerInvocationRow, receipt.invocation_id)
        if (
            invocation is None
            or invocation.workload_digest != receipt.digest
            or invocation.status not in {"running", "completed"}
        ):
            raise PermissionError("generation invocation lost its exact workload identity")
        observation, binding, prepared = await self._expected(
            session,
            rollout_id=receipt.rollout_id,
            lease_token=lease_token,
            worker_id=receipt.worker_id,
            decision_id=receipt.decision_id,
            request=GenerationRequest.model_validate_json(receipt.request_json),
            now=now,
            worker_access=worker_access,
        )
        if (
            observation.digest != receipt.observation_receipt_digest
            or observation.execution_digest != receipt.execution_digest
            or observation.lease_token_digest != receipt.lease_token_digest
            or binding.digest != receipt.observation_decision_digest
            or binding.decision_digest != receipt.decision_digest
            or prepared != receipt.prepared
            or receipt.admitted_at > now
        ):
            raise PermissionError("generation workload no longer matches its admitted sources")
        await self._retained(session, receipt)

    async def _retained(self, session: AsyncSession, receipt: ProcessGenerationWorkload) -> None:
        reference = receipt.prepared_artifact
        if (
            await self.information.forensic_reference(
                session, artifact_id=reference.artifact.artifact_id
            )
            != reference
        ):
            raise ValueError("prepared transport classification changed")
        pins = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_generation_workload",
                    ArtifactReferenceRow.owner_id == receipt.invocation_id,
                )
            )
        )
        if pins != {reference.artifact.artifact_id}:
            raise ValueError("prepared transport ownership is missing or inconsistent")
        if await artifact_read_bytes(
            self.catalog.backend, reference.artifact, allow_restricted=True
        ) != canonical_json_bytes(receipt.prepared):
            raise ValueError("prepared transport bytes differ from admission")
