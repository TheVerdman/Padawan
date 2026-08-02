from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from padawan.corpus.algebra import AlgebraCorpusGenerator
from padawan.domains.algebra import AlgebraDomain
from padawan.domains.builtin import build_builtin_domain_registry
from padawan.domains.contracts import (
    HardGateResult,
    RewardComponent,
    RewardRecord,
    VerifierDisposition,
)
from padawan.domains.registry import DomainRegistry
from padawan.models.contracts import CorpusPool


def test_builtin_registry_validates_real_algebra_items() -> None:
    registry = build_builtin_domain_registry()
    item = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=7,
        groups_per_family=1,
        siblings_per_group=3,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )[0]

    registry.validate_item(item)

    assert registry.get("math.algebra").spec.version == "1.0.0"
    assert [spec.domain_id for spec in registry.installed()] == ["math.algebra", "math.lean"]


def test_registry_rejects_duplicate_active_domain() -> None:
    registry = DomainRegistry()
    registry.register(AlgebraDomain())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(AlgebraDomain())


def test_hard_gate_failure_cannot_be_compensated_by_scalar_reward() -> None:
    gate = HardGateResult(
        gate_id="proof-kernel",
        passed=False,
        disposition=VerifierDisposition.REJECTED,
        evidence_refs=("verifier-result-1",),
        reason="Lean rejected the proof",
    )
    component = RewardComponent(
        component_id="clarity",
        value=1.0,
        weight=1.0,
        evidence_refs=("judge-result-1",),
    )

    with pytest.raises(ValueError, match="hard-gate failures"):
        RewardRecord(
            reward_id="reward-1",
            policy_id="policy-default",
            policy_version="1",
            hard_gates=(gate,),
            components=(component,),
            derived_utility=1.0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    record = RewardRecord(
        reward_id="reward-2",
        policy_id="policy-default",
        policy_version="1",
        hard_gates=(gate,),
        components=(component,),
        derived_utility=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert not record.eligible


@pytest.mark.parametrize(
    "disposition",
    [
        VerifierDisposition.REJECTED,
        VerifierDisposition.UNKNOWN,
        VerifierDisposition.INFRASTRUCTURE_FAILURE,
    ],
)
def test_only_verified_hard_gates_can_pass(disposition: VerifierDisposition) -> None:
    with pytest.raises(ValueError, match="if and only if"):
        HardGateResult(
            gate_id="invalid-pass",
            passed=True,
            disposition=disposition,
            evidence_refs=("verifier-result-1",),
            reason="the verifier did not establish the gate",
        )


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_reward_values_must_be_finite(non_finite: float) -> None:
    with pytest.raises(ValueError, match="finite number"):
        RewardComponent(
            component_id="invalid-number",
            value=non_finite,
            weight=1.0,
            evidence_refs=("verifier-result-1",),
        )
