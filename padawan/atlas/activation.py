"""Trusted-controller admission for a finite Atlas run, using its existing ledger.

The operator supplies an explicit, retained activation record. This is declared authority,
not authentication, workload attestation, a cloud launcher, or a PPRL worker capability.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.adapters.base import GenerationRequest
from padawan.adapters.prepared import PreparedGeneration
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, artifact_read_bytes
from padawan.atlas.contracts import AtlasRunManifest, AtlasTrialRequest
from padawan.models.contracts import ArtifactRef, NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import ResearchExecutionManifest
from padawan.models.tables import (
    ArtifactReferenceRow,
    AtlasRunManifestRow,
    AtlasTrialRequestRow,
    ExternalCallRow,
    ResearchExecutionRow,
    RunRow,
)


class AtlasActivation(StrictRecord):
    """One immutable run and exact transport, explicitly bounded by the operator."""

    run_manifest_digest: Sha256
    authorization_ref: NonEmpty
    provider: NonEmpty
    destination: NonEmpty
    configuration_digest: Sha256
    max_in_flight: int = Field(ge=1, le=64)
    starts_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bounded_window(self) -> AtlasActivation:
        if self.starts_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Atlas activation requires timezone-aware bounds")
        if self.starts_at >= self.expires_at:
            raise ValueError("Atlas activation window is empty")
        return self


async def admit_atlas_generation(
    session: AsyncSession,
    *,
    catalog: ArtifactCatalog,
    activation_ref: ArtifactRef,
    run_id: str,
    request: GenerationRequest,
    prepared: PreparedGeneration,
    provider: str,
    before_dispatch: bool,
    tool_trajectory_ref: ArtifactRef | None = None,
) -> datetime:
    """Serialize reservations across controllers before any model effect is possible."""
    if (
        not activation_ref.restricted
        or not activation_ref.raw_data
        or activation_ref.media_type != "application/vnd.padawan.atlas-activation+json"
    ):
        raise PermissionError("Atlas activation must be a retained private control record")
    activation = AtlasActivation.model_validate_json(
        await artifact_read_bytes(catalog.backend, activation_ref, allow_restricted=True)
    )
    # A no-op UPDATE takes a writer lock on SQLite and a row lock on PostgreSQL.
    # It precedes every admission read, including the existing-call lookup in the executor.
    locked = await session.execute(
        update(AtlasRunManifestRow)
        .where(
            AtlasRunManifestRow.manifest_digest == activation.run_manifest_digest,
            AtlasRunManifestRow.run_id == run_id,
        )
        .values(run_id=run_id)
        .returning(AtlasRunManifestRow.run_id)
    )
    if locked.scalar_one_or_none() is None:
        raise PermissionError("Atlas activation has no matching registered run")
    manifest_row = await session.get(AtlasRunManifestRow, activation.run_manifest_digest)
    assert manifest_row is not None
    manifest = AtlasRunManifest.model_validate_json(canonical_json_bytes(manifest_row.record_json))
    anchor = request
    trajectory_deadline = activation.expires_at
    if tool_trajectory_ref is not None:
        from padawan.atlas.coding_tool_boundary import validate_tool_turn

        anchor, trajectory_deadline = await validate_tool_turn(
            session,
            catalog,
            trajectory_ref=tool_trajectory_ref,
            activation_ref=activation_ref,
            run_id=run_id,
            request=request,
        )
    row = await session.get(AtlasTrialRequestRow, anchor.request_id)
    if row is None:
        raise PermissionError("Atlas activation requires an admitted trial request")
    trial = AtlasTrialRequest.model_validate_json(canonical_json_bytes(row.record_json))
    execution_row = await session.get(ResearchExecutionRow, manifest.research_execution_digest)
    run = await session.get(RunRow, run_id)
    if execution_row is None or run is None:
        raise PermissionError("Atlas activation lost its execution or run owner")
    execution = ResearchExecutionManifest.model_validate_json(
        canonical_json_bytes(execution_row.record_json)
    )
    expected_provider = execution.student_model.runtime_parameters.get("provider")
    if expected_provider is None:
        raise PermissionError("Atlas activation requires an explicit provider identity")
    if (
        trial.run_id != run_id
        or trial.research_execution_digest != manifest.research_execution_digest
        or trial.wire_request_digest != sha256_digest(anchor)
        or prepared.request_digest != sha256_digest(request)
        or prepared.request_id != request.request_id
        or prepared.transport != "http"
        or prepared.destination != activation.destination
        or prepared.configuration_digest != activation.configuration_digest
        or provider != activation.provider
        or prepared.provider != provider
        or provider != expected_provider
        or prepared.model_id != execution.student_model.model_id
        or prepared.protocol != execution.student_model.protocol
    ):
        raise PermissionError("Atlas generation differs from its frozen run or transport")
    if manifest.external_execution and (
        manifest.external_authorization_ref != activation.authorization_ref
    ):
        raise PermissionError("Atlas activation differs from the run's external authorization")
    bound_refs = set(
        await session.scalars(
            select(ArtifactReferenceRow.artifact_id).where(
                ArtifactReferenceRow.owner_type == "atlas_activation",
                ArtifactReferenceRow.owner_id == run_id,
            )
        )
    )
    if bound_refs and bound_refs != {activation_ref.artifact_id}:
        raise PermissionError("Atlas run already has a different immutable activation")
    existing = await session.get(ExternalCallRow, request.request_id)
    # Completed replay performs no I/O; it remains readable after expiry or pause.
    replay = existing is not None and existing.status == "completed"
    if not replay:
        now = datetime.now(UTC)
        if not activation.starts_at <= now < activation.expires_at or run.paused:
            raise PermissionError("Atlas activation is outside its window or paused")
        if run.state in {"COMPLETE", "FAILED_TERMINAL", "FAILED_RETRYABLE", "REVIEW_REQUIRED"}:
            raise PermissionError("Atlas run is terminal")
        calls = (
            await session.scalars(
                select(ExternalCallRow).where(
                    ExternalCallRow.run_id == run_id,
                    ExternalCallRow.purpose == "capability_atlas",
                )
            )
        ).all()
        if any(call.status not in {"pending", "completed"} for call in calls):
            raise PermissionError("Atlas run has an unresolved external effect")
        occupied = sum(call.status == "pending" for call in calls)
        if (
            occupied + (0 if before_dispatch or existing is not None else 1)
            > activation.max_in_flight
        ):
            raise PermissionError("Atlas activation concurrency reservation is full")
    if not bound_refs:
        await ArtifactInformationStore(catalog).classify(
            session,
            artifact=activation_ref,
            information_class=InformationClass.FORENSIC,
            classified_by="padawan.atlas.activation/v1",
            reason="Operator-declared finite run authority; no worker admission.",
            classified_at=datetime.now(UTC),
        )
        await catalog.reference(
            session, activation_ref, owner_type="atlas_activation", owner_id=run_id
        )
    return min(activation.expires_at, trajectory_deadline)
