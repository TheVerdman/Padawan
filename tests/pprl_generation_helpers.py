"""Explicit fake-provider composition for offline process-generation tests."""

from __future__ import annotations

from typing import cast

from padawan.adapters.base import GenerationRequest
from padawan.adapters.prepared import PreparedGenerationClient
from padawan.models.contracts import SamplingConfiguration, project_authored_internal_rights
from padawan.models.hashing import sha256_digest
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.generation_boundary import ProcessGenerationBoundary
from padawan.pprl.generation_contracts import ProcessGenerationPolicy
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ProcessStore
from tests.pprl_helpers import NOW, worker_model


def generation_boundary(
    process: ProcessStore,
    executor: IdempotentGenerationExecutor,
    *,
    instructions: str = "synthetic fixture",
) -> ProcessGenerationBoundary:
    sampling = SamplingConfiguration(max_output_tokens=12)
    client = cast(PreparedGenerationClient, executor.client)
    probe = client.prepare_generation(
        GenerationRequest(
            request_id="configuration-probe", instructions=instructions, input="", sampling=sampling
        )
    )
    return ProcessGenerationBoundary(
        observations=ProcessObservationStore(process),
        catalog=executor.catalog,
        client=client,
        policy=ProcessGenerationPolicy(
            policy_id="test.generation",
            version="1.0.0",
            role_id="researcher",
            worker_model_digest=sha256_digest(worker_model()),
            provider=probe.provider,
            transport_configuration_digest=probe.configuration_digest,
            instructions=instructions,
            sampling=sampling,
            reviewed_by="reviewer-a",
            reviewed_at=NOW,
            instructions_rights=project_authored_internal_rights(reviewed_at=NOW),
        ),
    )
