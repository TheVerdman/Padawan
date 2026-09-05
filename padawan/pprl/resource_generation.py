"""Reconstruct model accounting from retained private evidence, never worker claims."""

from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, artifact_read_bytes
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ArtifactRow,
    ExternalCallRow,
    ProcessGenerationWorkloadRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.generation_contracts import (
    ProcessGenerationWorkload,
    generation_workload_from_row,
)
from padawan.pprl.resource_contracts import ProcessResourceEvent, ProcessResourceReservation
from padawan.pprl.resources import ProcessResourceStore, atomic_resource_write


class ProcessGenerationResources:
    def __init__(self, *, store: ProcessResourceStore, catalog: ArtifactCatalog) -> None:
        self.store, self.catalog = store, catalog

    async def validate(
        self,
        session: AsyncSession,
        workload: ProcessGenerationWorkload,
    ) -> ProcessResourceReservation:
        reservation, phase, _ = await self.store.reservation(session, workload.decision_id)
        current = await self.store.inspect(session, reservation.authorization_digest)
        if current.stopped or phase.status not in {"reserved", "started", "settled"}:
            raise PermissionError("generation resource account is unavailable")
        if (
            reservation.decision_digest != workload.decision_digest
            or reservation.execution_digest != workload.execution_digest
            or reservation.rollout_id != workload.rollout_id
            or reservation.worker_model_digest != workload.worker_model_digest
            or reservation.lease_token_digest != workload.lease_token_digest
        ):
            raise PermissionError("generation differs from its funded action")
        grant = await self.store.grant(session, reservation.authorization_digest)
        rate = next(
            rate
            for rate in grant.model_rates
            if rate.worker_model_digest == workload.worker_model_digest
        )
        if (
            rate.price(reservation.amount.input_tokens, reservation.amount.output_tokens)
            > reservation.amount.micro_usd
        ):
            raise PermissionError("generation cost reservation does not cover its pinned rate")
        return reservation

    @atomic_resource_write
    async def reconcile(
        self,
        session: AsyncSession,
        *,
        invocation_id: str,
        now: datetime,
    ) -> ProcessResourceEvent:
        invocation = await session.get(ProcessWorkerInvocationRow, invocation_id)
        row = await session.get(ProcessGenerationWorkloadRow, invocation_id)
        if invocation is None or row is None:
            raise PermissionError("model reconciliation requires exact generation lineage")
        workload = generation_workload_from_row(row)
        reservation, _, _ = await self.store.reservation(session, workload.decision_id)
        # Lock before reading the phase or publishing independent pins; no rollout lock follows.
        account = await self.store.inspect(session, reservation.authorization_digest, lock=True)
        reservation, phase, prior = await self.store.reservation(session, workload.decision_id)
        if (
            invocation.workload_digest != workload.digest
            or invocation.request_id != workload.request_id
            or invocation.rollout_id != workload.rollout_id
            or invocation.amber_decision_id != workload.decision_id
            or reservation.decision_digest != workload.decision_digest
            or reservation.execution_digest != workload.execution_digest
            or reservation.worker_model_digest != workload.worker_model_digest
            or phase.status not in {"started", "settled"}
        ):
            raise PermissionError("model reconciliation has mismatched or unstarted intent")
        call = await session.get(ExternalCallRow, workload.request_id)
        if (
            call is None
            or call.status != "completed"
            or call.completed_at is None
            or call.response_artifact_id is None
            or call.process_rollout_id != workload.rollout_id
            or call.request_hash != workload.request_digest
            or call.purpose != workload.purpose
            or call.provider != workload.prepared.provider
        ):
            raise PermissionError("unknown model effects keep their resource reservation")
        references: list[ArtifactRef] = []
        payloads: list[bytes] = []
        for artifact_id, owner_type, owner_id in (
            (call.request_artifact_id, "external_call_request", workload.request_id),
            (call.response_artifact_id, "external_call_response", workload.request_id),
            (
                workload.prepared_artifact.artifact.artifact_id,
                "process_generation_workload",
                workload.invocation_id,
            ),
        ):
            artifact = await session.get(ArtifactRow, artifact_id)
            if artifact is None:
                raise ValueError("model accounting source is missing")
            reference = ArtifactRef.model_validate(
                {
                    key: getattr(artifact, key)
                    for key in (
                        "artifact_id",
                        "uri",
                        "digest",
                        "media_type",
                        "size_bytes",
                        "restricted",
                        "raw_data",
                    )
                },
                strict=False,
            )
            if not reference.restricted or not reference.raw_data:
                raise PermissionError("model accounting evidence must remain private and raw")
            await self._pin_exists(session, reference, owner_type, owner_id)
            if phase.status == "settled":
                await self._pin_exists(session, reference, "process_resource_event", prior.digest)
            payloads.append(
                await artifact_read_bytes(self.catalog.backend, reference, allow_restricted=True)
            )
            references.append(reference)
        request = GenerationRequest.model_validate_json(payloads[0])
        result = json.loads(payloads[1])
        if (
            sha256_digest(request) != workload.request_digest
            or references[2] != workload.prepared_artifact.artifact
            or sha256_digest(json.loads(payloads[2])) != sha256_digest(workload.prepared)
            or result.get("request_id") != workload.request_id
            or result.get("provider") != workload.prepared.provider
            or result.get("model_id") != workload.prepared.model_id
            or result.get("protocol") != workload.prepared.protocol
            or call.result_envelope_digest != references[1].digest
            or sha256_digest(base64.b64decode(result["raw_request_base64"], validate=True))
            != workload.prepared.body_digest
        ):
            raise ValueError("model accounting evidence differs from admitted workload")
        usage = result.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")
        ):
            raise ValueError("model accounting usage is missing, coerced or invalid")
        grant = await self.store.grant(session, reservation.authorization_digest)
        rate = next(
            rate
            for rate in grant.model_rates
            if rate.worker_model_digest == workload.worker_model_digest
        )
        sources = tuple(
            sorted({workload.digest, sha256_digest(rate), *(ref.digest for ref in references)})
        )
        try:
            charged = reservation.amount.model_validate(
                {
                    **reservation.amount.model_dump(),
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "micro_usd": rate.price(usage["input_tokens"], usage["output_tokens"]),
                    "artifact_bytes": max(
                        reservation.amount.artifact_bytes, sum(ref.size_bytes for ref in references)
                    ),
                }
            )
            # The accumulated debit must fit too; retain the original values on overflow.
            if phase.status != "settled":
                account.charged.plus(charged)
        except ValueError:
            event = await self.store.stop_overflow(
                session, decision_id=workload.decision_id, sources=sources, now=now
            )
        else:
            event = await self.store._settle(
                session,
                decision_id=workload.decision_id,
                charged=charged,
                source_digests=sources,
                provider_reported=True,
                now=now,
            )
        for reference in references:
            if event.sequence <= account.sequence:
                await self._pin_exists(session, reference, "process_resource_event", event.digest)
            await ArtifactInformationStore(self.catalog).classify(
                session,
                artifact=reference,
                information_class=InformationClass.FORENSIC,
                classified_by="padawan.pprl.resources",
                reason="retained model accounting source",
                classified_at=now,
            )
            await self.catalog.reference(
                session, reference, owner_type="process_resource_event", owner_id=event.digest
            )
        return event

    async def _pin_exists(
        self,
        session: AsyncSession,
        reference: ArtifactRef,
        owner_type: str,
        owner_id: str,
    ) -> None:
        pin = await session.scalar(
            select(ArtifactReferenceRow.reference_id).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == owner_id,
                ArtifactReferenceRow.artifact_id == reference.artifact_id,
            )
        )
        if pin is None:
            raise ValueError("resource accounting source lost its original retention ownership")
