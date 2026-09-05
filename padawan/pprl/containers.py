"""Observation/admission-bound local tool execution and private retained evidence.

All methods belong to the trusted broker. There is deliberately no worker-facing
stdout projection or automatic scheduler/CLI execution entry point.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import (
    ArtifactInformationStore,
    ForensicArtifactRef,
    InformationClass,
)
from padawan.artifacts.store import ArtifactCatalog, artifact_put_bytes, artifact_read_bytes
from padawan.governance.amber import AmberActionRequest, AmberAdmissionDisposition, AmberEgressMode
from padawan.models.contracts import RightsUse
from padawan.models.database import Database
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ArtifactReferenceRow,
    ProcessContainerHeadRow,
    ProcessContainerReceiptRow,
    ProcessContainerWorkloadRow,
    ProcessEventRow,
    ProcessObservationRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.container_contracts import ProcessContainerReceipt, ProcessContainerWorkload
from padawan.pprl.container_driver import (
    ContainerRunCancelled,
    DockerContainerDriver,
    container_wire,
)
from padawan.pprl.contracts import ProcessEventKind
from padawan.pprl.observation_contracts import (
    ProcessObservationDecisionBinding,
    ProcessObservationReceipt,
)
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.resource_contracts import (
    ProcessResourceEvent,
    ProcessResourceReservation,
    ProcessResources,
)
from padawan.pprl.resources import ProcessResourceStore, atomic_resource_write


class ProcessContainerUnavailableError(RuntimeError):
    """Worker-safe denial; detailed evidence is retained only in the broker plane."""


class ProcessContainerStore:
    def __init__(self, catalog: ArtifactCatalog, resources: ProcessResourceStore) -> None:
        self.catalog, self.resources = catalog, resources
        self.information = ArtifactInformationStore(catalog)

    async def put(
        self, session: AsyncSession, data: bytes, *, owner_type: str, owner_id: str, now: datetime
    ) -> ForensicArtifactRef:
        artifact = await artifact_put_bytes(
            self.catalog.backend,
            data,
            media_type="application/vnd.padawan.container-evidence+json",
            restricted=True,
            raw_data=True,
        )
        await self.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.FORENSIC,
            classified_by="padawan.pprl.container",
            reason="private local execution evidence",
            classified_at=now,
        )
        await self.catalog.reference(session, artifact, owner_type=owner_type, owner_id=owner_id)
        return await self.information.forensic_reference(session, artifact_id=artifact.artifact_id)

    async def _read(
        self,
        session: AsyncSession,
        reference: ForensicArtifactRef,
        *,
        owner_type: str,
        owner_id: str,
    ) -> bytes:
        if (
            await self.information.forensic_reference(
                session, artifact_id=reference.artifact.artifact_id
            )
            != reference
        ):
            raise ValueError("container evidence classification differs from its receipt")
        owner = await session.scalar(
            select(ArtifactReferenceRow.reference_id).where(
                ArtifactReferenceRow.artifact_id == reference.artifact.artifact_id,
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == owner_id,
            )
        )
        if owner is None:
            raise ValueError("container evidence lost its original retention ownership")
        return await artifact_read_bytes(
            self.catalog.backend, reference.artifact, allow_restricted=True
        )

    async def workload(self, session: AsyncSession, invocation_id: str) -> ProcessContainerWorkload:
        row = await session.get(ProcessContainerWorkloadRow, invocation_id)
        if row is None:
            raise PermissionError("container intent is missing")
        workload = ProcessContainerWorkload.model_validate(row.record_json, strict=False)
        if sha256_digest(row.record_json) != row.record_digest or (
            workload.invocation_id,
            workload.decision_id,
            workload.rollout_id,
            workload.observation_id,
            workload.container_name,
            workload.input_artifact.artifact.artifact_id,
            workload.digest,
        ) != (
            row.invocation_id,
            row.decision_id,
            row.rollout_id,
            row.observation_id,
            row.container_name,
            row.input_artifact_id,
            row.record_digest,
        ):
            raise ValueError("container workload record is corrupt")
        binding = await session.get(AmberAdmissionDecisionRow, workload.decision_id)
        if binding is None or sha256_digest(binding.record_json) != workload.decision_digest:
            raise ValueError("container workload lost its original admission")
        observed_row = await session.get(ProcessObservationRow, workload.observation_id)
        if (
            observed_row is None
            or sha256_digest(observed_row.record_json) != workload.observation_receipt_digest
        ):
            raise ValueError("container workload lost its original observation")
        observed = ProcessObservationReceipt.model_validate(observed_row.record_json, strict=False)
        reservation, _, _ = await self.resources.reservation(session, workload.decision_id)
        if (
            observed.digest != workload.observation_receipt_digest
            or observed.execution_digest != workload.execution_digest
            or observed.authorization_digest != workload.authorization_digest
            or observed.worker_id != workload.worker_id
            or observed.lease_token_digest != workload.lease_token_digest
            or observed.rollout_id != workload.rollout_id
            or reservation.decision_digest != workload.decision_digest
            or reservation.execution_digest != workload.execution_digest
            or reservation.authorization_digest != workload.authorization_digest
            or reservation.lease_token_digest != workload.lease_token_digest
            or workload.deadline
            != reservation.created_at
            + timedelta(microseconds=reservation.amount.action_microseconds)
        ):
            raise ValueError(
                "container workload differs from its retained observation or reservation"
            )
        original = await self._read(
            session,
            workload.input_artifact,
            owner_type="process_container_workload",
            owner_id=invocation_id,
        )
        if (
            sha256_digest(container_wire(workload.profile, original, workload.deadline))
            != workload.wire_digest
        ):
            raise ValueError("container input no longer reconstructs the exact admitted wire bytes")
        return workload

    async def inspect(
        self, session: AsyncSession, invocation_id: str
    ) -> tuple[ProcessContainerWorkload, ProcessContainerReceipt, dict[str, Any]]:
        workload = await self.workload(session, invocation_id)
        row = await session.get(ProcessContainerReceiptRow, invocation_id)
        if row is None:
            raise PermissionError("container execution has no retained final receipt")
        receipt = ProcessContainerReceipt.model_validate(row.record_json, strict=False)
        if (
            sha256_digest(row.record_json) != row.record_digest
            or receipt.digest != row.record_digest
            or receipt.invocation_id != invocation_id
            or receipt.workload_digest != workload.digest
            or receipt.evidence_artifact.artifact.artifact_id != row.evidence_artifact_id
        ):
            raise ValueError("container result receipt is corrupt")
        data = json.loads(
            await self._read(
                session,
                receipt.evidence_artifact,
                owner_type="process_container_result",
                owner_id=invocation_id,
            )
        )
        captures: dict[str, Any] = {}
        for reference in receipt.capture_artifacts:
            payload = json.loads(
                await artifact_read_bytes(
                    self.catalog.backend, reference.artifact, allow_restricted=True
                )
            )
            kind = payload["kind"]
            if (
                kind not in {"runtime", "created", "terminal", "cleanup"}
                or kind in captures
                or (
                    payload["invocation_id"] != invocation_id
                    or payload["workload_digest"] != workload.digest
                )
            ):
                raise ValueError("container capture has substituted or duplicate lineage")
            await self._read(
                session,
                reference,
                owner_type="process_container_capture",
                owner_id=f"{invocation_id}:{kind}",
            )
            captures[kind] = payload["data"]
        sources = (workload.input_artifact, receipt.evidence_artifact, *receipt.capture_artifacts)
        for reference in sources:
            await self._read(
                session, reference, owner_type="process_container_receipt", owner_id=receipt.digest
            )
        if (
            data["container_name"] != workload.container_name
            or data["profile_digest"] != workload.profile.digest
            or data["wire_digest"] != workload.wire_digest
            or data["removed"] != receipt.removed
            or data["finished_at"] != receipt.finished_at.isoformat()
            or receipt.finished_at < workload.created_at
        ):
            raise ValueError("container result differs from original intent")
        if receipt.effect_status == "terminated":
            if (
                not data["terminated"]
                or not data["start_attempted"]
                or not receipt.removed
                or (set(captures) != {"runtime", "created", "terminal", "cleanup"})
            ):
                raise ValueError(
                    "container terminal claim lacks retained execution and cleanup evidence"
                )
            runtime, created, terminal = (
                captures["runtime"],
                captures["created"],
                captures["terminal"],
            )
            if runtime["runtime"] != workload.profile.runtime.model_dump(mode="json"):
                raise ValueError("retained container runtime differs from its profile")
            cleanup = captures["cleanup"]
            if (
                cleanup["container_id"] != data["container_id"]
                or cleanup["removed_id"] != data["container_id"]
                or cleanup["remaining"].strip()
                or not data["terminal_verified"]
            ):
                raise ValueError("container removal has no exact retained confirmation")
            if data["snapshots"] != [created, terminal]:
                raise ValueError("container terminal evidence differs from independent captures")
            source = created["Config"]["Cmd"][4].encode("utf-8")
            for snapshot in (created, terminal):
                DockerContainerDriver.validate_inspection(
                    snapshot,
                    profile=workload.profile,
                    source=source,
                    name=workload.container_name,
                    image=runtime["image"],
                    container_id=data["container_id"],
                )
            if (
                created["State"]["Status"] != "created"
                or terminal["State"]["Running"]
                or (
                    terminal["State"]["Status"] != "exited"
                    or terminal["State"]["ExitCode"] != data["exit_code"]
                )
            ):
                raise ValueError("container lacks inspected create-before-start and terminal state")
        if receipt.complete_capture and (data["truncated"] or not data["capture_complete"]):
            raise ValueError("container receipt hides incomplete capture")
        return workload, receipt, data

    @atomic_resource_write
    async def reconcile(
        self, session: AsyncSession, *, invocation_id: str, now: datetime
    ) -> ProcessResourceEvent:
        workload = await self.workload(session, invocation_id)
        account = await self.resources.inspect(session, workload.authorization_digest, lock=True)
        workload, receipt, _ = await self.inspect(session, invocation_id)
        if receipt.effect_status != "terminated" or not receipt.complete_capture:
            raise PermissionError(
                "unknown or incomplete container effects retain their reservation"
            )
        reservation, phase, _ = await self.resources.reservation(session, workload.decision_id)
        references = (
            workload.input_artifact,
            receipt.evidence_artifact,
            *receipt.capture_artifacts,
        )
        sources = tuple(
            sorted({workload.digest, receipt.digest, *(ref.artifact.digest for ref in references)})
        )
        try:
            charged = ProcessResources.model_validate(
                {
                    **reservation.amount.model_dump(),
                    "artifact_bytes": max(
                        reservation.amount.artifact_bytes,
                        sum(ref.artifact.size_bytes for ref in references),
                    ),
                }
            )
            if phase.status != "settled":
                account.charged.plus(charged)
        except ValueError:
            event = await self.resources.stop_overflow(
                session,
                decision_id=workload.decision_id,
                sources=sources,
                now=now,
                basis="container_evidence",
            )
        else:
            event = await self.resources._settle(
                session,
                decision_id=workload.decision_id,
                charged=charged,
                source_digests=sources,
                provider_reported=False,
                container_evidence=True,
                now=now,
            )
        for reference in references:
            if event.sequence <= account.sequence:
                await self._read(
                    session, reference, owner_type="process_resource_event", owner_id=event.digest
                )
            await self.catalog.reference(
                session,
                reference.artifact,
                owner_type="process_resource_event",
                owner_id=event.digest,
            )
        return event


class ProcessContainerExecutor:
    def __init__(
        self,
        *,
        database: Database,
        observations: ProcessObservationStore,
        store: ProcessContainerStore,
        driver: DockerContainerDriver,
    ) -> None:
        self.database, self.observations, self.driver = database, observations, driver
        self.store = store
        if observations.store.container_evidence is not store:
            raise ValueError("container execution must share the process event evidence boundary")
        self._profile = driver.profile
        evidence = observations.store.evidence
        if evidence is not None and evidence.catalog.backend is not store.catalog.backend:
            raise ValueError(
                "container execution and observations must share owned evidence storage"
            )

    async def _expected(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime,
    ) -> tuple[
        ProcessObservationReceipt,
        ProcessObservationDecisionBinding,
        bytes,
        ProcessResourceReservation,
        datetime,
    ]:
        if self.driver.profile != self._profile:
            raise PermissionError("configured container profile drifted")
        p = self._profile
        binding = await self.observations.inspect_decision_binding(session, decision_id=decision_id)
        if binding.disposition != AmberAdmissionDisposition.ADMITTED:
            raise PermissionError("container execution needs an admitted observed action")
        observation = await self.observations.read(
            session,
            observation_id=binding.observation_id,
            rollout_id=rollout_id,
            lease_token=lease_token,
            worker_id=worker_id,
            now=now,
        )
        observed = await self.observations.inspect_receipt(
            session, observation_id=binding.observation_id
        )
        row = await session.get(AmberAdmissionDecisionRow, decision_id)
        assert row is not None
        action = AmberActionRequest.model_validate(row.request_json, strict=False)
        envelope = await self.observations.store.amber.get(
            session, authorization_digest=observed.authorization_digest
        )
        environment = envelope.environment
        reservation, phase, _ = await self.store.resources.reservation(session, decision_id)
        account = await self.store.resources.inspect(session, reservation.authorization_digest)
        data = canonical_json_bytes(observation)
        if (
            binding.declared_role_id != p.role_id
            or binding.declared_worker_model_digest != p.worker_model_digest
            or action.event_kind != ProcessEventKind.TOOL_INVOKED
            or (action.tool_id, action.tool_digest, action.tool_operation)
            != (p.tool_id, p.tool_digest, p.tool_operation)
            or action.requested_destination is not None
            or environment.network_enabled
            or environment.egress_mode != AmberEgressMode.DISABLED
            or environment.allowed_destinations
            or environment.filesystem_scopes != p.filesystem_scopes
            or not environment.reset_between_rollouts
            or (environment.sandbox_id, environment.sandbox_version, environment.sandbox_digest)
            != (p.profile_id, p.version, p.digest)
            or p.reviewed_by not in envelope.required_reviewers
            or p.reviewed_at > action.requested_at
            or not p.command_rights.permits(RightsUse.INTERNAL_RESEARCH)
            or not p.command_rights.permits(RightsUse.EVIDENCE_RETENTION)
            or p.command_rights.reviewed_at is None
            or p.command_rights.reviewed_at > p.reviewed_at
            or account.stopped
            or phase.status not in {"reserved", "started"}
            or reservation.decision_digest != binding.decision_digest
            or reservation.rollout_id != rollout_id
            or reservation.worker_model_digest != p.worker_model_digest
            or p.maximum_wall_ms * 1000 > reservation.amount.action_microseconds
            or p.maximum_retention_bytes > reservation.amount.artifact_bytes
            or len(data) > p.maximum_input_bytes
        ):
            raise PermissionError(
                "container workload differs from its current authority or funding"
            )
        for table, column in (
            (ProcessEventRow, ProcessEventRow.amber_decision_id),
            (ProcessWorkerInvocationRow, ProcessWorkerInvocationRow.amber_decision_id),
        ):
            if (
                await session.scalar(select(column).select_from(table).where(column == decision_id))
                is not None
            ):
                raise PermissionError("container action is already consumed by another effect")
        content = self.observations.store.content
        strings = content._bounded_strings(list(p.argv), maximum_bytes=p.maximum_input_bytes)
        await content._check_identifiers(session, strings, allowed=set(), admitted_digests=set())
        deadline = reservation.created_at + timedelta(
            microseconds=reservation.amount.action_microseconds
        )
        if now >= deadline:
            raise PermissionError("container action deadline expired")
        return observed, binding, data, reservation, deadline

    async def execute(
        self, *, decision_id: str, rollout_id: str, lease_token: str, worker_id: str
    ) -> ProcessContainerReceipt:
        try:
            return await self._execute(
                decision_id=decision_id,
                rollout_id=rollout_id,
                lease_token=lease_token,
                worker_id=worker_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ProcessContainerUnavailableError(
                "process container execution unavailable"
            ) from None

    async def _execute(
        self, *, decision_id: str, rollout_id: str, lease_token: str, worker_id: str
    ) -> ProcessContainerReceipt:
        scope = dict(
            decision_id=decision_id,
            rollout_id=rollout_id,
            lease_token=lease_token,
            worker_id=worker_id,
        )
        now = datetime.now(UTC)
        async with self.database.transaction() as session:
            observed, binding, data, reservation, deadline = await self._expected(
                session, **scope, now=now
            )
            if (
                await session.scalar(
                    select(ProcessContainerWorkloadRow.invocation_id).where(
                        ProcessContainerWorkloadRow.decision_id == decision_id
                    )
                )
                is not None
            ):
                raise PermissionError(
                    "container intent already exists; automatic launch retry is forbidden"
                )
            invocation_id = "process-container-" + uuid4().hex
            input_reference = await self.store.put(
                session,
                data,
                owner_type="process_container_workload",
                owner_id=invocation_id,
                now=now,
            )
            workload = ProcessContainerWorkload(
                invocation_id=invocation_id,
                container_name="padawan-cpu-" + uuid4().hex,
                decision_id=decision_id,
                decision_digest=binding.decision_digest,
                observation_id=observed.observation_id,
                observation_receipt_digest=observed.digest,
                rollout_id=rollout_id,
                execution_digest=observed.execution_digest,
                authorization_digest=observed.authorization_digest,
                lease_token_digest=observed.lease_token_digest,
                worker_id=worker_id,
                profile=self._profile,
                input_artifact=input_reference,
                wire_digest=sha256_digest(container_wire(self._profile, data, deadline)),
                deadline=deadline,
                created_at=now,
            )
            session.add(
                ProcessContainerWorkloadRow(
                    invocation_id=invocation_id,
                    decision_id=decision_id,
                    rollout_id=rollout_id,
                    observation_id=observed.observation_id,
                    container_name=workload.container_name,
                    input_artifact_id=input_reference.artifact.artifact_id,
                    record_digest=workload.digest,
                    record_json=workload.model_dump(mode="json"),
                )
            )
            await session.flush()
            session.add(ProcessContainerHeadRow(invocation_id=invocation_id, status="prepared"))

        captures: list[ForensicArtifactRef] = []

        async def authorize() -> None:
            async with self.database.transaction() as session:
                current, bound, actual, _, original_deadline = await self._expected(
                    session, **scope, now=datetime.now(UTC)
                )
                stored = await self.store.workload(session, invocation_id)
                if current.digest != workload.observation_receipt_digest or (
                    bound.decision_digest != workload.decision_digest
                    or actual != data
                    or original_deadline != workload.deadline
                    or stored != workload
                ):
                    raise PermissionError(
                        "container dispatch no longer matches its retained intent"
                    )

        async def before_start() -> None:
            async with self.database.transaction() as session:
                await self._expected(session, **scope, now=datetime.now(UTC))
                await self.store.resources.start(
                    session, decision_id=decision_id, now=datetime.now(UTC), allow_started=True
                )
                head = await session.get(ProcessContainerHeadRow, invocation_id)
                if head is None or head.status != "prepared":
                    raise PermissionError("container start phase is stale or already dispatched")
                head.status = "starting"

        async def audit(kind: str, payload: dict[str, Any]) -> None:
            encoded = canonical_json_bytes(
                {
                    "kind": kind,
                    "invocation_id": invocation_id,
                    "workload_digest": workload.digest,
                    "data": payload,
                }
            )
            if len(encoded) > self._profile.maximum_control_bytes:
                raise ValueError("container capture exceeds its declared bound")
            async with self.database.transaction() as session:
                reference = await self.store.put(
                    session,
                    encoded,
                    owner_type="process_container_capture",
                    owner_id=f"{invocation_id}:{kind}",
                    now=datetime.now(UTC),
                )
            captures.append(reference)

        cancelled = False
        try:
            result = await self.driver.run(
                name=workload.container_name,
                public_input=data,
                deadline=deadline,
                authorize=authorize,
                before_start=before_start,
                audit=audit,
            )
        except ContainerRunCancelled as exc:
            result, cancelled = exc.evidence, True
        except BaseException:
            async with self.database.transaction() as session:
                head = await session.get(ProcessContainerHeadRow, invocation_id)
                assert head is not None
                head.status = "unknown"
            raise
        async with self.database.transaction() as session:
            reference = await self.store.put(
                session,
                result.bytes(),
                owner_type="process_container_result",
                owner_id=invocation_id,
                now=datetime.now(UTC),
            )
            effect_status: Literal["not_started", "terminated", "unknown"] = "unknown"
            if (
                result.terminated
                and result.terminal_verified
                and result.removed
                and len(captures) == 4
            ):
                effect_status = "terminated"
            elif not result.start_attempted and result.removed:
                effect_status = "not_started"
            receipt = ProcessContainerReceipt(
                invocation_id=invocation_id,
                workload_digest=workload.digest,
                evidence_artifact=reference,
                capture_artifacts=tuple(sorted(captures, key=lambda ref: ref.artifact.artifact_id)),
                effect_status=effect_status,
                removed=result.removed,
                complete_capture=not result.truncated and result.capture_complete,
                finished_at=result.finished_at or datetime.now(UTC),
            )
            for item in (workload.input_artifact, reference, *captures):
                await self.store.catalog.reference(
                    session,
                    item.artifact,
                    owner_type="process_container_receipt",
                    owner_id=receipt.digest,
                )
            session.add(
                ProcessContainerReceiptRow(
                    invocation_id=invocation_id,
                    evidence_artifact_id=reference.artifact.artifact_id,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                )
            )
            head = await session.get(ProcessContainerHeadRow, invocation_id)
            assert head is not None
            head.status = "finished" if effect_status != "unknown" else "unknown"
        if effect_status == "terminated" and receipt.complete_capture:
            async with self.database.transaction() as session:
                accounting = await self.store.reconcile(
                    session, invocation_id=invocation_id, now=datetime.now(UTC)
                )
            if accounting.stopped:
                raise ProcessContainerUnavailableError("process container execution unavailable")
        if cancelled:
            raise asyncio.CancelledError("process container execution interrupted")
        if effect_status != "terminated" or not receipt.complete_capture:
            raise ProcessContainerUnavailableError("process container execution unavailable")
        return receipt
