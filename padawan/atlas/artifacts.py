"""Explicit privileged artifact retention for Atlas registry evidence.

This broker boundary grants no worker, memory, evaluation, or training admission.
It does not attest a provider workload or authenticate the calling principal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import (
    ArtifactInformationStore,
    ForensicArtifactRef,
    InformationClass,
)
from padawan.artifacts.store import ArtifactCatalog
from padawan.models.contracts import ArtifactRef
from padawan.models.tables import ArtifactReferenceRow

type AtlasArtifactOwner = Literal[
    "atlas_trial_request", "atlas_trial_result", "atlas_exploratory_proposal"
]


class AtlasArtifactBoundary:
    """Classify and retain bounded explicit source references under exact record owners."""

    maximum_artifacts = 64
    maximum_bytes = 67_108_864

    def __init__(self, catalog: ArtifactCatalog) -> None:
        self.catalog = catalog
        self.information = ArtifactInformationStore(catalog)

    async def retain(
        self,
        session: AsyncSession,
        *,
        owner_type: AtlasArtifactOwner,
        owner_id: str,
        references: tuple[ArtifactRef, ...],
        recorded_at: datetime,
    ) -> None:
        """Called only by the trusted registry in the source record's transaction."""
        refs = self._references(owner_type, owner_id, references, recorded_at)
        async with session.begin_nested():
            for reference in refs:
                await self.information.classify(
                    session,
                    artifact=reference,
                    information_class=InformationClass.FORENSIC,
                    classified_by="padawan.atlas.forensics/v1",
                    reason="Privileged Atlas evidence; retention grants no process admission.",
                    classified_at=recorded_at,
                )
                await self.catalog.reference(
                    session, reference, owner_type=owner_type, owner_id=owner_id
                )
            await self.validate(
                session,
                owner_type=owner_type,
                owner_id=owner_id,
                references=refs,
                recorded_at=recorded_at,
            )

    async def validate(
        self,
        session: AsyncSession,
        *,
        owner_type: AtlasArtifactOwner,
        owner_id: str,
        references: tuple[ArtifactRef, ...],
        recorded_at: datetime,
    ) -> tuple[ForensicArtifactRef, ...]:
        """Privileged result only. Missing legacy classification/ownership is not repaired."""
        refs = self._references(owner_type, owner_id, references, recorded_at)
        classified = []
        for reference in refs:
            information = await self.information.get(session, artifact_id=reference.artifact_id)
            if (
                information.artifact != reference
                or information.information_class != InformationClass.FORENSIC
                or information.classified_at > recorded_at
            ):
                raise PermissionError(
                    "Atlas evidence lacks its exact original forensic classification"
                )
            classified.append(
                ForensicArtifactRef(artifact=reference, classification_digest=information.digest)
            )
        actual = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == owner_type,
                    ArtifactReferenceRow.owner_id == owner_id,
                )
            )
        )
        if actual != {ref.artifact_id for ref in refs}:
            raise PermissionError("Atlas evidence has inconsistent independent retention ownership")
        return tuple(classified)

    def _references(
        self,
        owner_type: AtlasArtifactOwner,
        owner_id: str,
        references: tuple[ArtifactRef, ...],
        recorded_at: datetime,
    ) -> tuple[ArtifactRef, ...]:
        if (
            owner_type
            not in {"atlas_trial_request", "atlas_trial_result", "atlas_exploratory_proposal"}
            or not owner_id.strip()
        ):
            raise ValueError("Atlas evidence requires an explicit record owner")
        if recorded_at.tzinfo is None:
            raise ValueError("Atlas evidence time must be timezone-aware")
        if len(references) > self.maximum_artifacts:
            raise ValueError("Atlas evidence exceeds its reference bound")
        identities: dict[str, ArtifactRef] = {}
        for reference in references:
            if not reference.restricted or not reference.raw_data:
                raise PermissionError("Atlas forensic evidence requires protected raw storage")
            previous = identities.setdefault(reference.artifact_id, reference)
            if previous != reference:
                raise ValueError("Atlas evidence repeats an artifact with conflicting metadata")
        refs = tuple(identities[key] for key in sorted(identities))
        if sum(reference.size_bytes for reference in refs) > self.maximum_bytes:
            raise ValueError("Atlas evidence exceeds its byte bound")
        return refs
