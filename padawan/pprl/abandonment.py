"""Reviewed terminal cancellation without refund, worker impersonation, or execution.

The broker, SQL/artifact stores and original envelope's named reviewers are trusted.
Historical logical reads serve private replay/compiler use; physical reads additionally
reconstruct native recovery sources through the independently composed recovery reader.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ProcessAbandonmentRow,
    ProcessRecoveryRow,
    ProcessRolloutRow,
)
from padawan.pprl.abandonment_contracts import (
    ProcessAbandonmentReceipt,
    ProcessAbandonmentRequest,
)
from padawan.pprl.contracts import RolloutStatus
from padawan.pprl.recovery_contracts import ProcessRecoveryReceipt
from padawan.pprl.resources import atomic_resource_write, unresolved_action_query

if TYPE_CHECKING:
    from padawan.pprl.recovery import ProcessRecoveryStore


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def read_abandonment(
    session: AsyncSession, *, abandonment_id: str
) -> tuple[ProcessAbandonmentReceipt, ProcessRecoveryReceipt]:
    """Check durable logical lineage and pins; no repair, I/O dispatch, or authority grant."""
    from padawan.pprl.store import ProcessStore

    row = await session.get(ProcessAbandonmentRow, abandonment_id)
    if row is None:
        raise ValueError("terminal abandonment receipt is missing")
    receipt = ProcessAbandonmentReceipt.model_validate(row.record_json, strict=False)
    request = receipt.request
    if sha256_digest(row.record_json) != row.record_digest or (
        receipt.digest,
        request.abandonment_id,
        request.rollout_id,
        request.recovery_id,
        receipt.request_digest,
        receipt.created_at,
    ) != (
        row.record_digest,
        row.abandonment_id,
        row.rollout_id,
        row.recovery_id,
        row.request_digest,
        _utc(row.created_at),
    ):
        raise ValueError("terminal abandonment receipt is corrupt")
    recovery_row = await session.get(ProcessRecoveryRow, request.recovery_id)
    if recovery_row is None:
        raise ValueError("abandonment lost its recovery")
    recovery = ProcessRecoveryReceipt.model_validate(recovery_row.record_json, strict=False)
    if (
        sha256_digest(recovery_row.record_json) != recovery_row.record_digest
        or recovery.digest != request.recovery_digest
        or recovery.digest != recovery_row.record_digest
        or recovery.request.recovery_id != recovery_row.recovery_id
        or recovery.request_digest != recovery_row.request_digest
        or recovery.created_at != _utc(recovery_row.created_at)
        or recovery.request.rollout_id != request.rollout_id
        or recovery_row.rollout_id != request.rollout_id
        or recovery.execution_digest != receipt.execution_digest
        or recovery_row.execution_digest != receipt.execution_digest
        or recovery.authorization_digest != receipt.authorization_digest
        or recovery.state_id != receipt.state_id
        or recovery.state_digest != request.expected_state_digest
        or recovery.disposition != "review_required"
        or recovery.created_at > request.reviewed_at
        or request.exclusion
        != (
            "unresolved_external_effect"
            if any(effect.disposition == "unknown" for effect in recovery.effects)
            else "infrastructure_interruption"
        )
    ):
        raise ValueError("abandonment differs from its exact recovery assessment")
    processes = ProcessStore()
    state = await processes.get_state(session, state_id=receipt.state_id)
    head = await session.get(ProcessRolloutRow, request.rollout_id)
    if (
        head is None
        or head.terminal_abandonment_id != abandonment_id
        or head.status != RolloutStatus.CANCELLED.value
        or not head.paused
        or any(
            value is not None
            for value in (head.lease_token, head.lease_owner, head.lease_expires_at)
        )
        or head.current_state_id != receipt.state_id
        or head.sequence != receipt.state_sequence
        or head.execution_digest != receipt.execution_digest
        or head.authorization_digest != receipt.authorization_digest
        or _utc(head.updated_at) != receipt.created_at
        or state.state_digest != request.expected_state_digest
        or state.rollout_id != request.rollout_id
        or state.sequence != receipt.state_sequence
    ):
        raise ValueError("abandonment lost its exact terminal head")
    from padawan.pprl.tasks import ProcessTaskStore

    await ProcessTaskStore().check_rollout(session, head)
    _, _, envelope = await processes.workers._context(
        session, receipt.execution_digest, now=receipt.created_at, active=False
    )
    history = await processes.amber.history(
        session, authorization_digest=receipt.authorization_digest
    )
    sequence = request.expected_authorization_sequence
    if (
        envelope.digest != receipt.authorization_digest
        or request.reviewer_id not in envelope.required_reviewers
        or sequence >= len(history)
        or any(
            e.sequence != i or (i and e.from_status != history[i - 1].to_status)
            for i, e in enumerate(history)
        )
        or sha256_digest(history[sequence]) != receipt.authorization_event_digest
        or history[sequence].created_at > request.reviewed_at
        or (sequence + 1 < len(history) and history[sequence + 1].created_at <= receipt.created_at)
    ):
        raise ValueError("abandonment lost its original reviewed authority")
    account = await processes.amber.resources._event(session, request.expected_account_digest)
    if (
        account.authorization_digest != receipt.authorization_digest
        or account.created_at > request.reviewed_at
    ):
        raise ValueError("abandonment lost its original resource snapshot")
    journal = await processes.amber.resources.replay(session, receipt.authorization_digest)
    original_account = [event for event in journal if event.created_at <= receipt.created_at]
    if not original_account or original_account[-1].digest != request.expected_account_digest:
        raise ValueError("abandonment substitutes its original resource frontier")
    pending = set(
        await session.scalars(
            unresolved_action_query().where(
                AmberAdmissionDecisionRow.rollout_id == request.rollout_id
            )
        )
    )
    if not pending or pending != {
        effect.decision_id
        for effect in recovery.effects
        if effect.disposition in {"unknown", "completed_unadmitted"}
    }:
        raise ValueError("abandonment omits or substitutes an unresolved effect")
    expected = {s.reference.artifact.artifact_id for e in recovery.effects for s in e.sources}
    for owner_type, owner_id in (
        ("process_recovery_receipt", request.recovery_id),
        ("process_abandonment", abandonment_id),
    ):
        actual = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id)
                .where(
                    ArtifactReferenceRow.owner_type == owner_type,
                    ArtifactReferenceRow.owner_id == owner_id,
                )
                .limit(len(expected) + 1)
            )
        )
        if actual != expected:
            raise ValueError("abandonment lost independent source ownership")
    return receipt, recovery


class ProcessAbandonmentStore:
    def __init__(self, recovery: ProcessRecoveryStore) -> None:
        self.recovery = recovery

    async def read(
        self, session: AsyncSession, *, abandonment_id: str
    ) -> ProcessAbandonmentReceipt:
        receipt, expected = await read_abandonment(session, abandonment_id=abandonment_id)
        checked = await self.recovery.read(session, recovery_id=receipt.request.recovery_id)
        if checked != expected:
            raise ValueError("abandonment recovery source changed during read")
        return receipt

    @atomic_resource_write
    async def abandon(
        self, session: AsyncSession, request: ProcessAbandonmentRequest, *, now: datetime
    ) -> ProcessAbandonmentReceipt:
        request = ProcessAbandonmentRequest.model_validate_json(request.model_dump_json())
        if now.tzinfo is None:
            raise ValueError("abandonment requires an aware time")
        row = await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == request.rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise PermissionError("abandonment rollout is missing")
        existing = await session.get(ProcessAbandonmentRow, request.abandonment_id)
        if existing is not None:
            receipt = await self.read(session, abandonment_id=request.abandonment_id)
            if receipt.request != request:
                raise PermissionError("abandonment id already binds another review")
            return receipt  # historical retry grants no fresh authority, even after expiry
        if (
            row.terminal_abandonment_id is not None
            or row.status != RolloutStatus.REVIEW_REQUIRED.value
            or not row.paused
            or any(
                value is not None
                for value in (row.lease_owner, row.lease_token, row.lease_expires_at)
            )
            or not request.reviewed_at <= now < request.expires_at
            or request.reviewed_at < _utc(row.updated_at)
        ):
            raise PermissionError(
                "abandonment requires a fresh review of a fenced unresolved rollout"
            )
        _, _, envelope = await self.recovery.workers._context(
            session, row.execution_digest, now=now, active=False
        )
        authority = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == row.authorization_digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        history = await self.recovery._authority_history(session, row.authorization_digest)
        if (
            authority is None
            or authority.sequence != request.expected_authorization_sequence
            or history[-1].sequence != authority.sequence
            or history[-1].to_status.value != authority.status
            or history[-1].created_at != _utc(authority.updated_at)
            or history[-1].created_at > request.reviewed_at
            or request.reviewer_id not in envelope.required_reviewers
            or envelope.digest != row.authorization_digest
        ):
            raise PermissionError("abandonment review differs from current authority")
        account = await self.recovery.resources.inspect(
            session, row.authorization_digest, lock=True
        )
        if account.digest != request.expected_account_digest:
            raise PermissionError("abandonment resource snapshot is stale")
        recovery_row = await session.get(ProcessRecoveryRow, request.recovery_id)
        if (
            recovery_row is None
            or recovery_row.rollout_id != request.rollout_id
            or recovery_row.record_digest != request.recovery_digest
        ):
            raise PermissionError("abandonment names a different recovery source")
        original = await self.recovery.read(session, recovery_id=request.recovery_id)
        state = await self.recovery.processes.get_state(session, state_id=row.current_state_id)
        # The checked reader below verifies every immutable join, disposition and pin.
        timestamp = max(now, datetime.now(UTC))
        receipt = ProcessAbandonmentReceipt(
            request=request,
            request_digest=request.digest,
            execution_digest=row.execution_digest,
            authorization_digest=row.authorization_digest,
            authorization_event_digest=sha256_digest(history[-1]),
            state_id=state.state_id,
            state_sequence=state.sequence,
            created_at=timestamp,
        )
        for effect in original.effects:
            for source in effect.sources:
                await self.recovery.catalog.reference(
                    session,
                    source.reference.artifact,
                    owner_type="process_abandonment",
                    owner_id=request.abandonment_id,
                )
        session.add(
            ProcessAbandonmentRow(
                abandonment_id=request.abandonment_id,
                rollout_id=request.rollout_id,
                recovery_id=request.recovery_id,
                request_digest=request.digest,
                record_digest=receipt.digest,
                record_json=receipt.model_dump(mode="json"),
                created_at=timestamp,
            )
        )
        row.terminal_abandonment_id = request.abandonment_id
        row.status, row.paused, row.updated_at = RolloutStatus.CANCELLED.value, True, timestamp
        await session.flush()
        result = await self.read(session, abandonment_id=request.abandonment_id)
        if datetime.now(UTC) >= request.expires_at:
            raise PermissionError("abandonment review expired during source validation")
        return result
