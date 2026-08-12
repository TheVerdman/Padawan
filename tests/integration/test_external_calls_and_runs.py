from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.store import LocalArtifactStore
from padawan.models.contracts import ResearchRole, RunState, SamplingConfiguration
from padawan.models.tables import ExternalCallRow, RunRow, RunTransitionRow, WorkerRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import InvalidTransitionError, RunStore
from tests.helpers import CallbackGenerationClient


async def test_response_persistence_prevents_duplicate_generation_after_crash(
    database, tmp_path
) -> None:
    runs = RunStore()
    async with database.transaction() as session:
        run_id = await runs.create(session, payload={"student_id": "student"})
    client = CallbackGenerationClient(lambda _request: '{"ok":true}', "provider")
    executor = IdempotentGenerationExecutor(
        database=database,
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        client=client,
    )
    request = GenerationRequest(
        request_id="external-request",
        instructions="respond",
        input="input",
        sampling=SamplingConfiguration(max_output_tokens=20),
        store=False,
    )
    first = await executor.execute(
        run_id=run_id, purpose="student", provider="provider", request=request
    )
    # Simulate a crash: no run transition is written after the response commit.
    second = await executor.execute(
        run_id=run_id, purpose="student", provider="provider", request=request
    )
    assert len(client.calls) == 1
    assert second.raw_response == first.raw_response
    assert second.raw_request == first.raw_request
    async with database.transaction() as session:
        call = await session.get(ExternalCallRow, request.request_id)
        assert call is not None and call.status == "completed"
        assert call.result_model_id == first.model_id
        assert call.result_protocol == first.protocol
        assert call.result_raw_request_digest is not None
        assert call.result_raw_response_digest is not None
        assert call.result_output_text_digest is not None
        assert call.result_usage == first.usage
        assert call.result_capabilities_digest is not None
        assert call.result_latency_ms == first.latency_ms
        transitions = (
            await session.scalars(select(RunTransitionRow).where(RunTransitionRow.run_id == run_id))
        ).all()
        assert transitions == []


async def test_generic_executor_cannot_bypass_atlas_activation_gateway(database, tmp_path) -> None:
    client = CallbackGenerationClient(lambda _request: '{"ok":true}', "provider")
    executor = IdempotentGenerationExecutor(
        database=database,
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        client=client,
    )
    request = GenerationRequest(
        request_id="atlas-bypass",
        instructions="respond",
        input="input",
        sampling=SamplingConfiguration(max_output_tokens=20),
        store=False,
    )
    with pytest.raises(PermissionError, match="activation gateway"):
        await executor.execute(
            run_id="not-dispatched",
            purpose="capability_atlas",
            provider="provider",
            request=request,
        )
    assert client.calls == []


async def test_run_transition_lease_release_pause_and_stale_recovery(database) -> None:
    runs = RunStore()
    async with database.transaction() as session:
        run_id = await runs.create(session, payload={})
        claimed = await runs.claim_next(
            session, worker_id="worker-one", lease_for=timedelta(minutes=5)
        )
        assert claimed is not None
        await runs.transition(
            session,
            run_id=run_id,
            lease_token=claimed.lease_token,
            actor="worker-one",
            to_state=RunState.ITEMS_LEASED,
        )
    async with database.transaction() as session:
        claimed_again = await runs.claim_next(
            session, worker_id="worker-two", lease_for=timedelta(minutes=5)
        )
        assert claimed_again is not None and claimed_again.run_id == run_id
        await runs.pause(session, run_id=run_id)
    async with database.transaction() as session:
        assert (
            await runs.claim_next(
                session,
                worker_id="worker-three",
                lease_for=timedelta(minutes=5),
                now=datetime.now(UTC) + timedelta(minutes=10),
            )
            is None
        )
        await runs.resume(session, run_id=run_id)
        worker = WorkerRow(
            worker_id="stale",
            status="active",
            current_run_id=run_id,
            capabilities={},
            started_at=datetime.now(UTC) - timedelta(hours=1),
            heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(worker)
        row = await session.get(RunRow, run_id)
        assert row is not None
        row.lease_owner = "stale"
        row.lease_token = "stale-token"
        row.lease_expires_at = datetime.now(UTC) + timedelta(hours=1)
    async with database.transaction() as session:
        recovered = await runs.recover_stale_workers(
            session, stale_before=datetime.now(UTC) - timedelta(minutes=5)
        )
        assert recovered == ("stale",)
        row = await session.get(RunRow, run_id)
        assert row is not None and row.lease_token is None


async def test_illegal_state_transition_is_rejected(database) -> None:
    runs = RunStore()
    async with database.transaction() as session:
        run_id = await runs.create(session, payload={})
        claimed = await runs.claim_next(session, worker_id="worker", lease_for=timedelta(minutes=1))
        assert claimed is not None
        with pytest.raises(InvalidTransitionError):
            await runs.transition(
                session,
                run_id=run_id,
                lease_token=claimed.lease_token,
                actor="worker",
                to_state=RunState.COLD_GRADED,
            )


async def test_one_active_run_per_student_and_terminal_release(database) -> None:
    runs = RunStore()
    payload = {"student_id": "continuous-student"}
    async with database.transaction() as session:
        first = await runs.create(session, payload=payload)
        duplicate = await runs.create(session, payload=payload)
        assert duplicate == first
        claimed = await runs.claim_next(session, worker_id="worker", lease_for=timedelta(minutes=1))
        assert claimed is not None
        await runs.transition(
            session,
            run_id=first,
            lease_token=claimed.lease_token,
            actor="worker",
            to_state=RunState.FAILED_TERMINAL,
        )

    async with database.transaction() as session:
        second = await runs.create(session, payload=payload)
        assert second != first


async def test_run_role_is_immutable_and_active_identity_cannot_cross_roles(database) -> None:
    runs = RunStore()
    async with database.transaction() as session:
        run_id = await runs.create(
            session,
            payload={"student_id": "role-bound", "research_role": "target"},
        )
        with pytest.raises(ValueError, match="different research role"):
            await runs.create(
                session,
                payload={"student_id": "role-bound", "research_role": "baseline"},
            )
        claimed = await runs.claim_next(
            session,
            worker_id="worker",
            lease_for=timedelta(minutes=1),
        )
        assert claimed is not None
        assert claimed.run_id == run_id
        assert claimed.research_role == ResearchRole.TARGET
        with pytest.raises(InvalidTransitionError, match="cannot change research role"):
            await runs.transition(
                session,
                run_id=run_id,
                lease_token=claimed.lease_token,
                actor="worker",
                to_state=RunState.ITEMS_LEASED,
                payload_updates={"research_role": "baseline"},
            )
