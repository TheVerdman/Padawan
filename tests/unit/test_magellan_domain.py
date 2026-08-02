from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.domains.builtin import build_builtin_domain_registry
from padawan.domains.magellan_improvement import (
    MagellanImprovementDomain,
    MagellanScenarioFamily,
    MagellanScenarioGenerator,
    MagellanScenarioManifest,
)
from padawan.domains.magellan_improvement.corpus import UNBOUND_ENVIRONMENT_FINGERPRINT
from padawan.models.contracts import CorpusPool
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_magellan_domain_generates_matched_governed_scenarios() -> None:
    domain = MagellanImprovementDomain()
    items = domain.generate_curriculum(
        pool=CorpusPool.CURRICULUM,
        seed=101,
        groups_per_family=1,
        siblings_per_group=2,
    )

    assert len(items) == len(MagellanScenarioFamily) * 2
    assert len({item.item_id for item in items}) == len(items)
    assert all(item.expected_answer is None for item in items)
    assert all(item.verifier_spec.parameters["environment_bound"] is False for item in items)
    assert all(
        item.verifier_spec.parameters["scenario"]["environment_fingerprint"]
        == UNBOUND_ENVIRONMENT_FINGERPRINT
        for item in items
    )
    assert all(len(group) == 2 for group in _groups(items).values())
    for item in items:
        domain.validate_item(item)


def test_bound_scenarios_are_tied_to_one_environment_fingerprint() -> None:
    fingerprint = sha256_digest("ready-magellan-environment")
    generator = MagellanScenarioGenerator(environment_fingerprint=fingerprint)

    items = generator.generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=7,
        groups_per_family=1,
        siblings_per_group=2,
        families=(MagellanScenarioFamily.INVALID_DEPENDENCY,),
        created_at=NOW,
    )

    assert len(items) == 2
    assert all(item.verifier_spec.parameters["environment_bound"] is True for item in items)
    scenario = items[0].verifier_spec.parameters["scenario"]
    assert scenario["environment_fingerprint"] == fingerprint
    assert scenario["expected_trace_status"] == "refused"
    assert scenario["required_postconditions"][0]["path"] == "$"


def test_builtin_registry_installs_magellan_without_claiming_live_workflow() -> None:
    registry = build_builtin_domain_registry()
    package = registry.get("agent.magellan_improvement")

    assert package.spec.supports_tools is True
    assert package.spec.deterministic_verifiers == ("magellan_scenario",)
    with pytest.raises(ValueError, match="no autonomous workflow"):
        registry.get_workflow("agent.magellan_improvement")


def test_scenario_manifest_rejects_family_input_tampering() -> None:
    scenario = MagellanScenarioGenerator().scenario(
        family=MagellanScenarioFamily.IDEMPOTENT_REPLAY,
        seed=41,
        split=CorpusPool.CURRICULUM.value,
        created_at=NOW,
    )
    payload = scenario.model_dump(mode="json")
    payload["task_inputs"]["repeat_count"] = 1

    with pytest.raises(ValueError, match="task inputs do not match"):
        MagellanScenarioManifest.model_validate(payload, strict=False)


def _groups(items):
    grouped: dict[str, list[object]] = {}
    for item in items:
        grouped.setdefault(item.instance_group_id, []).append(item)
    return grouped
