"""Finite code-generation dispatch and official judging through native Atlas records."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from padawan.adapters.prepared import PreparedGenerationClient
from padawan.artifacts.store import (
    ArtifactCatalog,
    artifact_put_bytes,
    artifact_read_bytes,
)
from padawan.atlas.activation import AtlasActivation
from padawan.atlas.adapters import (
    AdapterReadinessContext,
    CodingAgenticAdapter,
    build_environment_readiness_evidence,
)
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.coding_judge import (
    DockerBatchJudge,
    JudgeInfrastructureError,
    JudgePackage,
    JudgeResult,
)
from padawan.atlas.contracts import AtlasItemManifest, AtlasTrialRequest, AtlasTrialResult, Modality
from padawan.atlas.orchestration import (
    FixedRunConfiguration,
    build_trial_result,
    generation_request_for,
)
from padawan.atlas.registry import AtlasRegistry
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import HarnessProfile, ResearchExecutionManifest
from padawan.models.tables import (
    ArtifactRow,
    AtlasTrialResultRow,
    ExternalCallRow,
    RunRow,
    VerifierResultRow,
)
from padawan.orchestration.external_calls import (
    IdempotentGenerationExecutor,
    _artifact_row_to_reference,
)
from padawan.rewards.engine import RewardEngine


def extract_cpp(output: str) -> str:
    """A declared final-channel parser; private reasoning is never searched for code."""
    blocks = re.findall(r"```(?:cpp|c\+\+|cc)?\s*\n(.*?)```", output, re.DOTALL)
    return (blocks[-1] if blocks else output).strip()


async def judge_with_deadline(
    *,
    semaphore: asyncio.Semaphore,
    judge: DockerBatchJudge,
    package: JudgePackage,
    source: str,
    timeout_seconds: float,
) -> JudgeResult:
    """The judge allowance includes waiting for a CPU slot, not just execution."""
    deadline = time.monotonic() + timeout_seconds
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout_seconds)
    except TimeoutError:
        raise JudgeInfrastructureError("judge slot was unavailable within its allowance") from None
    try:
        return await asyncio.to_thread(
            judge.judge, package=package, source=source, deadline=deadline
        )
    finally:
        semaphore.release()


async def run_coding_trials(
    *,
    executor: IdempotentGenerationExecutor,
    client: PreparedGenerationClient,
    activation_ref: ArtifactRef,
    requests: tuple[AtlasTrialRequest, ...],
    items: dict[str, AtlasItemManifest],
    profile: HarnessProfile,
    execution: ResearchExecutionManifest,
    configuration: FixedRunConfiguration,
    readiness: AdapterReadinessContext,
    packages: dict[str, JudgePackage],
    judge: DockerBatchJudge,
    judge_timeout_seconds: float = 900,
    max_judges: int = 2,
    shared_judge_semaphore: asyncio.Semaphore | None = None,
    compiler_policy: Any = None,
    tokenizer: Any = None,
    continuation_admission: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, str]:
    """Resume completed evidence; stop new dispatch on an unresolved effect or judge failure.

    Requests and allocations must already be registered. Every input ID remains in the returned
    ledger, including work not dispatched. There are no provider retries or hidden-test feedback.
    """
    if not 0 < judge_timeout_seconds <= 3600:
        raise ValueError("judge timeout must be finite and at most one hour")
    if not 1 <= max_judges <= 4:
        raise ValueError("local coding judging supports one to four CPU containers at once")
    if len({r.request_id for r in requests}) != len(requests):
        raise ValueError("duplicate trial IDs in finite dispatch list")
    if configuration.tools and (compiler_policy is None or tokenizer is None):
        raise ValueError("compiler trajectories require a pinned tool policy and tokenizer")
    activation = AtlasActivation.model_validate_json(
        await artifact_read_bytes(executor.artifacts, activation_ref, allow_restricted=True)
    )
    catalog = ArtifactCatalog(executor.artifacts)
    registry = AtlasRegistry(artifacts=AtlasArtifactBoundary(catalog))
    adapter = CodingAgenticAdapter()
    environment = build_environment_readiness_evidence(
        environment_fingerprint=execution.environment_fingerprint,
        verifier_id="livecodebench-pro.batch",
        verifier_version="padawan-1",
    )
    adapter.readiness(readiness, environment_evidence=environment).require_ready()
    # Validate the entire batch before the first model call.
    generations = {}
    for request in requests:
        item = items[request.item_digest]
        package = packages[request.item_digest]
        if (
            item.adapter_kind != adapter.descriptor.kind
            or item.verifier_payload.get("archive_sha256") != package.archive_digest
            or item.verifier_payload.get("case_count") != len(package.cases)
            or item.verifier_payload.get("judge_image") != judge.image
        ):
            raise ValueError("coding item differs from its exact judge package or image")
        if item.modalities != (Modality.TEXT,):
            raise ValueError("batch C++ runner accepts text statements only")
        generations[request.request_id] = generation_request_for(
            request=request, item=item, configuration=configuration, profile=profile
        )
    semaphore = asyncio.Semaphore(activation.max_in_flight)
    judge_semaphore = shared_judge_semaphore or asyncio.Semaphore(max_judges)
    stop = asyncio.Event()
    outcomes = {request.request_id: "not_run" for request in requests}

    async def trial(request: AtlasTrialRequest) -> None:
        async with semaphore:
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                async with executor.database.transaction() as session:
                    existing = await session.scalar(
                        select(AtlasTrialResultRow).where(
                            AtlasTrialResultRow.request_id == request.request_id
                        )
                    )
                    if existing is not None:
                        await registry.validate_trial_artifacts(
                            session, result_id=existing.result_id
                        )
                        outcomes[request.request_id] = existing.status
                        return
                # Retained completed evidence remains readable after admission expires.
                # Only new effects require the still-live activation window.
                if datetime.now(UTC) >= activation.expires_at:
                    return
                generation_request = generations[request.request_id]
                if configuration.tools:
                    from padawan.atlas.coding_tools import run_tool_coding_trial

                    outcomes[request.request_id] = await run_tool_coding_trial(
                        executor=executor,
                        client=client,
                        activation_ref=activation_ref,
                        request=request,
                        generation_request=generation_request,
                        item=items[request.item_digest],
                        package=packages[request.item_digest],
                        judge=judge,
                        policy=compiler_policy,
                        tokenizer=tokenizer,
                        judge_semaphore=judge_semaphore,
                        judge_timeout_seconds=judge_timeout_seconds,
                        admission_check=continuation_admission,
                    )
                    return
                generation = await executor.execute(
                    run_id=request.run_id,
                    purpose="capability_atlas",
                    provider=activation.provider,
                    request=generation_request,
                    prepared=client.prepare_generation(generation_request),
                    atlas_activation=activation_ref,
                )
                verifier_id = f"coding-verifier-{request.request_id}"
                async with executor.database.transaction() as session:
                    prior = await session.get(VerifierResultRow, verifier_id)
                    verifier = (
                        VerifierResult.model_validate_json(canonical_json_bytes(prior.record_json))
                        if prior is not None
                        else None
                    )
                if verifier is None:
                    source = extract_cpp(generation.output_text)
                    wire = json.loads(generation.raw_response)
                    evidence = {
                        "evaluated_output_digest": sha256_digest(generation.output_text),
                        "finish_reason": generation.finish_reason,
                        "incomplete_details": wire.get("incomplete_details"),
                    }
                    disposition = VerifierDisposition.REJECTED
                    summary = "No final C++ candidate was submitted within the declared allowance."
                    provider_failed = generation.protocol == "responses" and (
                        wire.get("error") is not None
                        or wire.get("status") not in {"completed", "incomplete"}
                    )
                    if provider_failed:
                        stop.set()
                        disposition = VerifierDisposition.INFRASTRUCTURE_FAILURE
                        summary = "Provider reported a failed response; no model score assigned."
                    elif source:
                        try:
                            result = await judge_with_deadline(
                                semaphore=judge_semaphore,
                                judge=judge,
                                package=packages[request.item_digest],
                                source=source,
                                timeout_seconds=judge_timeout_seconds,
                            )
                            evidence.update(asdict(result))
                            disposition = (
                                VerifierDisposition.VERIFIED
                                if result.success
                                else VerifierDisposition.REJECTED
                            )
                            summary = result.verdict
                        except JudgeInfrastructureError as error:
                            stop.set()
                            disposition = VerifierDisposition.INFRASTRUCTURE_FAILURE
                            evidence["infrastructure_error_type"] = type(error).__name__
                            summary = (
                                "Official judging failed; no model success or failure assigned."
                            )
                    verifier = VerifierResult(
                        result_id=verifier_id,
                        verifier_id=environment.verifier_id,
                        verifier_version=environment.verifier_version,
                        scope=request.request_id,
                        disposition=disposition,
                        deterministic=True,
                        summary=summary,
                        evidence=evidence,
                        created_at=datetime.now(UTC),
                    )
                    async with executor.database.transaction() as session:
                        await session.execute(
                            update(RunRow)
                            .where(RunRow.run_id == request.run_id)
                            .values(paused=RunRow.paused)
                        )
                        await RewardEngine().record_verifier_result(session, verifier)
                evaluation = adapter.evaluate(
                    verifier_result=verifier,
                    environment_evidence=environment,
                    context=readiness,
                )
                response_ref = await artifact_put_bytes(
                    executor.artifacts,
                    generation.raw_response,
                    media_type="application/json",
                    restricted=True,
                    raw_data=True,
                )
                async with executor.database.transaction() as session:
                    await session.execute(
                        update(RunRow)
                        .where(RunRow.run_id == request.run_id)
                        .values(paused=RunRow.paused)
                    )
                    call = await session.get(ExternalCallRow, request.request_id)
                    assert call is not None and call.response_artifact_id is not None
                    envelope = await session.get(ArtifactRow, call.response_artifact_id)
                    assert envelope is not None
                    await catalog.register(session, response_ref)
                    final: AtlasTrialResult = build_trial_result(
                        request=request,
                        generation=generation,
                        execution=execution,
                        generation_request=generation_request,
                        response_artifact=response_ref,
                        external_call_artifact=_artifact_row_to_reference(envelope),
                        evaluation=evaluation,
                        # These are runtime information checks, not a training-exposure claim.
                        contamination_checks={"terminal_hidden_judge_only": True},
                        completed_at=datetime.now(UTC),
                        wall_time_ms=(time.monotonic() - started) * 1000,
                    )
                    await registry.record_trial_result(session, final)
                    outcomes[request.request_id] = final.status.value
            except asyncio.CancelledError:
                stop.set()
                raise
            except Exception as error:
                stop.set()
                # Raw provider error bodies are retained by the native executor, never echoed.
                outcomes[request.request_id] = (
                    "judge_infrastructure_failure"
                    if isinstance(error, JudgeInfrastructureError)
                    else f"unresolved:{type(error).__name__}"
                )

    await asyncio.gather(*(trial(request) for request in requests))
    receipt = await artifact_put_bytes(
        executor.artifacts,
        canonical_json_bytes(
            {"activation": activation_ref, "outcomes": outcomes, "completed_at": datetime.now(UTC)}
        ),
        media_type="application/vnd.padawan.atlas-dispatch-status+json",
        restricted=True,
        raw_data=True,
    )
    async with executor.database.transaction() as session:
        # A receipt's catalog lookup must not begin a deferred SQLite read transaction
        # that races another trial's writer before inserting this final artifact.
        await session.execute(
            update(RunRow)
            .where(RunRow.run_id.in_({request.run_id for request in requests}))
            .values(paused=RunRow.paused)
        )
        await catalog.register(session, receipt)
        await catalog.reference(
            session,
            receipt,
            owner_type="atlas_dispatch_status",
            owner_id=activation.run_manifest_digest,
        )
    return outcomes
