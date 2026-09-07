from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from padawan.adapters.base import ModelProviderError
from padawan.atlas.orchestration import (
    generation_request_for,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ExternalCallRow, RunRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from tests.support.atlas_activation import (
    call,
    fixture,
    response,
)


async def test_registered_wire_dispatches_once_and_replays_after_pause(database, tmp_path):
    calls = []
    executor, request, prepared, activation = await fixture(
        database, tmp_path, lambda r: (calls.append(r.content), response())[1]
    )
    first = await call(executor, request, prepared, activation)
    async with database.transaction() as session:
        run = await session.get(RunRow, "atlas-run")
        run.paused = True
        refs = (
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "external_call_prepared"
                )
            )
        ).all()
        assert len(refs) == 1
    second = await call(executor, request, prepared, activation)
    assert second.raw_response == first.raw_response
    assert calls == [prepared.body_json.encode()]
    assert first.output_text == "4"


@pytest.mark.parametrize("change", ["destination", "wire", "expiry", "unregistered", "activation"])
async def test_changed_or_unadmitted_effect_is_not_dispatched(database, tmp_path, change):
    calls = []
    executor, request, prepared, activation = await fixture(
        database, tmp_path, lambda r: (calls.append(r), response())[1]
    )
    if change == "destination":
        prepared = prepared.model_copy(
            update={"destination": "https://elsewhere.test/v1/responses"}
        )
    elif change == "wire":
        request = request.model_copy(update={"instructions": "different prompt"})
    elif change == "unregistered":
        request = request.model_copy(update={"request_id": "unregistered"})
    else:
        raw = json.loads(executor.artifacts.read_bytes(activation, allow_restricted=True))
        if change == "expiry":
            raw["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        else:
            raw["run_manifest_digest"] = sha256_digest("other run")
        activation = executor.artifacts.put_text(
            json.dumps(raw), media_type=activation.media_type, restricted=True, raw_data=True
        )
    with pytest.raises(PermissionError):
        await call(executor, request, prepared, activation)
    assert calls == []
    async with database.transaction() as session:
        assert (await session.scalars(select(ExternalCallRow))).all() == []


async def test_unknown_effect_cannot_be_retried(database, tmp_path):
    calls = []

    def failed(r):
        calls.append(r)
        raise httpx.ReadTimeout("offline timeout")

    executor, request, prepared, activation = await fixture(database, tmp_path, failed)
    with pytest.raises(ModelProviderError):
        await call(executor, request, prepared, activation)
    with pytest.raises(PermissionError, match="unresolved"):
        await call(executor, request, prepared, activation)
    assert len(calls) == 1


async def test_concurrent_duplicate_is_fenced_before_second_io(database, tmp_path):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(r):
        calls.append(r)
        started.set()
        await finish.wait()
        return response()

    executor, request, prepared, activation = await fixture(database, tmp_path, handler)
    first = asyncio.create_task(call(executor, request, prepared, activation))
    await asyncio.wait_for(started.wait(), timeout=10)
    try:
        with pytest.raises(PermissionError, match="unresolved Atlas"):
            await call(executor, request, prepared, activation)
    finally:
        finish.set()
        await first
    assert len(calls) == 1


async def test_database_reservations_bound_concurrency_across_executors(database, tmp_path):
    full, finish = asyncio.Event(), asyncio.Event()
    calls, details = [], {}

    async def handler(r):
        calls.append(r)
        if len(calls) == 2:
            full.set()
        await finish.wait()
        return response()

    executor, _, _, activation = await fixture(
        database, tmp_path, handler, trials=4, details=details
    )
    raw = json.loads(executor.artifacts.read_bytes(activation, allow_restricted=True))
    raw["max_in_flight"] = 2
    activation = executor.artifacts.put_text(
        json.dumps(raw), media_type=activation.media_type, restricted=True, raw_data=True
    )
    tasks = []
    for trial in details["plan"].requests:
        request = generation_request_for(
            request=trial,
            item=details["item"],
            configuration=details["configuration"],
            profile=details["profile"],
        )
        independent = IdempotentGenerationExecutor(
            database=database, artifacts=executor.artifacts, client=details["client"]
        )
        tasks.append(
            asyncio.create_task(
                call(
                    independent, request, details["client"].prepare_generation(request), activation
                )
            )
        )
    try:
        await asyncio.wait_for(full.wait(), timeout=10)
        pending = set(tasks)
        rejected = set()
        while len(rejected) < 2:
            done, pending = await asyncio.wait(
                pending, timeout=10, return_when=asyncio.FIRST_COMPLETED
            )
            assert done
            rejected.update(done)
    finally:
        finish.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(calls) == 2
    assert len(failures) == 2
    assert all(
        isinstance(error, PermissionError) and "concurrency" in str(error) for error in failures
    )
