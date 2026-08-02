from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    ExposureRecord,
    ExposureType,
    ItemStatus,
    VisibilityClass,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    CompetencyRow,
    CorpusItemRow,
    ExposureRow,
    InstanceGroupRow,
    TemplateFamilyRow,
)


class CorpusPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    item: CorpusItemRecord
    token: str
    owner: str
    expires_at: datetime


@dataclass(frozen=True)
class RetirementPolicy:
    retire_exact_after_answer_feedback: bool = True
    retire_instance_group_after_answer_feedback: bool = True
    retire_template_family_after_answer_feedback: bool = False


class CorpusRegistry:
    """Transactional corpus authority; callers provide a transaction-owned session."""

    def __init__(self, *, retirement_policy: RetirementPolicy | None = None) -> None:
        self.retirement_policy = retirement_policy or RetirementPolicy()

    async def register_competency(
        self, session: AsyncSession, competency: CompetencyRecord
    ) -> CompetencyRow:
        existing = await session.get(CompetencyRow, competency.competency_id)
        if existing is not None:
            if existing.version != competency.version:
                raise CorpusPolicyError("competency ID already exists at a different version")
            return existing
        row = CompetencyRow(
            competency_id=competency.competency_id,
            version=competency.version,
            title=competency.title,
            description=competency.description,
            parent_competency_id=competency.parent_competency_id,
            prerequisite_ids=list(competency.prerequisite_competency_ids),
            grader_requirements=list(competency.grader_requirements),
            teacher_modes=[mode.value for mode in competency.permissible_teacher_modes],
            difficulty_calibration=competency.difficulty_calibration,
            created_at=competency.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def get_item(self, session: AsyncSession, *, item_id: str) -> CorpusItemRecord:
        row = await session.get(CorpusItemRow, item_id)
        if row is None:
            raise KeyError(item_id)
        return _to_record(row)

    async def register_items(
        self,
        session: AsyncSession,
        items: list[CorpusItemRecord],
    ) -> list[CorpusItemRow]:
        if not items:
            raise ValueError("at least one corpus item is required")
        quarantine_reasons = _batch_validation_reasons(items)
        registered: list[CorpusItemRow] = []
        for item in items:
            existing = await session.get(CorpusItemRow, item.item_id)
            if existing is not None:
                registered.append(existing)
                continue
            visibility = visibility_for_pool(item.pool)
            quarantine_reason = quarantine_reasons.get(item.item_id)
            family = await session.get(TemplateFamilyRow, item.template_family_id)
            lineage_digest = sha256_digest(
                {
                    "competency_id": item.competency_id,
                    "template_family_id": item.template_family_id,
                    "generator_version": item.generator_version,
                    "visibility_class": visibility.value,
                }
            )
            if family is None:
                family = TemplateFamilyRow(
                    template_family_id=item.template_family_id,
                    competency_id=item.competency_id,
                    visibility_class=visibility.value,
                    generator_version=item.generator_version,
                    lineage_digest=lineage_digest,
                    contaminated=quarantine_reason is not None,
                    created_at=item.created_at,
                )
                session.add(family)
                await session.flush()
            elif (
                family.competency_id != item.competency_id
                or family.visibility_class != visibility.value
                or family.generator_version != item.generator_version
            ):
                raise CorpusPolicyError(
                    "template family lineage conflicts with existing reservation"
                )
            elif quarantine_reason is not None:
                family.contaminated = True

            group = await session.get(InstanceGroupRow, item.instance_group_id)
            if group is None:
                group = InstanceGroupRow(
                    instance_group_id=item.instance_group_id,
                    template_family_id=item.template_family_id,
                    visibility_class=visibility.value,
                    generation_seed=item.generation_seed,
                    sibling_count=sum(
                        1
                        for sibling in items
                        if sibling.instance_group_id == item.instance_group_id
                    ),
                    created_at=item.created_at,
                )
                session.add(group)
                await session.flush()
            elif (
                group.template_family_id != item.template_family_id
                or group.visibility_class != visibility.value
            ):
                raise CorpusPolicyError(
                    "instance group lineage conflicts with existing reservation"
                )

            row = CorpusItemRow(
                item_id=item.item_id,
                competency_id=item.competency_id,
                template_family_id=item.template_family_id,
                instance_group_id=item.instance_group_id,
                visibility_class=visibility.value,
                generation_seed=item.generation_seed,
                generator_version=item.generator_version,
                difficulty=item.difficulty,
                prompt=item.prompt,
                expected_answer=item.expected_answer,
                verifier_spec=item.verifier_spec.model_dump(mode="json"),
                pool=(
                    CorpusPool.QUARANTINE.value
                    if quarantine_reason is not None
                    else item.pool.value
                ),
                status=(
                    ItemStatus.QUARANTINED.value
                    if quarantine_reason is not None
                    else item.status.value
                ),
                source=item.source,
                license=cast(str | None, item.model_dump(mode="python")["license"]),
                rights_json=item.rights.model_dump(mode="json"),
                rights_digest=sha256_digest(item.rights.model_dump(mode="json")),
                contamination_scope=item.contamination_scope,
                metadata_json={
                    "artifact_refs": [
                        artifact.model_dump(mode="json") for artifact in item.artifacts
                    ]
                },
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                lease_attempt=0,
                created_at=item.created_at,
                retired_at=item.retired_at,
                retirement_reason=quarantine_reason or item.retirement_reason,
            )
            session.add(row)
            await session.flush()
            registered.append(row)
        return registered

    async def lease_item(
        self,
        session: AsyncSession,
        *,
        owner: str,
        pool: CorpusPool,
        lease_for: timedelta,
        student_id: str | None = None,
        competency_id: str | None = None,
        difficulty_min: float | None = None,
        difficulty_max: float | None = None,
        purpose: str = "attempt",
        now: datetime | None = None,
    ) -> Lease | None:
        if not owner:
            raise ValueError("lease owner is required")
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease duration must be positive")
        if pool == CorpusPool.SEALED_ANCHOR and purpose != "sealed_evaluation":
            raise CorpusPolicyError("sealed anchors may only be leased for sealed evaluation")
        timestamp = now or datetime.now(UTC)
        await self.recover_expired_leases(session, now=timestamp)
        query: Select[tuple[CorpusItemRow]] = select(CorpusItemRow).where(
            CorpusItemRow.pool == pool.value,
            CorpusItemRow.status == ItemStatus.ACTIVE.value,
            CorpusItemRow.lease_owner.is_(None),
        )
        if student_id is not None:
            exposed_groups = select(ExposureRow.instance_group_id).where(
                ExposureRow.student_id == student_id
            )
            query = query.where(CorpusItemRow.instance_group_id.not_in(exposed_groups))
        if competency_id is not None:
            query = query.where(CorpusItemRow.competency_id == competency_id)
        if difficulty_min is not None:
            query = query.where(CorpusItemRow.difficulty >= difficulty_min)
        if difficulty_max is not None:
            query = query.where(CorpusItemRow.difficulty <= difficulty_max)
        query = query.order_by(
            CorpusItemRow.lease_attempt, CorpusItemRow.created_at, CorpusItemRow.item_id
        )
        dialect = session.bind.dialect.name if session.bind is not None else "unknown"
        if dialect == "postgresql":
            query = query.with_for_update(skip_locked=True).limit(1)
            row = await session.scalar(query)
            if row is None:
                return None
            token = f"lease-{uuid4()}"
            expires = timestamp + lease_for
            row.status = ItemStatus.LEASED.value
            row.lease_owner = owner
            row.lease_token = token
            row.lease_expires_at = expires
            row.lease_attempt += 1
            await session.flush()
        else:
            candidate = query.with_only_columns(CorpusItemRow.item_id).limit(1).scalar_subquery()
            token = f"lease-{uuid4()}"
            expires = timestamp + lease_for
            statement = (
                update(CorpusItemRow)
                .where(
                    CorpusItemRow.item_id == candidate,
                    CorpusItemRow.status == ItemStatus.ACTIVE.value,
                    CorpusItemRow.lease_owner.is_(None),
                )
                .values(
                    status=ItemStatus.LEASED.value,
                    lease_owner=owner,
                    lease_token=token,
                    lease_expires_at=expires,
                    lease_attempt=CorpusItemRow.lease_attempt + 1,
                )
                .returning(CorpusItemRow)
            )
            row = await session.scalar(statement)
            if row is None:
                return None
        return Lease(_to_record(row), token, owner, expires)

    async def lease_matched_group(
        self,
        session: AsyncSession,
        *,
        owner: str,
        pool: CorpusPool,
        count: int,
        lease_for: timedelta,
        student_id: str | None = None,
        competency_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Lease, ...]:
        """Lease unseen siblings together so a partial block cannot escape to another worker."""

        if count < 2:
            raise ValueError("matched group lease requires at least two items")
        if pool == CorpusPool.SEALED_ANCHOR:
            raise CorpusPolicyError("sealed anchors require the dedicated evaluation path")
        timestamp = now or datetime.now(UTC)
        await self.recover_expired_leases(session, now=timestamp)
        group_query = (
            select(CorpusItemRow.instance_group_id)
            .where(
                CorpusItemRow.pool == pool.value,
                CorpusItemRow.status == ItemStatus.ACTIVE.value,
                CorpusItemRow.lease_owner.is_(None),
            )
            .group_by(CorpusItemRow.instance_group_id)
            .having(func.count(CorpusItemRow.item_id) >= count)
            .order_by(CorpusItemRow.instance_group_id)
            .limit(1)
        )
        if student_id is not None:
            exposed_groups = select(ExposureRow.instance_group_id).where(
                ExposureRow.student_id == student_id
            )
            group_query = group_query.where(CorpusItemRow.instance_group_id.not_in(exposed_groups))
        if competency_id is not None:
            group_query = group_query.where(CorpusItemRow.competency_id == competency_id)
        group_id = await session.scalar(group_query)
        if group_id is None:
            return ()
        dialect = session.bind.dialect.name if session.bind is not None else "unknown"
        item_query = (
            select(CorpusItemRow)
            .where(
                CorpusItemRow.instance_group_id == group_id,
                CorpusItemRow.pool == pool.value,
                CorpusItemRow.status == ItemStatus.ACTIVE.value,
                CorpusItemRow.lease_owner.is_(None),
            )
            .order_by(CorpusItemRow.item_id)
            .limit(count)
        )
        expires = timestamp + lease_for
        if dialect != "postgresql":
            item_ids = list(
                (await session.scalars(item_query.with_only_columns(CorpusItemRow.item_id))).all()
            )
            if len(item_ids) != count:
                return ()
            group_token = f"grouplease-{uuid4()}"
            rows = list(
                (
                    await session.scalars(
                        update(CorpusItemRow)
                        .where(
                            CorpusItemRow.item_id.in_(item_ids),
                            CorpusItemRow.status == ItemStatus.ACTIVE.value,
                            CorpusItemRow.lease_owner.is_(None),
                        )
                        .values(
                            status=ItemStatus.LEASED.value,
                            lease_owner=owner,
                            lease_token=group_token,
                            lease_expires_at=expires,
                            lease_attempt=CorpusItemRow.lease_attempt + 1,
                        )
                        .returning(CorpusItemRow)
                    )
                ).all()
            )
            if len(rows) != count:
                await session.execute(
                    update(CorpusItemRow)
                    .where(CorpusItemRow.lease_token == group_token)
                    .values(
                        status=ItemStatus.ACTIVE.value,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        lease_attempt=CorpusItemRow.lease_attempt - 1,
                    )
                )
                return ()
            return tuple(
                Lease(_to_record(row), group_token, owner, expires)
                for row in sorted(rows, key=lambda item: item.item_id)
            )

        rows = list((await session.scalars(item_query.with_for_update(skip_locked=True))).all())
        if len(rows) != count:
            return ()
        leases: list[Lease] = []
        for row in rows:
            token = f"lease-{uuid4()}"
            row.status = ItemStatus.LEASED.value
            row.lease_owner = owner
            row.lease_token = token
            row.lease_expires_at = expires
            row.lease_attempt += 1
            leases.append(Lease(_to_record(row), token, owner, expires))
        await session.flush()
        return tuple(leases)

    async def recover_expired_leases(
        self, session: AsyncSession, *, now: datetime | None = None
    ) -> int:
        timestamp = now or datetime.now(UTC)
        result = await session.execute(
            update(CorpusItemRow)
            .where(
                CorpusItemRow.status == ItemStatus.LEASED.value,
                CorpusItemRow.lease_expires_at.is_not(None),
                CorpusItemRow.lease_expires_at <= timestamp,
            )
            .values(
                status=ItemStatus.ACTIVE.value,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def release_lease(
        self,
        session: AsyncSession,
        *,
        token: str,
        owner: str,
    ) -> bool:
        result = await session.execute(
            update(CorpusItemRow)
            .where(
                CorpusItemRow.lease_token == token,
                CorpusItemRow.lease_owner == owner,
                CorpusItemRow.status == ItemStatus.LEASED.value,
            )
            .values(
                status=ItemStatus.ACTIVE.value,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def record_exposure_and_retire(
        self,
        session: AsyncSession,
        *,
        exposure: ExposureRecord,
        lease_token: str,
        lease_owner: str,
        retirement_reason: str,
    ) -> tuple[ExposureRow, tuple[str, ...]]:
        existing = await self.record_exposure(
            session,
            exposure=exposure,
            lease_token=lease_token,
            lease_owner=lease_owner,
        )
        item = await session.scalar(
            select(CorpusItemRow)
            .where(
                CorpusItemRow.item_id == exposure.item_id,
                CorpusItemRow.lease_token == lease_token,
                CorpusItemRow.lease_owner == lease_owner,
            )
            .with_for_update()
        )
        if item is None:
            raise CorpusPolicyError("lease does not own the exposed item")
        answer_bearing = any(
            (
                exposure.answer_exposed,
                exposure.critique_exposed,
                exposure.repair_exposed,
                exposure.exposure_type
                in {ExposureType.ANSWER, ExposureType.CRITIQUE, ExposureType.REPAIR},
            )
        )
        if item.pool == CorpusPool.SEALED_ANCHOR.value and answer_bearing:
            raise CorpusPolicyError("sealed anchor answer-bearing exposure is forbidden")

        retired_ids: list[str] = []
        if answer_bearing:
            conditions = [CorpusItemRow.item_id == item.item_id]
            if self.retirement_policy.retire_instance_group_after_answer_feedback:
                conditions.append(CorpusItemRow.instance_group_id == item.instance_group_id)
            if self.retirement_policy.retire_template_family_after_answer_feedback:
                conditions.append(CorpusItemRow.template_family_id == item.template_family_id)
            selected = (
                await session.scalars(
                    select(CorpusItemRow).where(
                        or_(*conditions),
                        CorpusItemRow.status.in_(
                            [ItemStatus.ACTIVE.value, ItemStatus.LEASED.value]
                        ),
                    )
                )
            ).all()
            timestamp = datetime.now(UTC)
            for candidate in selected:
                candidate.status = ItemStatus.RETIRED.value
                candidate.retired_at = timestamp
                candidate.retirement_reason = retirement_reason
                candidate.lease_owner = None
                candidate.lease_token = None
                candidate.lease_expires_at = None
                retired_ids.append(candidate.item_id)
        else:
            item.status = ItemStatus.ACTIVE.value
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = None
        await session.flush()
        return existing, tuple(sorted(retired_ids))

    async def record_exposure(
        self,
        session: AsyncSession,
        *,
        exposure: ExposureRecord,
        lease_token: str,
        lease_owner: str,
    ) -> ExposureRow:
        """Persist exposure before external I/O while retaining the owned item lease."""

        item = await session.scalar(
            select(CorpusItemRow)
            .where(
                CorpusItemRow.item_id == exposure.item_id,
                CorpusItemRow.lease_token == lease_token,
                CorpusItemRow.lease_owner == lease_owner,
            )
            .with_for_update()
        )
        if item is None:
            raise CorpusPolicyError("lease does not own the exposed item")
        if (
            exposure.template_family_id != item.template_family_id
            or exposure.instance_group_id != item.instance_group_id
        ):
            raise CorpusPolicyError("exposure lineage does not match leased item")
        existing = await session.get(ExposureRow, exposure.exposure_id)
        if existing is not None:
            if not _same_exposure(existing, exposure):
                raise CorpusPolicyError("exposure ID replay conflicts with stored exposure")
            return existing
        row = ExposureRow(
            exposure_id=exposure.exposure_id,
            student_id=exposure.student_id,
            checkpoint_id=exposure.checkpoint_id,
            state_id=exposure.state_id,
            item_id=exposure.item_id,
            template_family_id=exposure.template_family_id,
            instance_group_id=exposure.instance_group_id,
            exposure_type=exposure.exposure_type.value,
            prompt_exposed=exposure.prompt_exposed,
            answer_exposed=exposure.answer_exposed,
            critique_exposed=exposure.critique_exposed,
            repair_exposed=exposure.repair_exposed,
            metadata_exposed=exposure.metadata_exposed,
            episode_id=exposure.episode_id or "",
            created_at=exposure.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def quarantine(
        self,
        session: AsyncSession,
        *,
        item_id: str,
        reason: str,
        close_family: bool = True,
    ) -> None:
        item = await session.scalar(
            select(CorpusItemRow).where(CorpusItemRow.item_id == item_id).with_for_update()
        )
        if item is None:
            raise KeyError(item_id)
        item.pool = CorpusPool.QUARANTINE.value
        item.status = ItemStatus.QUARANTINED.value
        item.retirement_reason = reason
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = None
        if close_family:
            family = await session.get(TemplateFamilyRow, item.template_family_id)
            if family is None:
                raise CorpusPolicyError("item has no template family")
            family.contaminated = True
            await session.execute(
                update(CorpusItemRow)
                .where(CorpusItemRow.template_family_id == item.template_family_id)
                .values(
                    pool=CorpusPool.QUARANTINE.value,
                    status=ItemStatus.QUARANTINED.value,
                    retirement_reason=reason,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
        await session.flush()

    async def assert_flow_allowed(
        self, session: AsyncSession, *, item_id: str, flow: str
    ) -> CorpusItemRow:
        item = await session.get(CorpusItemRow, item_id)
        if item is None:
            raise KeyError(item_id)
        if item.pool == CorpusPool.SEALED_ANCHOR.value and flow in {
            "teacher",
            "revision",
            "retrieval",
            "training",
        }:
            raise CorpusPolicyError(f"sealed anchor cannot enter {flow} flow")
        if item.status in {ItemStatus.RETIRED.value, ItemStatus.QUARANTINED.value}:
            raise CorpusPolicyError(f"{item.status} item cannot enter {flow} flow")
        family = await session.get(TemplateFamilyRow, item.template_family_id)
        if family is not None and family.contaminated and flow in {"shadow_evaluation", "training"}:
            raise CorpusPolicyError("contaminated family is closed")
        return item

    async def contamination_closure(
        self, session: AsyncSession, *, item_id: str, scope: str
    ) -> tuple[str, ...]:
        item = await session.get(CorpusItemRow, item_id)
        if item is None:
            raise KeyError(item_id)
        query = select(CorpusItemRow.item_id)
        if scope == "item":
            query = query.where(CorpusItemRow.item_id == item_id)
        elif scope == "instance_group":
            query = query.where(CorpusItemRow.instance_group_id == item.instance_group_id)
        elif scope in {"template_family", "lineage"}:
            query = query.where(CorpusItemRow.template_family_id == item.template_family_id)
        else:
            raise ValueError(f"unknown contamination scope: {scope}")
        return tuple(sorted((await session.scalars(query)).all()))

    async def validate_inventory(self, session: AsyncSession) -> tuple[str, ...]:
        errors: list[str] = []
        items = (await session.scalars(select(CorpusItemRow))).all()
        for item in items:
            if item.rights_digest != sha256_digest(item.rights_json):
                errors.append(f"invalid rights digest: {item.item_id}")
            try:
                CorpusItemRecord.model_validate(
                    {
                        "competency_id": item.competency_id,
                        "template_family_id": item.template_family_id,
                        "instance_group_id": item.instance_group_id,
                        "item_id": item.item_id,
                        "generation_seed": item.generation_seed,
                        "generator_version": item.generator_version,
                        "difficulty": item.difficulty,
                        "prompt": item.prompt,
                        "expected_answer": item.expected_answer,
                        "verifier_spec": item.verifier_spec,
                        "pool": item.pool,
                        "status": item.status,
                        "source": item.source,
                        "rights": item.rights_json,
                        "license": item.license,
                        "contamination_scope": item.contamination_scope,
                        "created_at": item.created_at,
                        "retired_at": item.retired_at,
                        "retirement_reason": item.retirement_reason,
                        "artifacts": item.metadata_json.get("artifact_refs", []),
                    },
                    strict=False,
                )
            except ValueError as exc:
                errors.append(f"invalid rights record: {item.item_id}: {exc}")
        families = (await session.scalars(select(TemplateFamilyRow))).all()
        for family in families:
            pools = set(
                (
                    await session.scalars(
                        select(CorpusItemRow.pool).where(
                            CorpusItemRow.template_family_id == family.template_family_id
                        )
                    )
                ).all()
            )
            if CorpusPool.SEALED_ANCHOR.value in pools and CorpusPool.CURRICULUM.value in pools:
                errors.append(f"family crosses sealed/curriculum: {family.template_family_id}")
            if family.contaminated and CorpusPool.ROTATING_SHADOW.value in pools:
                active_shadow = await session.scalar(
                    select(CorpusItemRow.item_id).where(
                        CorpusItemRow.template_family_id == family.template_family_id,
                        CorpusItemRow.pool == CorpusPool.ROTATING_SHADOW.value,
                        CorpusItemRow.status == ItemStatus.ACTIVE.value,
                    )
                )
                if active_shadow is not None:
                    errors.append(
                        f"contaminated family remains active in shadow: {family.template_family_id}"
                    )
        return tuple(errors)


def visibility_for_pool(pool: CorpusPool) -> VisibilityClass:
    if pool == CorpusPool.CURRICULUM:
        return VisibilityClass.TRAINING
    if pool == CorpusPool.ROTATING_SHADOW:
        return VisibilityClass.EVALUATION
    if pool == CorpusPool.SEALED_ANCHOR:
        return VisibilityClass.SEALED
    raise CorpusPolicyError("quarantine is a transition pool, not an intake visibility")


def _batch_validation_reasons(items: list[CorpusItemRecord]) -> dict[str, str]:
    """Quarantine malformed generated groups instead of admitting partial inventory."""

    issues_by_group: dict[str, list[str]] = {}
    grouped: dict[str, list[CorpusItemRecord]] = {}
    for item in items:
        grouped.setdefault(item.instance_group_id, []).append(item)
    for group_id, siblings in grouped.items():
        issues: list[str] = []
        if any(
            sibling.source == "deterministic:padawan.corpus.algebra"
            and sibling.expected_answer is None
            for sibling in siblings
        ):
            issues.append("algebra generator omitted expected answer")
        answers = [
            json.dumps(sibling.expected_answer, sort_keys=True, separators=(",", ":"))
            for sibling in siblings
        ]
        if len(answers) != len(set(answers)):
            issues.append("matched siblings have duplicate answers")
        for sibling in siblings:
            other_answers = {
                answer for answer, other in zip(answers, siblings, strict=True) if other != sibling
            }
            normalized_prompt = re.sub(r"\s+", "", sibling.prompt)
            if any(re.sub(r"\s+", "", answer) in normalized_prompt for answer in other_answers):
                issues.append(f"sibling answer leakage in {sibling.item_id}")
        if issues:
            issues_by_group[group_id] = sorted(set(issues))
    return {
        sibling.item_id: "; ".join(issues_by_group[group_id])
        for group_id, siblings in grouped.items()
        if group_id in issues_by_group
        for sibling in siblings
    }


def _to_record(row: CorpusItemRow) -> CorpusItemRecord:
    return CorpusItemRecord.model_validate(
        {
            "competency_id": row.competency_id,
            "template_family_id": row.template_family_id,
            "instance_group_id": row.instance_group_id,
            "item_id": row.item_id,
            "generation_seed": row.generation_seed,
            "generator_version": row.generator_version,
            "difficulty": row.difficulty,
            "prompt": row.prompt,
            "expected_answer": row.expected_answer,
            "verifier_spec": row.verifier_spec,
            "pool": row.pool,
            "status": row.status,
            "source": row.source,
            "rights": row.rights_json,
            "license": row.license,
            "contamination_scope": row.contamination_scope,
            "created_at": row.created_at,
            "retired_at": row.retired_at,
            "retirement_reason": row.retirement_reason,
            "artifacts": row.metadata_json.get("artifact_refs", []),
        },
        strict=False,
    )


def _same_exposure(row: ExposureRow, record: ExposureRecord) -> bool:
    return (
        row.student_id == record.student_id
        and row.checkpoint_id == record.checkpoint_id
        and row.state_id == record.state_id
        and row.item_id == record.item_id
        and row.template_family_id == record.template_family_id
        and row.instance_group_id == record.instance_group_id
        and row.exposure_type == record.exposure_type.value
        and row.prompt_exposed == record.prompt_exposed
        and row.answer_exposed == record.answer_exposed
        and row.critique_exposed == record.critique_exposed
        and row.repair_exposed == record.repair_exposed
        and row.metadata_exposed == record.metadata_exposed
        and row.episode_id == (record.episode_id or "")
    )
