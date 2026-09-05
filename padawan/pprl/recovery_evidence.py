"""Bounded privileged recovery source inspection. This module never dispatches work."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.information import ForensicArtifactRef, InformationClass
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ArtifactRow,
    ExternalCallRow,
    ProcessContainerReceiptRow,
    ProcessContainerWorkloadRow,
    ProcessGenerationWorkloadRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.container_contracts import ProcessContainerReceipt, ProcessContainerWorkload
from padawan.pprl.containers import ProcessContainerStore
from padawan.pprl.generation_contracts import generation_workload_from_row
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.recovery_contracts import ProcessRecoveryEffect, ProcessRecoverySource
from padawan.pprl.resource_contracts import ProcessResourceEvent, ProcessResourceReservation


@dataclass(frozen=True)
class RecoveryEvidence:
    kind: Literal["generic", "model", "container"]
    invocation_id: str | None = None
    workload_digest: str | None = None
    result_digest: str | None = None
    observed_status: str = "no_retained_intent"
    completed: bool = False
    sources: tuple[ProcessRecoverySource, ...] = ()


class RecoveryEvidenceReader:
    def __init__(
        self,
        containers: ProcessContainerStore,
        observations: ProcessObservationStore,
        *,
        maximum_bytes: int,
    ) -> None:
        self.containers, self.observations = containers, observations
        self.remaining_bytes = maximum_bytes
        self.seen: set[str] = set()

    def _budget(self, reference: ForensicArtifactRef) -> None:
        self._budget_artifact(reference.artifact)

    def _budget_artifact(self, artifact: ArtifactRef) -> None:
        if artifact.artifact_id in self.seen:
            return
        self.remaining_bytes -= artifact.size_bytes
        if self.remaining_bytes < 0:
            raise PermissionError("recovery exceeds its reviewed source byte limit")
        self.seen.add(artifact.artifact_id)

    async def read_source(self, session: AsyncSession, source: ProcessRecoverySource) -> bytes:
        self._budget(source.reference)
        return await self.containers._read(
            session, source.reference, owner_type=source.owner_type, owner_id=source.owner_id
        )

    async def _reference(
        self,
        session: AsyncSession,
        artifact_id: str,
        *,
        now: datetime,
        classify_missing: bool = False,
    ) -> ForensicArtifactRef:
        row = await session.get(ArtifactRow, artifact_id)
        if row is None:
            raise ValueError("recovery source artifact is missing")
        artifact = ArtifactRef.model_validate(
            {key: getattr(row, key) for key in ArtifactRef.model_fields if key != "schema_version"},
            strict=False,
        )
        if not artifact.raw_data or not artifact.restricted:
            raise PermissionError("recovery sources must remain restricted raw evidence")
        self._budget_artifact(artifact)
        # The external-call executor may have retained its request before interruption
        # but not reached PPRL classification. This reviewed operation classifies it
        # forensic only; it never creates a process reference or admits its contents.
        if classify_missing:
            await self.containers.information.classify(
                session,
                artifact=artifact,
                information_class=InformationClass.FORENSIC,
                classified_by="padawan.pprl.recovery",
                reason="reviewed private recovery source retention",
                classified_at=now,
            )
        return await self.containers.information.forensic_reference(
            session, artifact_id=artifact_id
        )

    async def inspect(
        self, session: AsyncSession, reservation: ProcessResourceReservation, *, now: datetime
    ) -> RecoveryEvidence:
        container = await session.scalar(
            select(ProcessContainerWorkloadRow).where(
                ProcessContainerWorkloadRow.decision_id == reservation.decision_id
            )
        )
        model = await session.scalar(
            select(ProcessWorkerInvocationRow).where(
                ProcessWorkerInvocationRow.amber_decision_id == reservation.decision_id
            )
        )
        if container is not None and model is not None:
            raise ValueError("one recovered admission cannot name two effects")
        if container is not None:
            return await self._container(session, container, reservation, now=now)
        if model is not None:
            return await self._model(session, model, reservation, now=now)
        return RecoveryEvidence(kind="generic")

    async def _container(
        self,
        session: AsyncSession,
        row: ProcessContainerWorkloadRow,
        reservation: ProcessResourceReservation,
        *,
        now: datetime,
    ) -> RecoveryEvidence:
        initial = ProcessContainerWorkload.model_validate(row.record_json, strict=False)
        self._budget(initial.input_artifact)
        workload = await self.containers.workload(session, row.invocation_id)
        binding = await self.observations.inspect_decision_binding(
            session, decision_id=reservation.decision_id
        )
        if (
            binding.observation_id != workload.observation_id
            or binding.observation_receipt_digest != workload.observation_receipt_digest
        ):
            raise ValueError("recovery container lost its exact observation binding")
        sources = [
            ProcessRecoverySource(
                reference=workload.input_artifact,
                owner_type="process_container_workload",
                owner_id=workload.invocation_id,
            )
        ]
        # Bound both count and byte reads. Partial captures survive broker death
        # independently of the final receipt and are not proof of no physical effect.
        pins = list(
            await session.scalars(
                select(ArtifactReferenceRow)
                .where(
                    ArtifactReferenceRow.owner_type == "process_container_capture",
                    ArtifactReferenceRow.owner_id.in_(
                        f"{workload.invocation_id}:{kind}"
                        for kind in ("runtime", "created", "terminal", "cleanup")
                    ),
                )
                .order_by(ArtifactReferenceRow.owner_id)
                .limit(5)
            )
        )
        if len(pins) > 4 or len({p.owner_id for p in pins}) != len(pins):
            raise ValueError("recovery found duplicate container captures")
        for pin in pins:
            source = ProcessRecoverySource(
                reference=await self._reference(session, pin.artifact_id, now=now),
                owner_type=pin.owner_type,
                owner_id=pin.owner_id,
            )
            payload = json.loads(await self.read_source(session, source))
            if (
                payload["invocation_id"] != workload.invocation_id
                or payload["workload_digest"] != workload.digest
                or pin.owner_id != f"{workload.invocation_id}:{payload['kind']}"
            ):
                raise ValueError("recovery container capture has substituted lineage")
            sources.append(source)
        result = await session.get(ProcessContainerReceiptRow, workload.invocation_id)
        receipt = None
        if result is not None:
            receipt = ProcessContainerReceipt.model_validate(result.record_json, strict=False)
            self._budget(receipt.evidence_artifact)
            for reference in receipt.capture_artifacts:
                self._budget(reference)
            _, receipt, _ = await self.containers.inspect(session, workload.invocation_id)
            if {s.reference for s in sources[1:]} != set(receipt.capture_artifacts):
                raise ValueError("recovery container receipt differs from original captures")
            sources.append(
                ProcessRecoverySource(
                    reference=receipt.evidence_artifact,
                    owner_type="process_container_result",
                    owner_id=workload.invocation_id,
                )
            )
        return RecoveryEvidence(
            kind="container",
            invocation_id=workload.invocation_id,
            workload_digest=workload.digest,
            result_digest=receipt.digest if receipt else None,
            observed_status=receipt.effect_status if receipt else "intent_without_final_receipt",
            completed=bool(
                receipt and receipt.effect_status == "terminated" and receipt.complete_capture
            ),
            sources=tuple(sources),
        )

    async def _model(
        self,
        session: AsyncSession,
        invocation: ProcessWorkerInvocationRow,
        reservation: ProcessResourceReservation,
        *,
        now: datetime,
    ) -> RecoveryEvidence:
        row = await session.get(ProcessGenerationWorkloadRow, invocation.invocation_id)
        if row is None:
            raise ValueError("recovery model intent lost its admitted workload")
        workload = generation_workload_from_row(row)
        binding = await self.observations.inspect_decision_binding(
            session, decision_id=reservation.decision_id
        )
        if (
            workload.decision_id != reservation.decision_id
            or workload.decision_digest != reservation.decision_digest
            or workload.execution_digest != reservation.execution_digest
            or workload.rollout_id != reservation.rollout_id
            or workload.lease_token_digest != reservation.lease_token_digest
            or workload.worker_model_digest != reservation.worker_model_digest
            or workload.observation_decision_digest != binding.digest
            or workload.observation_id != binding.observation_id
            or invocation.workload_digest != workload.digest
            or invocation.request_id != workload.request_id
            or invocation.rollout_id != workload.rollout_id
            or invocation.worker_model_digest != workload.worker_model_digest
            or invocation.role_id != workload.role_id
        ):
            raise ValueError("recovery model intent lost its exact admitted lineage")
        prepared = ProcessRecoverySource(
            reference=workload.prepared_artifact,
            owner_type="process_generation_workload",
            owner_id=workload.invocation_id,
        )
        if sha256_digest(json.loads(await self.read_source(session, prepared))) != sha256_digest(
            workload.prepared
        ):
            raise ValueError("recovery model wire source differs from its intent")
        sources = [prepared]
        call = await session.get(ExternalCallRow, workload.request_id)
        if call is not None:
            if (
                call.process_rollout_id != workload.rollout_id
                or call.request_hash != workload.request_digest
                or call.purpose != workload.purpose
                or call.provider != workload.prepared.provider
            ):
                raise ValueError("recovery external call has substituted lineage")
            for artifact_id, owner_type in (
                (call.request_artifact_id, "external_call_request"),
                (call.response_artifact_id, "external_call_response"),
            ):
                if artifact_id is not None:
                    source = ProcessRecoverySource(
                        reference=await self._reference(
                            session, artifact_id, now=now, classify_missing=True
                        ),
                        owner_type=owner_type,
                        owner_id=workload.request_id,
                    )
                    data = await self.read_source(session, source)
                    if (
                        owner_type == "external_call_request"
                        and sha256_digest(json.loads(data)) != workload.request_digest
                    ):
                        raise ValueError("recovery normalized request differs from its workload")
                    sources.append(source)
        return RecoveryEvidence(
            kind="model",
            invocation_id=workload.invocation_id,
            workload_digest=workload.digest,
            result_digest=call.result_envelope_digest if call else None,
            observed_status=call.status if call else "intent_without_external_call",
            completed=bool(call and call.status == "completed"),
            sources=tuple(sources),
        )

    async def verify_retained(
        self, session: AsyncSession, effect: ProcessRecoveryEffect, *, recovery_id: str
    ) -> None:
        """Validate the immutable snapshot's sources, without demanding frozen live status."""
        payloads = {}
        for source in effect.sources:
            payloads[source.owner_type, source.owner_id] = await self.read_source(session, source)
            await self.containers._read(
                session,
                source.reference,
                owner_type="process_recovery_receipt",
                owner_id=recovery_id,
            )
        if effect.kind == "container":
            await self._verify_container_snapshot(session, effect, payloads)
        elif effect.kind == "model":
            await self._verify_model_snapshot(session, effect, payloads)

    async def _verify_container_snapshot(
        self,
        session: AsyncSession,
        effect: ProcessRecoveryEffect,
        payloads: dict[tuple[str, str], bytes],
    ) -> None:
        assert effect.invocation_id is not None
        workload = await self.containers.workload(session, effect.invocation_id)
        if (
            workload.digest != effect.workload_digest
            or workload.decision_id != effect.decision_id
            or workload.decision_digest != effect.decision_digest
        ):
            raise ValueError("recovery lost its original container workload")
        expected = {("process_container_workload", workload.invocation_id): workload.input_artifact}
        if effect.result_digest is not None:
            _, receipt, _ = await self.containers.inspect(session, effect.invocation_id)
            if (
                receipt.digest != effect.result_digest
                or receipt.effect_status != effect.observed_status
                or (
                    effect.disposition == "completed_unadmitted"
                    and (receipt.effect_status != "terminated" or not receipt.complete_capture)
                )
            ):
                raise ValueError("recovery lost its original container result")
            expected["process_container_result", workload.invocation_id] = receipt.evidence_artifact
            # Inspect validates capture bytes, kinds and their original native owners.
            for reference in receipt.capture_artifacts:
                matches = [s for s in effect.sources if s.reference == reference]
                if len(matches) != 1 or matches[0].owner_type != "process_container_capture":
                    raise ValueError("recovery omits a container result's exact capture")
                source = matches[0]
                expected[source.owner_type, source.owner_id] = reference
        elif effect.observed_status != "intent_without_final_receipt":
            raise ValueError("recovery container status lacks its retained result")
        for source in effect.sources:
            if source.owner_type == "process_container_capture":
                data = json.loads(payloads[source.owner_type, source.owner_id])
                if (
                    data["kind"] not in {"runtime", "created", "terminal", "cleanup"}
                    or data["invocation_id"] != workload.invocation_id
                    or data["workload_digest"] != workload.digest
                    or source.owner_id != f"{workload.invocation_id}:{data['kind']}"
                ):
                    raise ValueError("recovery container capture has substituted lineage")
                if effect.result_digest is None:
                    # A partial snapshot is historical: later captures do not enlarge it.
                    expected[source.owner_type, source.owner_id] = source.reference
        if {(s.owner_type, s.owner_id): s.reference for s in effect.sources} != expected:
            raise ValueError("recovery container snapshot has inconsistent native sources")

    async def _verify_model_snapshot(
        self,
        session: AsyncSession,
        effect: ProcessRecoveryEffect,
        payloads: dict[tuple[str, str], bytes],
    ) -> None:
        row = await session.get(ProcessGenerationWorkloadRow, effect.invocation_id)
        if row is None:
            raise ValueError("recovery lost its original model workload")
        workload = generation_workload_from_row(row)
        if (
            workload.digest != effect.workload_digest
            or workload.decision_id != effect.decision_id
            or workload.decision_digest != effect.decision_digest
            or workload.observation_decision_digest != effect.observation_binding_digest
        ):
            raise ValueError("recovery model snapshot crossed its admitted workload")
        sources = {(s.owner_type, s.owner_id): s.reference for s in effect.sources}
        prepared_key = ("process_generation_workload", workload.invocation_id)
        request_key = ("external_call_request", workload.request_id)
        response_key = ("external_call_response", workload.request_id)
        if (
            sources.get(prepared_key) != workload.prepared_artifact
            or set(sources) - {prepared_key, request_key, response_key}
            or ((response_key in sources) != (effect.result_digest is not None))
            or (response_key in sources and request_key not in sources)
            or (
                effect.observed_status == "intent_without_external_call"
                and set(sources) != {prepared_key}
            )
            or (effect.observed_status == "completed" and response_key not in sources)
            or (
                effect.disposition == "completed_unadmitted"
                and effect.observed_status != "completed"
            )
        ):
            raise ValueError("recovery model snapshot has inconsistent native sources")
        if sha256_digest(json.loads(payloads[prepared_key])) != sha256_digest(workload.prepared):
            raise ValueError("recovery model prepared bytes differ from admitted intent")
        if request_key in sources:
            request = GenerationRequest.model_validate_json(payloads[request_key])
            if sha256_digest(request) != workload.request_digest:
                raise ValueError("recovery model request differs from admitted intent")
        if response_key in sources:
            result = json.loads(payloads[response_key])
            if (
                sources[response_key].artifact.digest != effect.result_digest
                or result.get("request_id") != workload.request_id
                or result.get("provider") != workload.prepared.provider
                or result.get("model_id") != workload.prepared.model_id
                or result.get("protocol") != workload.prepared.protocol
                or sha256_digest(base64.b64decode(result["raw_request_base64"], validate=True))
                != workload.prepared.body_digest
            ):
                raise ValueError("recovery model result differs from its exact admitted request")

    async def verify_settlement(
        self, session: AsyncSession, effect: ProcessRecoveryEffect, event: ProcessResourceEvent
    ) -> None:
        """The completed snapshot cites the original debit, never a new reconciliation."""
        reservation, _, _ = await self.containers.resources.reservation(session, effect.decision_id)
        sources = {effect.workload_digest, *(s.reference.artifact.digest for s in effect.sources)}
        if effect.kind == "model":
            grant = await self.containers.resources.grant(session, reservation.authorization_digest)
            rates = [
                rate
                for rate in grant.model_rates
                if rate.worker_model_digest == reservation.worker_model_digest
            ]
            if len(rates) != 1:
                raise ValueError("recovery settlement lost its model rate")
            sources.add(sha256_digest(rates[0]))
        else:
            sources.add(effect.result_digest)
        if (
            event.kind != "settle"
            or event.basis
            != ("provider_reported" if effect.kind == "model" else "container_evidence")
            or set(event.source_digests) != sources
        ):
            raise ValueError("recovery result differs from its original settlement sources")
        for source in effect.sources:
            await self.containers._read(
                session,
                source.reference,
                owner_type="process_resource_event",
                owner_id=event.digest,
            )
