from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import (
    CorpusPool,
    ItemStatus,
    RightsUse,
    SourceRights,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AuthoredDemonstrationRow,
    CorpusItemRow,
    VerifierResultRow,
)
from padawan.training.contracts import AuthoredDemonstration


@dataclass(frozen=True)
class GovernedAuthoredDemonstration:
    demonstration: AuthoredDemonstration
    verifier_result: VerifierResult


class AuthoredDemonstrationRegistry:
    """Admission gate for project-authored gold behavior and its verifier evidence."""

    async def admit(
        self,
        session: AsyncSession,
        *,
        demonstration: AuthoredDemonstration,
    ) -> GovernedAuthoredDemonstration:
        item = await session.get(CorpusItemRow, demonstration.source_item_id)
        if item is None:
            raise KeyError(demonstration.source_item_id)
        if item.competency_id != demonstration.competency_id:
            raise ValueError("authored demonstration competency differs from its source item")
        if item.pool != CorpusPool.CURRICULUM.value or item.visibility_class != "training":
            raise ValueError("authored demonstrations require a training-visible curriculum item")
        if item.status not in {ItemStatus.ACTIVE.value, ItemStatus.LEASED.value}:
            raise ValueError("authored demonstration source item is not active")
        if item.expected_answer is None:
            raise ValueError("authored demonstration source item has no governed answer")

        source_rights = SourceRights.model_validate(item.rights_json, strict=False)
        source_rights_digest = sha256_digest(source_rights.model_dump(mode="json"))
        if item.rights_digest != source_rights_digest:
            raise ValueError("authored demonstration source-item rights digest is invalid")
        if not source_rights.permits(RightsUse.SFT):
            raise ValueError("authored demonstration source item lacks confirmed SFT rights")
        if not demonstration.rights.permits(RightsUse.SFT):
            raise ValueError("authored demonstration output lacks confirmed SFT rights")

        verifier_row = await session.get(VerifierResultRow, demonstration.verifier_result_id)
        if verifier_row is None:
            raise KeyError(demonstration.verifier_result_id)
        verifier = VerifierResult.model_validate(verifier_row.record_json, strict=False)
        verifier_digest = sha256_digest(verifier.model_dump(mode="json"))
        if (
            verifier_row.record_digest != verifier_digest
            or demonstration.verifier_result_digest != verifier_digest
        ):
            raise ValueError("authored demonstration verifier digest is invalid")
        if (
            verifier_row.verifier_id != verifier.verifier_id
            or verifier_row.verifier_version != verifier.verifier_version
            or verifier_row.scope != verifier.scope
            or verifier_row.disposition != verifier.disposition.value
            or verifier_row.deterministic != verifier.deterministic
        ):
            raise ValueError("authored demonstration verifier index differs from its record")
        if verifier.scope != demonstration.verification_scope:
            raise ValueError("authored demonstration verifier scope is invalid")
        if not verifier.deterministic or verifier.disposition != VerifierDisposition.VERIFIED:
            raise ValueError("authored demonstrations require deterministic verified evidence")
        if _utc(demonstration.created_at) < max(_utc(item.created_at), _utc(verifier.created_at)):
            raise ValueError("authored demonstration predates its source evidence")

        payload = demonstration.model_dump(mode="json")
        record_digest = sha256_digest(payload)
        existing = await session.get(AuthoredDemonstrationRow, demonstration.demonstration_id)
        if existing is not None:
            if existing.record_digest != record_digest or existing.record_json != payload:
                raise ValueError("authored demonstration ID conflicts with persisted evidence")
            return GovernedAuthoredDemonstration(demonstration, verifier)
        digest_owner = await session.scalar(
            select(AuthoredDemonstrationRow).where(
                AuthoredDemonstrationRow.record_digest == record_digest
            )
        )
        if digest_owner is not None:
            raise ValueError("identical authored demonstration is admitted under another identity")
        session.add(
            AuthoredDemonstrationRow(
                demonstration_id=demonstration.demonstration_id,
                domain_id=demonstration.domain_id,
                competency_id=demonstration.competency_id,
                source_item_id=demonstration.source_item_id,
                verifier_result_id=demonstration.verifier_result_id,
                rights_digest=demonstration.rights_digest,
                record_digest=record_digest,
                record_json=payload,
                created_at=demonstration.created_at,
            )
        )
        await session.flush()
        return GovernedAuthoredDemonstration(demonstration, verifier)

    async def get(
        self, session: AsyncSession, *, demonstration_id: str
    ) -> GovernedAuthoredDemonstration:
        row = await session.get(AuthoredDemonstrationRow, demonstration_id)
        if row is None:
            raise KeyError(demonstration_id)
        demonstration = AuthoredDemonstration.model_validate(row.record_json, strict=False)
        verifier_row = await session.get(VerifierResultRow, demonstration.verifier_result_id)
        if verifier_row is None:
            raise ValueError("authored demonstration verifier evidence is missing")
        return GovernedAuthoredDemonstration(
            demonstration=demonstration,
            verifier_result=VerifierResult.model_validate(verifier_row.record_json, strict=False),
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
