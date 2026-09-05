"""Authenticated, bounded worker requests. No listener, model, tool or scheduler is launched."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.governance.amber import AmberActionRequest
from padawan.models.database import Database
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ProcessRolloutRow,
    ProcessWorkerRequestRow,
)
from padawan.pprl.coordinator import _action_request, _add_usage
from padawan.pprl.observation_contracts import ProcessWorkerObservation
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ClaimedProcessRollout, ProcessStore, _rollout_from_row
from padawan.pprl.worker_contracts import ProcessWorkerAccess
from padawan.pprl.worker_identities import _record
from padawan.pprl.worker_protocol_contracts import (
    ProcessWorkerReply,
    ProcessWorkerRequest,
    ProcessWorkerRequestPayload,
    ProcessWorkerRequestReceipt,
)


class ProcessWorkerRequestDeniedError(PermissionError):
    """One worker-visible error without privileged detail or credential echo."""


class ProcessWorkerBroker:
    MAXIMUM_REQUEST_BYTES = 65_536
    MAXIMUM_REPLY_BYTES = 1_200_000

    def __init__(
        self,
        *,
        database: Database,
        store: ProcessStore,
        broker_audience: str,
        lease_for: timedelta = timedelta(minutes=5),
    ) -> None:
        if not broker_audience.strip() or lease_for <= timedelta(0):
            raise ValueError("worker broker requires an explicit audience and positive lease")
        self.database, self.store = database, store
        self.broker_audience, self.lease_for = broker_audience, lease_for
        self.observations = ProcessObservationStore(store)

    async def inspect_request(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        request_id: str,
    ) -> tuple[ProcessWorkerRequestReceipt, ProcessWorkerReply]:
        """Privileged historical reconstruction; never exposed by the stream adapter."""
        row = await session.scalar(
            select(ProcessWorkerRequestRow).where(
                ProcessWorkerRequestRow.worker_id == worker_id,
                ProcessWorkerRequestRow.request_id == request_id,
            )
        )
        receipt = _record(ProcessWorkerRequestReceipt, row)
        assert row is not None
        original = ProcessWorkerRequestPayload.model_validate_json(receipt.request_json)
        registration, _ = await self.store.workers.registration(session, worker_id)
        assignment = await self.store.workers.assignment(session, receipt.assignment_id)
        observation = await self.observations.inspect_receipt(
            session, observation_id=receipt.observation_id
        )
        binding = None
        if receipt.decision_id is not None:
            binding = await self.store.workers.require_decision(
                session, assignment=assignment, decision_id=receipt.decision_id
            )
        if (
            receipt.receipt_id != row.receipt_id
            or receipt.worker_id != row.worker_id
            or receipt.request_id != row.request_id
            or receipt.assignment_id != row.assignment_id
            or receipt.observation_id != row.observation_id
            or receipt.decision_id != row.decision_id
            or receipt.registration_digest != registration.digest
            or receipt.assignment_digest != assignment.digest
            or receipt.observation_receipt_digest != observation.digest
            or assignment.worker_id != worker_id
            or original.worker_id != worker_id
            or original.request_id != request_id
            or original.broker_audience != registration.broker_audience
            or observation.worker_id != worker_id
            or observation.rollout_id != assignment.rollout_id
            or observation.lease_token_digest != assignment.lease_token_digest
            or observation.state_digest != assignment.state_digest
            or not assignment.created_at
            <= observation.created_at
            <= receipt.created_at
            < assignment.expires_at
            or (original.kind != "claim" and original.assignment_id != assignment.assignment_id)
            or canonical_json_bytes(original).decode() != receipt.request_json
            or ((binding.digest if binding else None) != receipt.decision_binding_digest)
        ):
            raise ValueError("worker request lost its original authenticated lineage")
        await self.observations._validate_ownership(session, observation, now=receipt.created_at)
        observed = ProcessWorkerObservation.model_validate_json(observation.observation_json)
        await self.store._validate_owned_references(
            session,
            observed.state.artifact_refs,
            execution_digest=registration.execution_digest,
            owner_type="process_worker_request",
            owner_id=receipt.digest,
            now=receipt.created_at,
        )
        disposition = None
        if receipt.decision_id is not None:
            bound = await self.observations.inspect_decision_binding(
                session, decision_id=receipt.decision_id
            )
            if bound.observation_id != receipt.observation_id:
                raise ValueError("worker proposal differs from its observed source")
            disposition = bound.disposition
            assert original.proposal is not None and original.observation_request_id is not None
            source_row = await session.scalar(
                select(ProcessWorkerRequestRow).where(
                    ProcessWorkerRequestRow.worker_id == worker_id,
                    ProcessWorkerRequestRow.request_id == original.observation_request_id,
                )
            )
            source = _record(ProcessWorkerRequestReceipt, source_row)
            source_payload = ProcessWorkerRequestPayload.model_validate_json(source.request_json)
            if source_payload.kind not in {"claim", "observe"}:
                raise ValueError("worker proposal source was not an observation delivery")
            source, _ = await self.inspect_request(
                session, worker_id=worker_id, request_id=original.observation_request_id
            )
            decision_row = await session.get(AmberAdmissionDecisionRow, receipt.decision_id)
            assert decision_row is not None
            action = AmberActionRequest.model_validate(decision_row.request_json, strict=False)
            state = await self.store.get_state(session, state_id=assignment.state_id)
            fields = (
                "event_kind",
                "target_class",
                "tool_id",
                "tool_digest",
                "tool_operation",
                "requested_destination",
            )
            if (
                source.observation_id != receipt.observation_id
                or source.assignment_id != receipt.assignment_id
                or source.created_at > receipt.created_at
                or any(
                    getattr(action, field) != getattr(original.proposal, field) for field in fields
                )
                or action.triggered_stop_conditions
                != tuple(sorted(original.proposal.triggered_stop_conditions))
                or action.projected_usage
                != _add_usage(state.payload.budget_usage, original.proposal.incremental_usage)
            ):
                raise ValueError("worker admission differs from its exact observed proposal")
        if (original.kind == "propose") != (disposition is not None):
            raise ValueError("worker reply has an inconsistent operation")
        reply = ProcessWorkerReply(
            request_id=request_id,
            assignment_id=receipt.assignment_id,
            observation=None if disposition is not None else observed,
            disposition=disposition,
        )
        if sha256_digest(reply) != receipt.reply_digest:
            raise ValueError("worker reply differs from retained request evidence")
        return receipt, reply

    async def request(self, request: ProcessWorkerRequest) -> ProcessWorkerReply:
        try:
            return await self._request(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        # Even exception context must not preserve credential-bearing validation details.
        raise ProcessWorkerRequestDeniedError("worker request denied")

    async def _prior_reply(
        self,
        session: AsyncSession,
        *,
        payload: ProcessWorkerRequestPayload,
        access: ProcessWorkerAccess,
        now: datetime,
        data: bytes,
    ) -> ProcessWorkerReply | None:
        prior = await session.scalar(
            select(ProcessWorkerRequestRow).where(
                ProcessWorkerRequestRow.worker_id == payload.worker_id,
                ProcessWorkerRequestRow.request_id == payload.request_id,
            )
        )
        if prior is None:
            return None
        receipt, reply = await self.inspect_request(
            session, worker_id=payload.worker_id, request_id=payload.request_id
        )
        if receipt.request_json != data.decode():
            raise PermissionError("worker request ID cannot change content")
        access = access.assigned(receipt.assignment_id)
        assignment = await self.store.workers.assignment(session, receipt.assignment_id)
        rollout = await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == assignment.rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if rollout is None:
            raise PermissionError("worker request has no current rollout")
        await self.store.workers.require(session, rollout=rollout, access=access, now=now)
        await self.observations.read(
            session,
            observation_id=receipt.observation_id,
            rollout_id=rollout.rollout_id,
            lease_token=rollout.lease_token or "",
            worker_id=payload.worker_id,
            now=now,
            worker_access=access,
        )
        return reply

    async def _request(self, request: ProcessWorkerRequest) -> ProcessWorkerReply:
        data = canonical_json_bytes(request)
        if (
            len(data) > self.MAXIMUM_REQUEST_BYTES
            or request.broker_audience != self.broker_audience
            or request.credential.get_secret_value().encode() in data
        ):
            raise PermissionError("worker request is invalid")
        # Detach nested proposal fields without ever retaining the credential as request JSON.
        payload = ProcessWorkerRequestPayload.model_validate_json(data)
        access = ProcessWorkerAccess(
            payload.worker_id, self.broker_audience, request.credential, payload.assignment_id
        )
        now = datetime.now(UTC)
        async with self.database.transaction() as session:
            registered = await self.store.workers.identify(session, access, now=now)
            prior_reply = await self._prior_reply(
                session, payload=payload, access=access, now=now, data=data
            )
            if prior_reply is not None:
                return prior_reply
            if payload.kind == "claim":
                claimed = await self.store.claim_next(
                    session,
                    worker_id=registered.worker_id,
                    lease_for=self.lease_for,
                    now=now,
                    worker_access=access,
                )
                if claimed is None or claimed.worker_assignment_id is None:
                    raise PermissionError("worker has no available assignment")
                access = access.assigned(claimed.worker_assignment_id)
                rollout = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
                assert rollout is not None
            else:
                assert payload.assignment_id is not None
                assignment = await self.store.workers.assignment(session, payload.assignment_id)
                if assignment.worker_id != registered.worker_id:
                    raise PermissionError("worker cannot address another assignment")
                rollout = await session.scalar(
                    select(ProcessRolloutRow)
                    .where(ProcessRolloutRow.rollout_id == assignment.rollout_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if rollout is None:
                    raise PermissionError("worker request has no rollout")
                await self.store.workers.require(session, rollout=rollout, access=access, now=now)
                # Another broker may have committed this request while we waited for
                # the rollout lock. Never turn concurrent replay into another admission.
                prior_reply = await self._prior_reply(
                    session, payload=payload, access=access, now=now, data=data
                )
                if prior_reply is not None:
                    return prior_reply
                claimed = ClaimedProcessRollout(
                    rollout=_rollout_from_row(rollout),
                    state=await self.store.get_state(session, state_id=rollout.current_state_id),
                    lease_token=rollout.lease_token or "",
                    worker_assignment_id=assignment.assignment_id,
                )
            now = datetime.now(UTC)
            decision = None
            if payload.kind == "propose":
                assert payload.observation_request_id is not None and payload.proposal is not None
                source, source_reply = await self.inspect_request(
                    session,
                    worker_id=registered.worker_id,
                    request_id=payload.observation_request_id,
                )
                if source.assignment_id != access.assignment_id or source_reply.observation is None:
                    raise PermissionError("worker proposal has no delivered observation")
                observed_id = source.observation_id
                observed = await self.observations.read(
                    session,
                    observation_id=observed_id,
                    rollout_id=rollout.rollout_id,
                    lease_token=claimed.lease_token,
                    worker_id=registered.worker_id,
                    now=now,
                    worker_access=access,
                )
                execution, program, _ = await self.store.workers._context(
                    session, registered.execution_digest, now=now
                )
                proposal = payload.proposal.assigned(
                    registered.role_id, registered.worker_model_digest
                )
                action = _action_request(
                    claimed=claimed,
                    proposal=proposal,
                    execution=execution,
                    program=program,
                    requested_at=now,
                )
                decision = await self.store.amber.admit(
                    session, request=action, active_workers=0, worker_access=access
                )
                await self.observations.bind_decision(
                    session,
                    observation_id=observed_id,
                    decision_id=decision.decision_id,
                    rollout_id=rollout.rollout_id,
                    lease_token=claimed.lease_token,
                    worker_id=registered.worker_id,
                    now=now,
                    worker_access=access,
                )
            else:
                result = await self.observations.observe_claim(
                    session,
                    rollout_id=rollout.rollout_id,
                    lease_token=claimed.lease_token,
                    worker_id=registered.worker_id,
                    now=now,
                    worker_access=access,
                )
                observed_id, observed = result.observation_id, result.observation
            assert access.assignment_id is not None
            assignment = await self.store.workers.assignment(session, access.assignment_id)
            observation = await self.observations.inspect_receipt(
                session, observation_id=observed_id
            )
            decision_binding = (
                None
                if decision is None
                else await self.store.workers.require_decision(
                    session, assignment=assignment, decision_id=decision.decision_id
                )
            )
            reply = ProcessWorkerReply(
                request_id=payload.request_id,
                assignment_id=assignment.assignment_id,
                observation=None if decision else observed,
                disposition=decision.disposition if decision else None,
            )
            if len(canonical_json_bytes(reply)) > self.MAXIMUM_REPLY_BYTES:
                raise PermissionError("worker reply exceeds its bound")
            receipt = ProcessWorkerRequestReceipt(
                receipt_id="worker-request-" + uuid4().hex,
                worker_id=registered.worker_id,
                registration_digest=registered.digest,
                request_id=payload.request_id,
                request_json=data.decode(),
                request_digest=sha256_digest(data),
                assignment_id=assignment.assignment_id,
                assignment_digest=assignment.digest,
                observation_id=observation.observation_id,
                observation_receipt_digest=observation.digest,
                decision_id=decision.decision_id if decision else None,
                decision_binding_digest=decision_binding.digest if decision_binding else None,
                reply_digest=sha256_digest(reply),
                created_at=now,
            )
            # The assignment check holds this worker's row lock. Quotas include the source
            # observation bytes conservatively even when an existing receipt is reused.
            scope = await self.store.workers.scope(session, registered.execution_digest)
            assert scope is not None
            history = list(
                await session.scalars(
                    select(ProcessWorkerRequestRow).where(
                        ProcessWorkerRequestRow.worker_id == registered.worker_id
                    )
                )
            )
            retained_bytes = len(canonical_json_bytes(receipt)) + len(
                observation.observation_json.encode()
            )
            for row in history:
                older = _record(ProcessWorkerRequestReceipt, row)
                older_observation = await self.observations.inspect_receipt(
                    session, observation_id=older.observation_id
                )
                retained_bytes += len(canonical_json_bytes(older)) + len(
                    older_observation.observation_json.encode()
                )
            if (
                len(history) >= scope.maximum_request_records
                or retained_bytes > scope.maximum_request_history_bytes
            ):
                raise PermissionError("worker control history quota is exhausted")
            for reference in observed.state.artifact_refs:
                assert self.store.evidence is not None
                await self.store.evidence.retain_for_process(
                    session,
                    reference=reference,
                    execution_digest=registered.execution_digest,
                    owner_type="process_worker_request",
                    owner_id=receipt.digest,
                    now=now,
                )
            session.add(
                ProcessWorkerRequestRow(
                    receipt_id=receipt.receipt_id,
                    worker_id=registered.worker_id,
                    request_id=payload.request_id,
                    assignment_id=assignment.assignment_id,
                    observation_id=observation.observation_id,
                    decision_id=receipt.decision_id,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                )
            )
            await session.flush()
            _, retained = await self.inspect_request(
                session, worker_id=registered.worker_id, request_id=payload.request_id
            )
            return retained


async def serve_worker_stream(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, broker: ProcessWorkerBroker
) -> None:
    """One bounded request on an already connected channel; opens no socket/listener."""
    try:
        async with asyncio.timeout(10):
            size = int.from_bytes(await reader.readexactly(4), "big")
            if not 0 < size <= broker.MAXIMUM_REQUEST_BYTES:
                raise ValueError("worker frame length is invalid")
            request = ProcessWorkerRequest.model_validate_json(await reader.readexactly(size))
            response = canonical_json_bytes(await broker.request(request))
    except asyncio.CancelledError:
        writer.close()
        raise
    except Exception:
        response = b'{"error":"worker_request_denied"}'
    try:
        async with asyncio.timeout(5):
            writer.write(len(response).to_bytes(4, "big") + response)
            await writer.drain()
    except (ConnectionError, TimeoutError):
        pass
    finally:
        writer.close()
        try:
            async with asyncio.timeout(1):
                await writer.wait_closed()
        except (ConnectionError, TimeoutError):
            pass
