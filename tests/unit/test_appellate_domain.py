from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.domains.builtin import build_builtin_domain_registry
from padawan.domains.legal.appellate import (
    AppellateBriefingDomain,
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    AppellateScenarioManifest,
    build_fourth_circuit_pack,
)
from padawan.models.contracts import CorpusPool, RightsUse

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def test_appellate_pack_and_matched_transfer_corpus_are_deterministic() -> None:
    generator = AppellateCorpusGenerator()
    first = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=19,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=NOW,
    )
    second = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=19,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=NOW,
    )

    assert first == second
    assert len(first) == len(AppellateScenarioFamily) * 2
    assert len({item.item_id for item in first}) == len(first)
    assert all(item.expected_answer is None for item in first)
    assert all(item.rights.permits(RightsUse.RLVR) for item in first)
    assert all(item.rights.permits(RightsUse.REDISTRIBUTION) is False for item in first)
    assert all(len(group) == 2 for group in _groups(first).values())
    domain = AppellateBriefingDomain()
    for item in first:
        domain.validate_item(item)


def test_court_pack_is_closed_and_does_not_claim_currentness() -> None:
    pack = build_fourth_circuit_pack()

    assert pack.coverage.task_complete is True
    assert pack.coverage.globally_complete is False
    assert pack.coverage.dependable_citator_source_ids == ()
    assert {authority.authority_id for authority in pack.authorities} == {
        "brown-v-walmart",
        "scott-v-harris",
        "tolan-v-cotton",
    }


def test_builtin_registry_installs_appellate_without_claiming_live_workflow() -> None:
    registry = build_builtin_domain_registry()
    package = registry.get("legal.appellate.fourth_circuit")

    assert package.spec.supports_tools is False
    assert package.spec.deterministic_verifiers == ("appellate_closed_record",)
    with pytest.raises(ValueError, match="no autonomous workflow"):
        registry.get_workflow("legal.appellate.fourth_circuit")


def test_scenario_content_address_rejects_record_tampering() -> None:
    scenario = AppellateCorpusGenerator().scenario(
        family=AppellateScenarioFamily.AMBIGUOUS_VIDEO,
        seed=23,
        split=CorpusPool.CURRICULUM.value,
        created_at=NOW,
    )
    payload = scenario.model_dump(mode="json")
    payload["record"]["facts"][0]["statement"] = "Invented jurisdictional fact."

    with pytest.raises(ValueError, match="record|digest"):
        AppellateScenarioManifest.model_validate(payload, strict=False)


def _groups(items):
    grouped: dict[str, list[object]] = {}
    for item in items:
        grouped.setdefault(item.instance_group_id, []).append(item)
    return grouped
