from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from padawan.adapters.base import ModelProviderError
from padawan.adapters.openai_compatible import OpenAICompatibleClient
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.atlas.activation import AtlasActivation
from padawan.atlas.adapters import AlgebraAdapter, CodingAgenticAdapter
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.coding_manifests import coding_suite
from padawan.atlas.orchestration import (
    FixedRunConfiguration,
    generation_request_for,
    plan_fixed_suite_run,
)
from padawan.atlas.registry import AtlasRegistry
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ExternalCallRow, RunRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from tests.integration.test_atlas_registry import (
    NOW,
    _binding,
    _campaign,
    _gate,
    _governance,
    _item,
    _ontology,
    _run_row,
    _seed_controls,
    _seed_preflight_artifacts,
    _suite,
)


async def fixture(database, tmp_path, handler, *, coding=None, details=None, trials=1):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    registry = AtlasRegistry(artifacts=AtlasArtifactBoundary(ArtifactCatalog(artifacts)))
    async with database.transaction() as session:
        profile, execution = await _seed_controls(session)
        governance, item, ontology = _governance(), coding or _item(), _ontology()
        suite = (
            _suite(governance, item)
            if coding is None
            else coding_suite(
                items=(item,),
                governance=governance,
                task_digest=execution.task.task_manifest_digest,
                corpus_digest=execution.task.corpus_digest,
                environment_fingerprint=execution.environment_fingerprint,
                text_gate=_gate(),
                created_at=NOW,
            )
        )
        await registry.register_dataset_governance(session, governance)
        await registry.register_suite(session, suite)
        await registry.register_ontology(session, ontology)
        campaign = _campaign(
            suite.content_digest,
            ontology.manifest_digest,
            execution_digest=sha256_digest(execution),
            trials_per_item=trials,
        )
        if trials > 1:
            campaign = campaign.model_copy(
                update={
                    "conditions": (
                        campaign.conditions[0].model_copy(
                            update={"max_input_tokens": 4096 * trials}
                        ),
                    )
                }
            )
            identity = campaign.model_dump(
                mode="json", exclude={"manifest_digest", "status", "created_at"}
            )
            campaign = type(campaign).model_validate_json(
                json.dumps(
                    {**campaign.model_dump(mode="json"), "manifest_digest": sha256_digest(identity)}
                )
            )
        await registry.register_campaign(session, campaign)
        binding = _binding(campaign, suite, profile, execution)
        await registry.register_execution_binding(session, binding)
        configuration = FixedRunConfiguration(
            instructions="Return only the requested answer.",
            max_output_tokens_per_request=128,
            action_budget_per_request=1,
            max_cost_usd_per_request=0,
            temperature=0,
            base_seed=17,
            edge_preflight_evidence_digest=sha256_digest("registry edge preflight"),
        )
        plan = plan_fixed_suite_run(
            campaign=campaign,
            condition=campaign.conditions[0],
            suite=suite,
            binding=binding,
            execution=execution,
            profile=profile,
            run_id="atlas-run",
            adapter=(
                AlgebraAdapter.descriptor if coding is None else CodingAgenticAdapter.descriptor
            ),
            configuration=configuration,
            created_at=NOW + timedelta(seconds=1),
        )
        trial = plan.requests[0]
        await _seed_preflight_artifacts(
            session, execution=execution, request=trial, artifacts=artifacts
        )
        session.add(_run_row(plan.manifest.run_id, execution))
        await session.flush()
        await registry.register_run_manifest(session, plan.manifest)
        for allocation, planned in zip(plan.allocations, plan.requests, strict=True):
            await registry.record_allocation(session, allocation)
            await registry.record_trial_request(session, planned)
    request = generation_request_for(
        request=trial, item=item, configuration=configuration, profile=profile
    )
    client = OpenAICompatibleClient(
        base_url="https://offline.test",
        model="student-model",
        provider="test-provider",
        protocol="responses",
        retry_attempts=1,
        allow_legacy_fallback=False,
        capture_private_reasoning=True,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    prepared = client.prepare_generation(request)
    activation = AtlasActivation(
        run_manifest_digest=plan.manifest.manifest_digest,
        authorization_ref="local-offline-fixture",
        provider=prepared.provider,
        destination=prepared.destination,
        configuration_digest=prepared.configuration_digest,
        max_in_flight=1,
        starts_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    activation_ref = artifacts.put_text(
        activation.model_dump_json(),
        media_type="application/vnd.padawan.atlas-activation+json",
        restricted=True,
        raw_data=True,
    )
    executor = IdempotentGenerationExecutor(database=database, artifacts=artifacts, client=client)
    if details is not None:
        details.update(
            plan=plan,
            profile=profile,
            execution=execution,
            configuration=configuration,
            suite=suite,
            item=item,
            client=client,
        )
    return executor, request, prepared, activation_ref


def response():
    return httpx.Response(
        200,
        json={
            "id": "captured-response",
            "model": "student-model",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "4"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        },
    )


async def call(executor, request, prepared, activation):
    return await executor.execute(
        run_id="atlas-run",
        purpose="capability_atlas",
        provider="test-provider",
        request=request,
        prepared=prepared,
        atlas_activation=activation,
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
