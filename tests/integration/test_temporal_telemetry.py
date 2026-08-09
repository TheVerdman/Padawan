from __future__ import annotations

from datetime import UTC, datetime, timedelta

from padawan.models.hashing import sha256_digest
from padawan.temporal import OperationStatus, VirtualClock
from padawan.temporal.telemetry import OperationTelemetryStore

NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
FINGERPRINT = sha256_digest("temporal-test-environment")


async def test_operation_spans_build_reproducible_duration_profiles(database) -> None:
    clock = VirtualClock(NOW)
    telemetry = OperationTelemetryStore(clock)
    durations = (10, 20, 30)
    for index, seconds in enumerate(durations):
        operation_id = f"operation-success-{index}"
        async with database.transaction() as session:
            await telemetry.create(
                session,
                operation_id=operation_id,
                operation_type="test_suite",
                environment_fingerprint=FINGERPRINT,
                workload_class="small",
                workload={"tests": 100},
                status=OperationStatus.RUNNING,
            )
        clock.advance(timedelta(seconds=seconds / 2))
        async with database.transaction() as session:
            await telemetry.transition(
                session,
                operation_id=operation_id,
                status=OperationStatus.RUNNING,
                progress=0.5,
            )
        clock.advance(timedelta(seconds=seconds / 2))
        async with database.transaction() as session:
            completed = await telemetry.transition(
                session,
                operation_id=operation_id,
                status=OperationStatus.SUCCEEDED,
            )
        assert completed.wall_seconds == seconds

    async with database.transaction() as session:
        await telemetry.create(
            session,
            operation_id="operation-timeout",
            operation_type="test_suite",
            environment_fingerprint=FINGERPRINT,
            workload_class="small",
            workload={"tests": 100},
            status=OperationStatus.RUNNING,
        )
    clock.advance(timedelta(seconds=60))
    async with database.transaction() as session:
        await telemetry.transition(
            session,
            operation_id="operation-timeout",
            status=OperationStatus.TIMED_OUT,
        )
        profile = await telemetry.build_duration_profile(
            session,
            operation_type="test_suite",
            environment_fingerprint=FINGERPRINT,
            workload_class="small",
        )

    assert profile.sample_count == 4
    assert profile.success_count == 3
    assert profile.timeout_count == 1
    assert profile.timeout_probability == 0.25
    assert profile.p50_seconds == 20
    assert profile.p90_seconds == 30
    assert profile.p95_seconds == 30

    async with database.transaction() as session:
        rebuilt = await telemetry.build_duration_profile(
            session,
            operation_type="test_suite",
            environment_fingerprint=FINGERPRINT,
            workload_class="small",
            as_of=profile.as_of,
        )
        latest = await telemetry.latest_duration_profile(
            session,
            operation_type="test_suite",
            environment_fingerprint=FINGERPRINT,
            workload_class="small",
        )

    assert rebuilt == profile
    assert latest == profile


async def test_active_operation_query_excludes_terminal_spans(database) -> None:
    telemetry = OperationTelemetryStore(VirtualClock(NOW))
    async with database.transaction() as session:
        await telemetry.create(
            session,
            operation_id="operation-active",
            operation_type="deployment",
            environment_fingerprint=FINGERPRINT,
            workload_class="default",
            workload={},
            status=OperationStatus.RUNNING,
        )
        await telemetry.create(
            session,
            operation_id="operation-complete",
            operation_type="deployment",
            environment_fingerprint=FINGERPRINT,
            workload_class="default",
            workload={},
            status=OperationStatus.RUNNING,
        )
        await telemetry.transition(
            session,
            operation_id="operation-complete",
            status=OperationStatus.SUCCEEDED,
        )
        active = await telemetry.active(session)

    assert tuple(item.operation_id for item in active) == ("operation-active",)
