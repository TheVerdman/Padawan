from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.experiments.engine import MatchedBlock, assign_blocks
from padawan.models.contracts import CorpusItemRecord, CorpusPool, RunState
from padawan.models.tables import RunRow
from padawan.orchestration.state_machine import InvalidTransitionError, RunStore


@given(
    seed=st.integers(min_value=-(2**31), max_value=2**31 - 1),
    family=st.sampled_from(list(AlgebraFamily)),
)
@settings(max_examples=80, deadline=None)
def test_algebra_generation_is_deterministic_valid_and_serializable(
    seed: int, family: AlgebraFamily
) -> None:
    generator = AlgebraCorpusGenerator()
    first = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=seed,
        groups_per_family=1,
        siblings_per_group=3,
        families=(family,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=seed,
        groups_per_family=1,
        siblings_per_group=3,
        families=(family,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert first == second
    assert len({item.item_id for item in first}) == 3
    assert len({item.instance_group_id for item in first}) == 1
    for item in first:
        assert CorpusItemRecord.model_validate_json(item.model_dump_json()) == item


@given(seed=st.integers(), count=st.integers(min_value=2, max_value=20))
@settings(max_examples=60)
def test_assignment_is_reproducible_and_balanced(seed: int, count: int) -> None:
    blocks = tuple(MatchedBlock(f"g{i}", f"a{i}", f"b{i}") for i in range(count))
    first = assign_blocks(seed=seed, blocks=blocks)
    second = assign_blocks(seed=seed, blocks=blocks)
    assert first == second
    a_treatment = sum(item.treatment_item_id.startswith("a") for item in first)
    assert abs(a_treatment - (count - a_treatment)) <= 1
    assert all(item.treatment_item_id != item.control_item_id for item in first)


_HAPPY_PATH = (
    RunState.CREATED,
    RunState.ITEMS_LEASED,
    RunState.BASE_STATE_SNAPSHOTTED,
    RunState.BRANCHES_CREATED,
    RunState.COLD_ATTEMPT_RUNNING,
    RunState.COLD_ATTEMPT_STORED,
    RunState.COLD_GRADED,
    RunState.TEACHER_REQUESTED,
    RunState.TEACHER_RESPONSE_STORED,
    RunState.COMMENT_VALIDATED,
    RunState.REVISION_RUNNING,
    RunState.REVISION_STORED,
    RunState.REVISION_GRADED,
    RunState.TRANSFER_RUNNING,
    RunState.TRANSFER_STORED,
    RunState.TRANSFER_GRADED,
    RunState.MEMORY_DECIDED,
    RunState.EXPOSURES_RECORDED,
    RunState.ITEMS_RETIRED,
    RunState.EPISODE_COMMITTED,
    RunState.COMPLETE,
)


@given(
    from_index=st.integers(min_value=0, max_value=len(_HAPPY_PATH) - 2),
    to_index=st.integers(min_value=0, max_value=len(_HAPPY_PATH) - 1),
)
def test_state_machine_accepts_exactly_the_next_happy_transition(
    from_index: int, to_index: int
) -> None:
    source = _HAPPY_PATH[from_index]
    target = _HAPPY_PATH[to_index]
    row = RunRow(
        run_id="run",
        episode_id=None,
        state=source.value,
        sequence=from_index,
        payload={},
        retry_count=0,
        retry_budget=3,
        paused=False,
        lease_owner=None,
        lease_token=None,
        lease_expires_at=None,
        last_error=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    if to_index == from_index + 1:
        RunStore._validate_transition(source, target, row)
    else:
        with pytest.raises(InvalidTransitionError):
            RunStore._validate_transition(source, target, row)
