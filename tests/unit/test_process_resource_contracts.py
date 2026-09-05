from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import ProjectBudgetUsage
from padawan.pprl.resource_contracts import (
    MAX_RESOURCE,
    ProcessModelRate,
    ProcessResources,
    resources_from_usage,
)


@given(st.integers(0, 10**12), st.integers(0, 10**12))
def test_integer_balances_conserve_exactly(funding, reservation):
    left, right = ProcessResources(input_tokens=funding), ProcessResources(input_tokens=reservation)
    assert left.plus(right).minus(right) == left
    assert left.covers(right) == (funding >= reservation)


@pytest.mark.parametrize("value", [True, -1, 1.0, "1", MAX_RESOURCE + 1])
def test_resource_units_reject_coercion_or_overflow(value):
    with pytest.raises(ValidationError):
        ProcessResources(input_tokens=value)


def test_decimal_limits_round_outward_and_never_erase_tiny_reservations():
    usage = ProjectBudgetUsage(cost=0.0000001, wall_time_seconds=0.0000001)
    assert resources_from_usage(usage).micro_usd == 1
    assert resources_from_usage(usage, ceiling=False).micro_usd == 0
    assert resources_from_usage(usage).action_microseconds == 1
    assert resources_from_usage(ProjectBudgetUsage(cost=5e-324)).micro_usd == 1
    delta = resources_from_usage(
        ProjectBudgetUsage(cost=0.3), previous=ProjectBudgetUsage(cost=0.1)
    )
    assert delta.micro_usd == int(Decimal("0.2") * 1_000_000)
    with pytest.raises(ValueError):
        resources_from_usage(ProjectBudgetUsage(cost=0.1), previous=ProjectBudgetUsage(cost=0.3))
    with pytest.raises(ValueError):
        resources_from_usage(ProjectBudgetUsage(cost=1e100))


def test_rate_is_exact_and_rounds_up_fractional_micro_usd():
    rate = ProcessModelRate(
        rate_id="test",
        worker_model_digest=sha256_digest("worker"),
        input_micro_usd_per_million_tokens=3,
        output_micro_usd_per_million_tokens=7,
        request_micro_usd=2,
    )
    assert rate.price(1, 1) == 3
    assert rate.price(1_000_000, 1_000_000) == 12
    with pytest.raises(ValueError):
        rate.model_copy(update={"request_micro_usd": MAX_RESOURCE}).price(1, 1)
