from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusPolicyError, CorpusRegistry
from padawan.domains.legal.appellate import AppellateCorpusGenerator, AppellateScenarioFamily
from padawan.models.contracts import CorpusItemRecord, CorpusPool, ItemStatus
from padawan.models.tables import CorpusItemRow, TemplateFamilyRow


def _generated(pool: CorpusPool = CorpusPool.CURRICULUM):
    generator = AlgebraCorpusGenerator()
    return generator, generator.generate(
        pool=pool,
        seed=300,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AlgebraFamily.INVALID_CANCELLATION,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def _register_competencies(database, generator, registry) -> None:
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=datetime(2026, 1, 1, tzinfo=UTC)):
            await registry.register_competency(session, competency)


async def test_template_family_cannot_cross_sealed_and_curriculum(database) -> None:
    generator, items = _generated()
    registry = CorpusRegistry()
    await _register_competencies(database, generator, registry)
    async with database.transaction() as session:
        await registry.register_items(session, items)
    sealed = items[0].model_copy(
        update={"item_id": "sealed-conflict", "pool": CorpusPool.SEALED_ANCHOR}
    )
    with pytest.raises(CorpusPolicyError, match="template family lineage"):
        async with database.transaction() as session:
            await registry.register_items(session, [sealed])


async def test_instance_group_cannot_cross_visibility_lineage(database) -> None:
    generator, items = _generated()
    registry = CorpusRegistry()
    await _register_competencies(database, generator, registry)
    async with database.transaction() as session:
        await registry.register_items(session, items)
    sealed = items[0].model_copy(
        update={
            "item_id": "sealed-group-conflict",
            "template_family_id": "new-sealed-family",
            "pool": CorpusPool.SEALED_ANCHOR,
        }
    )
    with pytest.raises(CorpusPolicyError, match="instance group lineage"):
        async with database.transaction() as session:
            await registry.register_items(session, [sealed])


@pytest.mark.parametrize("defect", ["missing_answer", "duplicate_answer", "sibling_leak"])
async def test_malformed_generated_group_is_quarantined(database, defect: str) -> None:
    generator, items = _generated()
    registry = CorpusRegistry()
    await _register_competencies(database, generator, registry)
    if defect == "missing_answer":
        items[1] = items[1].model_copy(update={"expected_answer": None})
    elif defect == "duplicate_answer":
        items[1] = items[1].model_copy(update={"expected_answer": items[0].expected_answer})
    else:
        leaked = json.dumps(items[0].expected_answer, sort_keys=True, separators=(",", ":"))
        items[1] = items[1].model_copy(update={"prompt": f"{items[1].prompt}\n{leaked}"})
    async with database.transaction() as session:
        await registry.register_items(session, items)
    async with database.transaction() as session:
        rows = (
            await session.scalars(
                select(CorpusItemRow).where(
                    CorpusItemRow.instance_group_id == items[0].instance_group_id
                )
            )
        ).all()
        family = await session.get(TemplateFamilyRow, items[0].template_family_id)
        assert len(rows) == 3
        assert all(row.pool == CorpusPool.QUARANTINE.value for row in rows)
        assert all(row.status == ItemStatus.QUARANTINED.value for row in rows)
        assert family is not None and family.contaminated


async def test_agentic_siblings_without_fixed_answers_use_distinct_task_identity(database) -> None:
    generator = AppellateCorpusGenerator()
    created_at = datetime(2026, 8, 8, tzinfo=UTC)
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=311,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AppellateScenarioFamily.AMBIGUOUS_VIDEO,),
        created_at=created_at,
    )
    registry = CorpusRegistry()
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=created_at):
            await registry.register_competency(session, competency)
        await registry.register_items(session, items)
    async with database.transaction() as session:
        rows = (
            await session.scalars(
                select(CorpusItemRow).where(
                    CorpusItemRow.instance_group_id == items[0].instance_group_id
                )
            )
        ).all()

    assert len(rows) == 3
    assert all(row.expected_answer is None for row in rows)
    assert all(row.status == ItemStatus.ACTIVE.value for row in rows)


async def test_contaminated_shadow_family_is_closed_in_full(database) -> None:
    generator, items = _generated(CorpusPool.ROTATING_SHADOW)
    registry = CorpusRegistry()
    await _register_competencies(database, generator, registry)
    async with database.transaction() as session:
        await registry.register_items(session, items)
        await registry.quarantine(
            session, item_id=items[0].item_id, reason="leak detected", close_family=True
        )
    async with database.transaction() as session:
        rows = (
            await session.scalars(
                select(CorpusItemRow).where(
                    CorpusItemRow.template_family_id == items[0].template_family_id
                )
            )
        ).all()
        assert all(row.status == ItemStatus.QUARANTINED.value for row in rows)
        assert all(row.pool == CorpusPool.QUARANTINE.value for row in rows)
        assert await registry.validate_inventory(session) == ()
        with pytest.raises(CorpusPolicyError):
            await registry.assert_flow_allowed(
                session, item_id=items[1].item_id, flow="shadow_evaluation"
            )


def test_difficulty_cannot_be_omitted_from_contract() -> None:
    _, items = _generated()
    payload = items[0].model_dump(mode="json")
    del payload["difficulty"]
    with pytest.raises(ValidationError):
        CorpusItemRecord.model_validate(payload)
