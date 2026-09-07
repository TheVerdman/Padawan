"""Shared atlas activation fixture setup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

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
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from tests.support.atlas_registry import (
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
