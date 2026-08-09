from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from padawan.artifacts.store import LocalArtifactStore
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.temporal_grounding import (
    TemporalAuthoredDemonstrationFactory,
    TemporalGroundingCorpusGenerator,
    TemporalScenarioFamily,
)
from padawan.models.contracts import CorpusPool
from padawan.models.hashing import canonical_json_bytes
from padawan.rewards import RewardEngine
from padawan.training import (
    AuthoredDemonstrationRegistry,
    TrainingCompiler,
    TrainingProductKind,
)


def _product_lines(store, manifest, kind: TrainingProductKind) -> list[dict[str, object]]:
    product = next(product for product in manifest.products if product.kind == kind)
    text = store.read_text(product.artifact, allow_restricted=True)
    return [json.loads(line) for line in text.splitlines()]


async def test_verified_temporal_gold_compiles_as_separate_authored_sft(database, tmp_path) -> None:
    timestamp = datetime(2026, 8, 9, 12, tzinfo=UTC)
    generator = TemporalGroundingCorpusGenerator()
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=503,
        groups_per_family=1,
        siblings_per_group=3,
        families=(TemporalScenarioFamily.DURATION_CALIBRATION,),
        created_at=timestamp,
    )
    item = items[0]
    built = TemporalAuthoredDemonstrationFactory().build(item)
    corpus = CorpusRegistry()
    demonstrations = AuthoredDemonstrationRegistry()
    rewards = RewardEngine()

    async with database.transaction() as session:
        for competency in generator.competencies(created_at=timestamp):
            await corpus.register_competency(session, competency)
        await corpus.register_items(session, items)
        with pytest.raises(KeyError, match=built.verifier_result.result_id):
            await demonstrations.admit(session, demonstration=built.demonstration)
        await rewards.record_verifier_result(session, built.verifier_result)
        first = await demonstrations.admit(session, demonstration=built.demonstration)
        second = await demonstrations.admit(session, demonstration=built.demonstration)
        assert first == second

    store = LocalArtifactStore(tmp_path / "temporal-training-artifacts")
    compiler = TrainingCompiler(store)
    async with database.transaction() as session:
        bundle = await compiler.compile(session)
        verification = await compiler.verify(session, bundle_id=bundle.manifest.bundle_id)

    assert verification.valid, verification.errors
    assert bundle.manifest.source_demonstration_ids == (built.demonstration.demonstration_id,)
    assert bundle.manifest.included_counts[TrainingProductKind.AUTHORED_SFT] == 1
    assert bundle.manifest.included_counts[TrainingProductKind.SFT] == 0
    rows = _product_lines(store, bundle.manifest, TrainingProductKind.AUTHORED_SFT)
    assert len(rows) == 1
    row = rows[0]
    assert row["demonstration_id"] == built.demonstration.demonstration_id
    assert row["source_item_id"] == item.item_id
    assert [message["role"] for message in row["messages"]] == [
        "developer",
        "user",
        "assistant",
        "user",
    ]
    assert "padawan_temporal_context" in row["messages"][0]["content"]
    assert row["target_events"][-1]["content"] == canonical_json_bytes(row["final_answer"]).decode(
        "utf-8"
    )
    assert row["verification"]["disposition"] == "verified"

    evidence = _product_lines(store, bundle.manifest, TrainingProductKind.EVIDENCE_LEDGER)
    authored = [entry for entry in evidence if entry["source_kind"] == "authored_demonstration"]
    assert [entry["source_id"] for entry in authored] == [built.demonstration.demonstration_id]
