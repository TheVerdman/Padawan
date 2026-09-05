"""Authorization-wide conserved allowances, independent of worker/state snapshots."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Concatenate, Literal

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAuthorizationEnvelope,
    AmberStatus,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    AmberAuthorizationRow,
    ProcessContainerWorkloadRow,
    ProcessEventRow,
    ProcessResourceAccountRow,
    ProcessResourceEventRow,
    ProcessResourceGrantRow,
    ProcessResourceReservationHeadRow,
    ProcessResourceReservationRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.contracts import ProjectBudgetUsage
from padawan.pprl.resource_contracts import (
    ProcessResourceEvent,
    ProcessResourceGrant,
    ProcessResourceReservation,
    ProcessResources,
    resources_from_usage,
)
from padawan.pprl.worker_contracts import ProcessWorkerAccess


def unresolved_action_query() -> Select[tuple[str]]:
    """Admissions are the root: deleting a reservation cannot erase a pending effect."""
    return (
        select(AmberAdmissionDecisionRow.decision_id)
        .outerjoin(
            ProcessResourceReservationRow,
            ProcessResourceReservationRow.decision_id == AmberAdmissionDecisionRow.decision_id,
        )
        .outerjoin(
            ProcessResourceReservationHeadRow,
            ProcessResourceReservationHeadRow.decision_id
            == ProcessResourceReservationRow.decision_id,
        )
        .outerjoin(
            ProcessEventRow,
            ProcessEventRow.amber_decision_id == AmberAdmissionDecisionRow.decision_id,
        )
        .where(
            or_(
                AmberAdmissionDecisionRow.disposition == "admitted",
                ProcessResourceReservationRow.decision_id.is_not(None),
            ),
            or_(
                ProcessResourceReservationRow.decision_id.is_(None),
                and_(
                    ProcessEventRow.event_id.is_(None),
                    or_(
                        ProcessResourceReservationHeadRow.status.is_(None),
                        ProcessResourceReservationHeadRow.status != "released",
                    ),
                ),
            ),
        )
    )


def unresolved_process_actions() -> Exists:
    """Cheap claim filter; selected candidates still require full source validation."""
    return (
        unresolved_action_query()
        .where(AmberAdmissionDecisionRow.rollout_id == ProcessRolloutRow.rollout_id)
        .correlate(ProcessRolloutRow)
        .exists()
    )


def atomic_resource_write[S, **P, R](
    operation: Callable[Concatenate[S, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[S, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(self: S, session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with session.begin_nested():
            return await operation(self, session, *args, **kwargs)

    return wrapped


class ProcessResourceStore:
    """Privileged broker API. SQL/credentials/reviewer identities remain trusted."""

    @atomic_resource_write
    async def fund(
        self, session: AsyncSession, grant: ProcessResourceGrant
    ) -> ProcessResourceGrant:
        grant = ProcessResourceGrant.model_validate_json(grant.model_dump_json())
        envelope = await self._authorization(session, grant.authorization_digest)
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == grant.authorization_digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if head is None or head.status not in {
            AmberStatus.AUTHORIZED.value,
            AmberStatus.ACTIVE.value,
        }:
            raise PermissionError("funding requires reviewed authorized or active Amber authority")
        if not (_utc(head.updated_at) <= grant.created_at < envelope.expires_at):
            raise PermissionError("resource grant falls outside current funding authority")
        if grant.reviewed_by not in envelope.required_reviewers:
            raise PermissionError("resource funding requires a named Amber reviewer")
        if not grant.review_evidence.strip():
            raise PermissionError("resource funding requires retained review evidence")
        cap = ProjectBudgetUsage(**envelope.budgets.model_dump(exclude={"concurrent_workers"}))
        if not resources_from_usage(cap, ceiling=False).covers(grant.capacity) or (
            grant.concurrent_reservations > envelope.budgets.concurrent_workers
        ):
            raise PermissionError("resource funding exceeds Amber ceilings")
        if {rate.worker_model_digest for rate in grant.model_rates} != set(
            envelope.allowed_worker_model_digests
        ):
            raise PermissionError("funding must explicitly price every authorized worker model")
        prior = await session.get(ProcessResourceGrantRow, grant.authorization_digest)
        if prior is not None:
            if await self.grant(session, grant.authorization_digest) != grant:
                raise PermissionError("resource authorization is already funded differently")
            await self.inspect(session, grant.authorization_digest)
            return grant
        if (
            await session.scalar(
                select(ProcessRolloutRow.rollout_id)
                .where(ProcessRolloutRow.authorization_digest == grant.authorization_digest)
                .limit(1)
            )
            is not None
        ):
            raise PermissionError("legacy rollouts cannot receive implicit fresh funding")
        session.add(
            ProcessResourceGrantRow(
                authorization_digest=grant.authorization_digest,
                grant_id=grant.grant_id,
                record_digest=grant.digest,
                record_json=grant.model_dump(mode="json"),
            )
        )
        await session.flush()
        event = ProcessResourceEvent(
            authorization_digest=grant.authorization_digest,
            sequence=0,
            previous_digest=None,
            grant_digest=grant.digest,
            kind="grant",
            subject_id=grant.grant_id,
            amount=grant.capacity,
            held=ProcessResources(),
            charged=ProcessResources(),
            open_reservations=0,
            stopped=False,
            basis="grant",
            actor_id=grant.reviewed_by,
            reason=grant.review_evidence,
            created_at=grant.created_at,
        )
        session.add(
            ProcessResourceAccountRow(
                authorization_digest=grant.authorization_digest,
                sequence=0,
                record_digest=event.digest,
            )
        )
        await session.flush()
        await self._append(session, event)
        return grant

    async def grant(self, session: AsyncSession, authorization_digest: str) -> ProcessResourceGrant:
        row = await session.get(ProcessResourceGrantRow, authorization_digest)
        if row is None:
            raise PermissionError("authorization has no explicitly reviewed resource funding")
        record = ProcessResourceGrant.model_validate(row.record_json, strict=False)
        if sha256_digest(row.record_json) != row.record_digest or (
            record.digest,
            record.authorization_digest,
            record.grant_id,
        ) != (
            row.record_digest,
            row.authorization_digest,
            row.grant_id,
        ):
            raise ValueError("resource funding record is corrupt")
        return record

    async def inspect(
        self,
        session: AsyncSession,
        authorization_digest: str,
        *,
        lock: bool = False,
    ) -> ProcessResourceEvent:
        query = (
            select(ProcessResourceAccountRow)
            .where(ProcessResourceAccountRow.authorization_digest == authorization_digest)
            .execution_options(populate_existing=True)
        )
        head = await session.scalar(query.with_for_update() if lock else query)
        if head is None:
            raise PermissionError("authorization has no resource account")
        grant = await self.grant(session, authorization_digest)
        record = await self._event(session, head.record_digest)
        if (record.authorization_digest, record.sequence, record.grant_digest) != (
            authorization_digest,
            head.sequence,
            grant.digest,
        ):
            raise ValueError("resource account head differs from its retained journal")
        if not record.stopped and not grant.capacity.covers(record.held.plus(record.charged)):
            raise ValueError("resource account silently exceeds its funding")
        return record

    async def admission_reasons(
        self,
        session: AsyncSession,
        *,
        request: AmberActionRequest,
        previous: ProjectBudgetUsage,
        _pending_decision_id: str | None = None,
    ) -> tuple[str, ...]:
        if await session.get(ProcessResourceAccountRow, request.authorization_digest) is None:
            return ("resource_funding_missing",)
        current = await self.inspect(session, request.authorization_digest, lock=True)
        grant = await self.grant(session, request.authorization_digest)
        amount = resources_from_usage(request.projected_usage, previous=previous)
        reasons: list[str] = []
        if current.stopped:
            reasons.append("resource_account_stopped")
        if not grant.capacity.covers(current.held.plus(current.charged).plus(amount)):
            reasons.append("shared_resource_capacity_exhausted")
        if current.open_reservations >= grant.concurrent_reservations:
            reasons.append("shared_resource_concurrency_exhausted")
        prior = await session.scalar(
            select(ProcessResourceReservationRow).where(
                ProcessResourceReservationRow.rollout_id == request.rollout_id,
                ProcessResourceReservationRow.state_digest == request.state_digest,
                ProcessResourceReservationRow.lease_token_digest == request.lease_token_digest,
            )
        )
        if prior is not None:
            reasons.append("resource_action_already_reserved")
        pending = unresolved_action_query().where(
            AmberAdmissionDecisionRow.rollout_id == request.rollout_id
        )
        if _pending_decision_id is not None:
            pending = pending.where(AmberAdmissionDecisionRow.decision_id != _pending_decision_id)
        if await session.scalar(pending.limit(1)) is not None:
            reasons.append("rollout_recovery_required")
        else:
            await self.assert_rollout_recoverable(
                session, rollout_id=request.rollout_id, _pending_decision_id=_pending_decision_id
            )
        return tuple(sorted(reasons))

    async def assert_rollout_recoverable(
        self, session: AsyncSession, *, rollout_id: str, _pending_decision_id: str | None = None
    ) -> None:
        """Closed head fields alone cannot erase missing or corrupt recovery evidence."""
        from padawan.pprl.store import _event_from_row

        rollout = await session.get(ProcessRolloutRow, rollout_id)
        if rollout is not None:
            from padawan.pprl.tasks import ProcessTaskStore

            await ProcessTaskStore().check_rollout(session, rollout)
        if rollout is not None and rollout.terminal_abandonment_id is not None:
            raise PermissionError("terminally abandoned rollout cannot resume")

        decisions = set(
            await session.scalars(
                select(ProcessResourceReservationRow.decision_id).where(
                    ProcessResourceReservationRow.rollout_id == rollout_id
                )
            )
        )
        decisions.update(
            await session.scalars(
                select(AmberAdmissionDecisionRow.decision_id).where(
                    AmberAdmissionDecisionRow.rollout_id == rollout_id,
                    AmberAdmissionDecisionRow.disposition == "admitted",
                )
            )
        )
        for decision_id in decisions:
            if decision_id == _pending_decision_id:
                continue
            reservation, phase, source = await self.reservation(session, decision_id)
            if phase.status == "released":
                authority = await self._authorization(session, reservation.authorization_digest)
                if (
                    source.actor_id not in authority.required_reviewers
                    or source.basis != "reviewed"
                    or source.amount != reservation.amount
                    or not source.reason.strip()
                ):
                    raise ValueError("resource release lost its reviewed source")
                await self.assert_no_effect_intent(session, decision_id=decision_id)
                continue
            row = await session.scalar(
                select(ProcessEventRow).where(ProcessEventRow.amber_decision_id == decision_id)
            )
            if row is None or phase.status != "settled":
                raise PermissionError("rollout has an uncommitted effect requiring recovery")
            event = _event_from_row(row)
            if (
                event.rollout_id != rollout_id
                or event.lease_token_digest != reservation.lease_token_digest
                or event.amber_authorization_digest != reservation.authorization_digest
            ):
                raise ValueError("closed effect lost its exact committed transition")

    @atomic_resource_write
    async def reserve(
        self,
        session: AsyncSession,
        *,
        decision: AmberAdmissionDecision,
        request: AmberActionRequest,
        previous: ProjectBudgetUsage,
    ) -> ProcessResourceReservation:
        # Amber has just inserted this admitted decision in the same transaction.
        # Only this not-yet-published reservation is exempt from the historical gap
        # check; the public admission path and all claims have no exemption.
        reasons = await self.admission_reasons(
            session, request=request, previous=previous, _pending_decision_id=decision.decision_id
        )
        if reasons or decision.disposition.value != "admitted":
            raise PermissionError(f"resource reservation is not admitted: {reasons}")
        grant = await self.grant(session, request.authorization_digest)
        current = await self.inspect(session, request.authorization_digest, lock=True)
        rollout = await session.get(ProcessRolloutRow, request.rollout_id)
        decision_row = await session.get(AmberAdmissionDecisionRow, decision.decision_id)
        if (
            rollout is None
            or decision_row is None
            or (
                decision_row.record_digest != sha256_digest(decision)
                or decision_row.request_digest != sha256_digest(request)
            )
        ):
            raise PermissionError("resource reservation has no exact durable admission")
        reservation = ProcessResourceReservation(
            decision_id=decision.decision_id,
            decision_digest=sha256_digest(decision),
            request_digest=sha256_digest(request),
            grant_digest=grant.digest,
            authorization_digest=request.authorization_digest,
            authorization_sequence=decision.authorization_sequence,
            rollout_id=request.rollout_id,
            execution_digest=rollout.execution_digest,
            state_digest=request.state_digest,
            lease_token_digest=request.lease_token_digest,
            worker_model_digest=request.worker_model_digest,
            amount=resources_from_usage(request.projected_usage, previous=previous),
            created_at=request.requested_at,
        )
        session.add(
            ProcessResourceReservationRow(
                decision_id=reservation.decision_id,
                authorization_digest=reservation.authorization_digest,
                rollout_id=reservation.rollout_id,
                state_digest=reservation.state_digest,
                lease_token_digest=reservation.lease_token_digest,
                record_digest=reservation.digest,
                record_json=reservation.model_dump(mode="json"),
            )
        )
        await session.flush()
        event = self._next(
            current,
            kind="reserve",
            subject_id=reservation.decision_id,
            reservation_digest=reservation.digest,
            amount=reservation.amount,
            held=current.held.plus(reservation.amount),
            open_reservations=current.open_reservations + 1,
            basis="reservation",
            created_at=request.requested_at,
        )
        await self._append(session, event)
        session.add(
            ProcessResourceReservationHeadRow(
                decision_id=reservation.decision_id,
                status="reserved",
                record_digest=event.digest,
            )
        )
        await session.flush()
        return reservation

    async def reservation(
        self,
        session: AsyncSession,
        decision_id: str,
    ) -> tuple[ProcessResourceReservation, ProcessResourceReservationHeadRow, ProcessResourceEvent]:
        row = await session.get(ProcessResourceReservationRow, decision_id)
        if row is None:
            raise PermissionError("process action has no conserved resource reservation")
        record = ProcessResourceReservation.model_validate(row.record_json, strict=False)
        if sha256_digest(row.record_json) != row.record_digest:
            raise ValueError("resource reservation JSON is corrupt")
        from padawan.pprl.tasks import ProcessTaskStore

        rollout = await session.get(ProcessRolloutRow, row.rollout_id)
        if rollout is None:
            raise ValueError("resource reservation lost its task owner")
        await ProcessTaskStore().check_rollout(session, rollout)
        if (
            record.digest,
            record.authorization_digest,
            record.rollout_id,
            record.state_digest,
            record.lease_token_digest,
            record.decision_id,
        ) != (
            row.record_digest,
            row.authorization_digest,
            row.rollout_id,
            row.state_digest,
            row.lease_token_digest,
            row.decision_id,
        ):
            raise ValueError("resource reservation is corrupt")
        decision = await session.get(AmberAdmissionDecisionRow, decision_id)
        if decision is None or (
            sha256_digest(decision.record_json) != record.decision_digest
            or sha256_digest(decision.request_json) != record.request_digest
            or decision.disposition != "admitted"
            or AmberAdmissionDecision.model_validate(
                decision.record_json, strict=False
            ).disposition.value
            != "admitted"
        ):
            raise ValueError("resource reservation has lost its admission lineage")
        from padawan.pprl.worker_identities import ProcessWorkerIdentityStore

        await ProcessWorkerIdentityStore().inspect_decision(session, decision_id=decision_id)
        grant = await self.grant(session, record.authorization_digest)
        if record.grant_digest != grant.digest:
            raise ValueError("resource reservation differs from its original funding")
        head = await session.scalar(
            select(ProcessResourceReservationHeadRow)
            .where(ProcessResourceReservationHeadRow.decision_id == decision_id)
            .execution_options(populate_existing=True)
        )
        if head is None:
            raise ValueError("resource reservation lost its phase")
        event = await self._event(session, head.record_digest)
        phases = {
            "reserve": "reserved",
            "start": "started",
            "settle": "settled",
            "release": "released",
        }
        if (event.subject_id, event.reservation_digest, event.authorization_digest) != (
            decision_id,
            record.digest,
            record.authorization_digest,
        ) or phases.get(event.kind) != head.status:
            raise ValueError("resource reservation phase differs from its journal")
        return record, head, event

    @atomic_resource_write
    async def start(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        now: datetime,
        allow_started: bool = False,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> None:
        reservation, _, _ = await self.reservation(session, decision_id)
        rollout = await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == reservation.rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if rollout is not None:
            # Lazy import avoids the shared transaction-helper import cycle.
            from padawan.pprl.worker_identities import ProcessWorkerIdentityStore

            workers = ProcessWorkerIdentityStore()
            assignment = await workers.require(
                session,
                rollout=rollout,
                access=worker_access,
                now=now,
                worker_model_digest=reservation.worker_model_digest,
            )
            await workers.require_decision(session, assignment=assignment, decision_id=decision_id)
        authority = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(
                AmberAuthorizationHeadRow.authorization_digest == reservation.authorization_digest
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        envelope = await self._authorization(session, reservation.authorization_digest)
        current = await self.inspect(session, reservation.authorization_digest, lock=True)
        reservation, head, _ = await self.reservation(session, decision_id)
        state = (
            None
            if rollout is None
            else await session.get(ProcessStateRow, rollout.current_state_id)
        )
        if (
            rollout is None
            or authority is None
            or state is None
            or current.stopped
            or rollout.status != "active"
            or rollout.lease_token is None
            or sha256_digest(rollout.lease_token) != reservation.lease_token_digest
            or rollout.lease_expires_at is None
            or now >= _utc(rollout.lease_expires_at)
            or state.state_digest != reservation.state_digest
            or authority.status != AmberStatus.ACTIVE.value
            or authority.sequence != reservation.authorization_sequence
            or now >= envelope.expires_at
            or now < reservation.created_at
            or now
            >= reservation.created_at
            + timedelta(microseconds=reservation.amount.action_microseconds)
        ):
            raise PermissionError("resource dispatch has stale or stopped authority")
        if head.status == "started" and allow_started:
            return
        if head.status != "reserved":
            raise PermissionError("resource reservation cannot start another effect")
        event = self._next(
            current,
            kind="start",
            subject_id=decision_id,
            reservation_digest=reservation.digest,
            amount=ProcessResources(),
            basis="reservation",
            created_at=now,
        )
        await self._append(session, event)
        head.status, head.record_digest = "started", event.digest
        await session.flush()

    @atomic_resource_write
    async def charge_initial(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        authorization_digest: str,
        usage: ProjectBudgetUsage,
        state_digest: str,
        now: datetime,
    ) -> None:
        current = await self.inspect(session, authorization_digest, lock=True)
        grant = await self.grant(session, authorization_digest)
        amount = resources_from_usage(usage)
        if current.stopped or not grant.capacity.covers(
            current.charged.plus(current.held).plus(amount)
        ):
            raise PermissionError("initial state exceeds shared resource funding")
        await self._append(
            session,
            self._next(
                current,
                kind="initial",
                subject_id=rollout_id,
                amount=amount,
                charged=current.charged.plus(amount),
                basis="committed_ceiling",
                source_digests=(state_digest,),
                created_at=now,
            ),
        )

    @atomic_resource_write
    async def _settle(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        charged: ProcessResources,
        source_digests: tuple[str, ...],
        provider_reported: bool,
        now: datetime,
        container_evidence: bool = False,
    ) -> ProcessResourceEvent:
        """Internal broker primitive; callers must reconstruct evidence before this mutation."""
        reservation, _, _ = await self.reservation(session, decision_id)
        current = await self.inspect(session, reservation.authorization_digest, lock=True)
        reservation, head, prior = await self.reservation(session, decision_id)
        sources = tuple(sorted(set(source_digests)))
        if provider_reported and container_evidence:
            raise ValueError("resource settlement must have one evidence basis")
        basis = (
            "container_evidence"
            if container_evidence
            else ("provider_reported" if provider_reported else "committed_ceiling")
        )
        if head.status == "settled":
            if prior.amount != charged or prior.source_digests != sources or prior.basis != basis:
                raise ValueError("resource settlement retry changes original accounting evidence")
            return prior
        if head.status not in {"reserved", "started"} or not sources:
            raise PermissionError("resource settlement has no live reservation or evidence")
        if charged.actions != reservation.amount.actions or (
            charged.action_microseconds != reservation.amount.action_microseconds
            or charged.artifact_bytes < reservation.amount.artifact_bytes
        ):
            raise PermissionError("resource reconciliation cannot refund unmeasured allowances")
        if (
            container_evidence
            and charged.model_copy(update={"artifact_bytes": reservation.amount.artifact_bytes})
            != reservation.amount
        ):
            raise PermissionError("container accounting cannot refund unmetered allowances")
        if not provider_reported and not container_evidence and charged != reservation.amount:
            raise PermissionError("generic actions must charge their full unmetered allowance")
        event = self._next(
            current,
            kind="settle",
            subject_id=decision_id,
            reservation_digest=reservation.digest,
            amount=charged,
            held=current.held.minus(reservation.amount),
            charged=current.charged.plus(charged),
            open_reservations=current.open_reservations - 1,
            stopped=current.stopped or not reservation.amount.covers(charged),
            source_digests=sources,
            basis=basis,
            created_at=now,
        )
        await self._append(session, event)
        head.status, head.record_digest = "settled", event.digest
        await session.flush()
        return event

    @atomic_resource_write
    async def stop_overflow(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        sources: tuple[str, ...],
        now: datetime,
        basis: Literal["provider_reported", "container_evidence"] = "provider_reported",
    ) -> ProcessResourceEvent:
        reservation, _, _ = await self.reservation(session, decision_id)
        current = await self.inspect(session, reservation.authorization_digest, lock=True)
        if (
            current.kind == "stop"
            and current.subject_id == decision_id
            and current.source_digests == sources
        ):
            return current
        event = self._next(
            current,
            kind="stop",
            subject_id=decision_id,
            reservation_digest=reservation.digest,
            amount=ProcessResources(),
            stopped=True,
            source_digests=sources,
            basis=basis,
            reason="retained usage exceeds accounting arithmetic; hold remains unresolved",
            created_at=now,
        )
        await self._append(session, event)
        return event

    async def settle_event(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        event_digest: str,
        now: datetime,
    ) -> None:
        # Only an already retained exact event can settle a generic action. An arbitrary
        # worker-provided digest is not evidence that a started external effect ended.
        from padawan.pprl.store import _event_from_row

        row = await session.scalar(
            select(ProcessEventRow).where(ProcessEventRow.amber_decision_id == decision_id)
        )
        if row is None or _event_from_row(row).event_digest != event_digest:
            raise PermissionError("resource settlement requires the exact committed process event")
        reservation, head, _ = await self.reservation(session, decision_id)
        if head.status == "settled":
            return  # Model completion already charged this action, including artifact allowance.
        container = await session.scalar(
            select(ProcessContainerWorkloadRow.invocation_id).where(
                ProcessContainerWorkloadRow.decision_id == decision_id
            )
        )
        if container is not None:
            raise PermissionError("container action has no completed resource reconciliation")
        invocation = await session.scalar(
            select(ProcessWorkerInvocationRow).where(
                ProcessWorkerInvocationRow.amber_decision_id == decision_id
            )
        )
        if invocation is not None:
            raise PermissionError("model action has no completed resource reconciliation")
        await self._settle(
            session,
            decision_id=decision_id,
            charged=reservation.amount,
            source_digests=(event_digest,),
            provider_reported=False,
            now=now,
        )

    @atomic_resource_write
    async def release_unstarted(
        self,
        session: AsyncSession,
        *,
        decision_id: str,
        reviewer_id: str,
        evidence: str,
        now: datetime,
    ) -> ProcessResourceEvent:
        reservation, _, _ = await self.reservation(session, decision_id)
        envelope = await self._authorization(session, reservation.authorization_digest)
        # Serialize the no-intent proof with admission, durable intent and start.
        # A prepared container can already exist outside SQL even while reserved.
        await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == reservation.rollout_id)
            .with_for_update()
        )
        current = await self.inspect(session, reservation.authorization_digest, lock=True)
        reservation, head, prior = await self.reservation(session, decision_id)
        if reviewer_id not in envelope.required_reviewers or not evidence.strip():
            raise PermissionError("resource release requires a named reviewer and retained reason")
        if now < reservation.created_at:
            raise ValueError("resource release predates its reservation")
        if head.status == "released":
            if prior.actor_id != reviewer_id or prior.reason != evidence:
                raise ValueError("resource release retry changes its evidence")
            return prior
        if head.status != "reserved":
            raise PermissionError("started or uncertain effects cannot receive an automatic refund")
        await self.assert_no_effect_intent(session, decision_id=decision_id)
        event = self._next(
            current,
            kind="release",
            subject_id=decision_id,
            reservation_digest=reservation.digest,
            amount=reservation.amount,
            held=current.held.minus(reservation.amount),
            open_reservations=current.open_reservations - 1,
            basis="reviewed",
            actor_id=reviewer_id,
            reason=evidence,
            created_at=now,
        )
        await self._append(session, event)
        head.status, head.record_digest = "released", event.digest
        await session.flush()
        return event

    async def assert_no_effect_intent(self, session: AsyncSession, *, decision_id: str) -> None:
        if (
            await session.scalar(
                select(ProcessContainerWorkloadRow.invocation_id).where(
                    ProcessContainerWorkloadRow.decision_id == decision_id
                )
            )
            is not None
            or await session.scalar(
                select(ProcessWorkerInvocationRow.invocation_id).where(
                    ProcessWorkerInvocationRow.amber_decision_id == decision_id
                )
            )
            is not None
        ):
            raise PermissionError("retained effect intent requires explicit recovery evidence")

    async def replay(
        self, session: AsyncSession, authorization_digest: str
    ) -> tuple[ProcessResourceEvent, ...]:
        current = await self.inspect(session, authorization_digest)
        rows = (
            await session.scalars(
                select(ProcessResourceEventRow)
                .where(ProcessResourceEventRow.authorization_digest == authorization_digest)
                .order_by(ProcessResourceEventRow.sequence)
            )
        ).all()
        events = tuple([await self._event(session, row.record_digest) for row in rows])
        previous: ProcessResourceEvent | None = None
        for sequence, event in enumerate(events):
            if (
                event.sequence != sequence
                or event.previous_digest != (previous.digest if previous is not None else None)
                or event.grant_digest != current.grant_digest
            ):
                raise ValueError("resource journal chain is incomplete or substituted")
            held = previous.held if previous else ProcessResources()
            charged = previous.charged if previous else ProcessResources()
            slots = previous.open_reservations if previous else 0
            if event.kind == "reserve":
                held, slots = held.plus(event.amount), slots + 1
            elif event.kind in {"settle", "release"}:
                reservation, _, _ = await self.reservation(session, event.subject_id)
                held, slots = held.minus(reservation.amount), slots - 1
            if event.kind in {"settle", "initial"}:
                charged = charged.plus(event.amount)
            if (event.held, event.charged, event.open_reservations) != (held, charged, slots):
                raise ValueError("resource journal arithmetic does not conserve funding")
            if previous and previous.stopped and not event.stopped:
                raise ValueError("resource journal silently reopened a stopped account")
            previous = event
        if not events or events[-1] != current:
            raise ValueError("resource journal head is missing or rolled back")
        return events

    async def _authorization(
        self, session: AsyncSession, digest: str
    ) -> AmberAuthorizationEnvelope:
        row = await session.get(AmberAuthorizationRow, digest)
        if row is None:
            raise PermissionError("resource account has no Amber authorization")
        envelope = AmberAuthorizationEnvelope.model_validate(row.record_json, strict=False)
        if envelope.digest != digest or sha256_digest(row.record_json) != digest:
            raise ValueError("resource authorization is corrupt")
        return envelope

    async def _event(self, session: AsyncSession, digest: str) -> ProcessResourceEvent:
        row = await session.get(ProcessResourceEventRow, digest)
        if row is None:
            raise ValueError("resource journal record is missing")
        record = ProcessResourceEvent.model_validate(row.record_json, strict=False)
        if sha256_digest(row.record_json) != row.record_digest:
            raise ValueError("resource journal JSON is corrupt")
        if (record.digest, record.authorization_digest, record.sequence) != (
            row.record_digest,
            row.authorization_digest,
            row.sequence,
        ):
            raise ValueError("resource journal record is corrupt")
        return record

    @staticmethod
    def _next(current: ProcessResourceEvent, **changes: object) -> ProcessResourceEvent:
        values = current.model_dump()
        values.update(
            sequence=current.sequence + 1,
            previous_digest=current.digest,
            reservation_digest=None,
            source_digests=(),
            actor_id="padawan.pprl.resources",
            reason="conserved resource accounting",
        )
        values.update(changes)
        # Independent requests can acquire the account lock in a different order.
        # Journal time is a monotone lower bound; source request times remain unchanged.
        values["created_at"] = max(values["created_at"], current.created_at)
        return ProcessResourceEvent.model_validate(values)

    async def _append(self, session: AsyncSession, record: ProcessResourceEvent) -> None:
        session.add(
            ProcessResourceEventRow(
                record_digest=record.digest,
                authorization_digest=record.authorization_digest,
                sequence=record.sequence,
                record_json=record.model_dump(mode="json"),
            )
        )
        head = await session.get(ProcessResourceAccountRow, record.authorization_digest)
        if head is None:
            raise RuntimeError("resource journal lost its account head")
        head.sequence, head.record_digest = record.sequence, record.digest
        await session.flush()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
