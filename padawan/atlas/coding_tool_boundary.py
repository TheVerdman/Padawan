"""Native intent admission for finite compiler trajectories under an Atlas activation.

Every model turn retains its own exact prepared HTTP effect. The root request and
immutable tool policy own all turns; a completed tool response authorizes only its
deterministically reconstructed public continuation. Unknown effects never retry.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, artifact_put_bytes, artifact_read_bytes
from padawan.atlas.coding_tool_contracts import (
    TOOL_ID,
    CodingToolTrajectory,
    CompilerReceipt,
    compiler_wire_identity,
    next_tool_request,
    public_response_items,
)
from padawan.atlas.contracts import AtlasTrialRequest
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import HarnessProfile
from padawan.models.tables import (
    ArtifactReferenceRow,
    ArtifactRow,
    AtlasTrialRequestRow,
    ExternalCallRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    RunRow,
)
from padawan.orchestration.external_calls import _artifact_row_to_reference, _deserialize_result

TRAJECTORY_MEDIA = "application/vnd.padawan.atlas-coding-tool-trajectory+json"
RECEIPT_MEDIA = "application/vnd.padawan.atlas-compiler-receipt+json"


async def owned_reference(
    session: AsyncSession, catalog: ArtifactCatalog, *, owner_type: str, owner_id: str
) -> ArtifactRef | None:
    rows = (
        await session.scalars(
            select(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == owner_id,
            )
        )
    ).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise PermissionError("compiler evidence owner has inconsistent references")
    row = await session.get(ArtifactRow, rows[0].artifact_id)
    if row is None:
        raise PermissionError("compiler evidence artifact is missing")
    reference = _artifact_row_to_reference(row)
    classification = await ArtifactInformationStore(catalog).get(
        session, artifact_id=reference.artifact_id
    )
    if (
        classification.artifact != reference
        or classification.information_class != InformationClass.FORENSIC
    ):
        raise PermissionError("compiler evidence lost its protected classification")
    await artifact_read_bytes(catalog.backend, reference, allow_restricted=True)
    return reference


async def retain_owned(
    session: AsyncSession,
    catalog: ArtifactCatalog,
    *,
    reference: ArtifactRef,
    owner_type: str,
    owner_id: str,
) -> None:
    # Caller acquires a writer/row lock before any catalog read.
    existing = await owned_reference(session, catalog, owner_type=owner_type, owner_id=owner_id)
    if existing is not None:
        if existing != reference:
            raise PermissionError("compiler evidence is immutable for its original owner")
        return
    await ArtifactInformationStore(catalog).classify(
        session,
        artifact=reference,
        information_class=InformationClass.FORENSIC,
        classified_by="padawan.atlas.coding-tools/v1",
        reason="Private compiler trajectory evidence; no process or training admission.",
        classified_at=datetime.now(UTC),
    )
    await catalog.reference(session, reference, owner_type=owner_type, owner_id=owner_id)


async def register_trajectory(
    session: AsyncSession, catalog: ArtifactCatalog, trajectory: CodingToolTrajectory
) -> ArtifactRef:
    await session.execute(
        update(RunRow).where(RunRow.run_id == trajectory.run_id).values(paused=RunRow.paused)
    )
    reference = await artifact_put_bytes(
        catalog.backend,
        canonical_json_bytes(trajectory),
        media_type=TRAJECTORY_MEDIA,
        restricted=True,
        raw_data=True,
    )
    await retain_owned(
        session,
        catalog,
        reference=reference,
        owner_type="atlas_coding_tool_trajectory",
        owner_id=trajectory.trajectory_id,
    )
    return reference


async def prior_turns(
    session: AsyncSession,
    catalog: ArtifactCatalog,
    trajectory: CodingToolTrajectory,
    count: int,
) -> tuple[list[tuple[GenerationResult, tuple[CompilerReceipt, ...]]], list[ArtifactRef]]:
    previous: list[tuple[GenerationResult, tuple[CompilerReceipt, ...]]] = []
    references: list[ArtifactRef] = []
    for index in range(count):
        rid = trajectory.trajectory_id if index == 0 else f"{trajectory.trajectory_id}.turn-{index}"
        call = await session.get(ExternalCallRow, rid)
        if (
            call is None
            or call.status != "completed"
            or call.run_id != trajectory.run_id
            or call.purpose != "capability_atlas"
        ):
            raise PermissionError("compiler continuation requires a completed owned model effect")
        row = await session.get(ArtifactRow, call.response_artifact_id)
        if row is None:
            raise PermissionError("compiler continuation lost its native generation envelope")
        reference = _artifact_row_to_reference(row)
        result = _deserialize_result(
            await artifact_read_bytes(catalog.backend, reference, allow_restricted=True)
        )
        if (
            reference.digest != call.result_envelope_digest
            or result.request_id != rid
            or result.provider != call.provider
            or result.model_id != call.result_model_id
            or result.protocol != call.result_protocol
            or sha256_digest(result.raw_request) != call.result_raw_request_digest
            or sha256_digest(result.raw_response) != call.result_raw_response_digest
            or result.usage != call.result_usage
        ):
            raise PermissionError("compiler continuation source differs from native evidence")
        references.append(reference)
        _, calls = public_response_items(result)
        receipts = []
        for number, tool_call in enumerate(calls):
            intent_ref = await owned_reference(
                session, catalog, owner_type="atlas_compiler_intent", owner_id=f"{rid}:{number}"
            )
            if intent_ref is None:
                raise PermissionError("compiler feedback lacks its original admitted action intent")
            import json

            intent = json.loads(
                await artifact_read_bytes(catalog.backend, intent_ref, allow_restricted=True)
            )
            if intent != {
                "trajectory_id": trajectory.trajectory_id,
                "model_turn": rid,
                "call": tool_call,
                "policy_digest": trajectory.policy.digest,
            }:
                raise PermissionError("compiler action intent differs from the observed call")
            references.append(intent_ref)
            receipt_ref = await owned_reference(
                session, catalog, owner_type="atlas_compiler_receipt", owner_id=f"{rid}:{number}"
            )
            if receipt_ref is None or receipt_ref.media_type != RECEIPT_MEDIA:
                raise PermissionError("compiler continuation is missing scoped tool feedback")
            receipt = CompilerReceipt.model_validate_json(
                await artifact_read_bytes(catalog.backend, receipt_ref, allow_restricted=True)
            )
            receipts.append(receipt)
            references.append(receipt_ref)
        previous.append((result, tuple(receipts)))
    return previous, references


async def validate_tool_turn(
    session: AsyncSession,
    catalog: ArtifactCatalog,
    *,
    trajectory_ref: ArtifactRef,
    activation_ref: ArtifactRef,
    run_id: str,
    request: GenerationRequest,
) -> tuple[GenerationRequest, datetime]:
    """Return the original frozen root intent after validating this exact child turn."""
    if (
        trajectory_ref.media_type != TRAJECTORY_MEDIA
        or not trajectory_ref.restricted
        or not trajectory_ref.raw_data
    ):
        raise PermissionError("compiler trajectory requires private typed control evidence")
    trajectory = CodingToolTrajectory.model_validate_json(
        await artifact_read_bytes(catalog.backend, trajectory_ref, allow_restricted=True)
    )
    if trajectory.run_id != run_id or trajectory.activation != activation_ref:
        raise PermissionError("compiler trajectory belongs to another activation or run")
    owned = await owned_reference(
        session,
        catalog,
        owner_type="atlas_coding_tool_trajectory",
        owner_id=trajectory.trajectory_id,
    )
    if owned != trajectory_ref:
        raise PermissionError("compiler trajectory lacks its original immutable owner")
    initial = trajectory.initial_request
    trial_row = await session.get(AtlasTrialRequestRow, initial.request_id)
    if trial_row is None:
        raise PermissionError("compiler trajectory requires a registered Atlas root")
    trial = AtlasTrialRequest.model_validate_json(canonical_json_bytes(trial_row.record_json))
    if trial.run_id != run_id or trial.wire_request_digest != sha256_digest(initial):
        raise PermissionError("compiler trajectory differs from its frozen root intent")
    execution = await session.get(ResearchExecutionRow, trial.research_execution_digest)
    if execution is None:
        raise PermissionError("compiler trajectory lost its research execution")
    profile_row = await session.get(
        HarnessProfileRow, execution.record_json["harness_profile_digest"]
    )
    if profile_row is None:
        raise PermissionError("compiler trajectory lost its harness profile")
    profile = HarnessProfile.model_validate_json(canonical_json_bytes(profile_row.record_json))
    expected = sha256_digest(compiler_wire_identity(trajectory.policy))
    if (
        len(profile.tools) != 1
        or profile.tools[0].component_id != TOOL_ID
        or profile.tools[0].digest != expected
        or profile.continuation.private_reasoning_used_as_context
        or profile.continuation.reasoning_retention_enabled
    ):
        raise PermissionError("compiler trajectory differs from its reviewed tool profile")
    identities = [initial.request_id] + [
        f"{initial.request_id}.turn-{i}" for i in range(1, trajectory.policy.max_model_turns)
    ]
    if request.request_id not in identities:
        raise PermissionError("model turn lies outside the finite compiler trajectory")
    step = identities.index(request.request_id)
    previous, _ = await prior_turns(session, catalog, trajectory, step)
    count = (
        trajectory.initial_input_tokens
        if step == 0
        else int(request.metadata.get("tool_input_tokens", "0"))
    )
    expected_request = next_tool_request(trajectory, previous, input_tokens=count)
    if request != expected_request:
        raise PermissionError("model turn differs from its exact public tool continuation")
    # A later admitted effect or a terminal trajectory forbids branching/reordering.
    later = await session.scalar(
        select(ExternalCallRow.request_id).where(
            ExternalCallRow.request_id.in_(identities[step + 1 :])
        )
    )
    terminal = await owned_reference(
        session, catalog, owner_type="atlas_coding_tool_result", owner_id=trajectory.trajectory_id
    )
    existing = await session.get(ExternalCallRow, request.request_id)
    replay = existing is not None and existing.status == "completed"
    if not replay and not trajectory.started_at <= datetime.now(UTC) < trajectory.model_deadline:
        raise PermissionError("compiler trajectory is outside its immutable execution window")
    if (later is not None or terminal is not None) and not replay:
        raise PermissionError("compiler trajectory is already advanced or terminal")
    return initial, trajectory.model_deadline
