"""Explicit information classes at the trusted artifact-broker boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog, ArtifactIntegrityError
from padawan.models.contracts import ArtifactRef, NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ArtifactInformationRow


class InformationClass(StrEnum):
    PROCESS_CANDIDATE = "process_candidate"
    FORENSIC = "forensic"


class ArtifactInformationRecord(StrictRecord):
    """Privileged classification evidence. Absence means unclassified, never process-safe."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    artifact: ArtifactRef
    information_class: InformationClass
    classified_by: NonEmpty
    reason: NonEmpty
    classified_at: datetime

    @model_validator(mode="after")
    def classification_is_consistent(self) -> ArtifactInformationRecord:
        if self.classified_at.tzinfo is None:
            raise ValueError("classification time must be timezone-aware")
        if self.information_class == InformationClass.FORENSIC:
            if not self.artifact.restricted or not self.artifact.raw_data:
                raise ValueError("forensic records require protected raw storage")
        elif self.artifact.raw_data:
            raise ValueError("raw artifacts cannot be process candidates")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ForensicArtifactRef(StrictRecord):
    """Researcher-only reference; never part of a worker projection."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    domain: Literal["forensic"] = "forensic"
    artifact: ArtifactRef
    classification_digest: Sha256

    @model_validator(mode="after")
    def storage_is_protected(self) -> ForensicArtifactRef:
        if not self.artifact.restricted or not self.artifact.raw_data:
            raise ValueError("forensic references require protected raw storage")
        return self


class ProcessArtifactRef(StrictRecord):
    """Worker-facing reference without forensic lineage or physical storage authority."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    domain: Literal["process"] = "process"
    process_artifact_id: Annotated[str, Field(pattern=r"^process-artifact-[0-9a-f]{32}$")]
    execution_digest: Sha256
    content_digest: Sha256
    media_type: NonEmpty
    size_bytes: Annotated[int, Field(ge=0)]


class ArtifactInformationStore:
    """Broker-only classification operations, independent of worker admission.

    The caller supplies trusted classification provenance. This is not a public
    endpoint and does not authenticate the broker or infer the semantics of bytes.
    """

    def __init__(self, catalog: ArtifactCatalog) -> None:
        self.catalog = catalog

    async def classify(
        self,
        session: AsyncSession,
        *,
        artifact: ArtifactRef,
        information_class: InformationClass,
        classified_by: str,
        reason: str,
        classified_at: datetime,
    ) -> ArtifactInformationRecord:
        proposed = ArtifactInformationRecord(
            artifact=artifact,
            information_class=information_class,
            classified_by=classified_by,
            reason=reason,
            classified_at=classified_at,
        )
        # Validate physical storage even for a duplicate declaration.
        await self.catalog.register(session, artifact)
        existing = await session.get(ArtifactInformationRow, artifact.artifact_id)
        if existing is not None:
            stored = _information_record(existing)
            if stored.artifact != artifact or stored.information_class != information_class:
                raise ArtifactIntegrityError("artifact information class is immutable")
            return stored
        session.add(
            ArtifactInformationRow(
                artifact_id=artifact.artifact_id,
                information_class=information_class.value,
                record_digest=proposed.digest,
                record_json=proposed.model_dump(mode="json"),
                created_at=classified_at,
            )
        )
        await session.flush()
        return proposed

    async def get(self, session: AsyncSession, *, artifact_id: str) -> ArtifactInformationRecord:
        row = await session.get(ArtifactInformationRow, artifact_id)
        if row is None:
            raise PermissionError("artifact has no admitted information classification")
        record = _information_record(row)
        await self.catalog.register(session, record.artifact)
        return record

    async def forensic_reference(
        self, session: AsyncSession, *, artifact_id: str
    ) -> ForensicArtifactRef:
        record = await self.get(session, artifact_id=artifact_id)
        if record.information_class != InformationClass.FORENSIC:
            raise PermissionError("artifact is not a classified forensic record")
        return ForensicArtifactRef(artifact=record.artifact, classification_digest=record.digest)


def _information_record(row: ArtifactInformationRow) -> ArtifactInformationRecord:
    record = ArtifactInformationRecord.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or record.digest != row.record_digest
        or record.artifact.artifact_id != row.artifact_id
        or record.information_class.value != row.information_class
        or record.classified_at
        != (row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at)
    ):
        raise ArtifactIntegrityError("stored information classification is inconsistent")
    return record
