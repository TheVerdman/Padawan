from __future__ import annotations

import pytest

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.atlas.harness import (
    atlas_schema_identity,
    build_atlas_execution_manifest,
    build_atlas_harness_profile,
)
from padawan.models.contracts import ResearchRole
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import ParentStateIdentity
from tests.support.atlas_harness import (
    NOW,
    _base_control,
    _condition,
    _suite,
)


def test_atlas_profile_preserves_inkling_guard_under_retention_and_compaction() -> None:
    base = _base_control()
    profile = build_atlas_harness_profile(
        base=base.profile,
        condition=_condition(),
        source_revision="c2e7a25",
        created_at=NOW,
    )

    assert profile.instrumentation.capability_atlas_schema == atlas_schema_identity()
    assert profile.continuation.reasoning_retention_enabled
    assert profile.context.compaction_enabled
    assert profile.context.compactor is not None
    assert not profile.continuation.response_storage_enabled
    assert not profile.continuation.previous_response_id_enabled
    assert not profile.continuation.private_reasoning_used_as_context


def test_atlas_execution_binds_suite_and_rejects_checkpoint_substitution() -> None:
    base = _base_control()
    parent = ParentStateIdentity(
        state_id="state-atlas",
        state_hash=sha256_digest("state-atlas"),
        student_id="student-atlas",
        checkpoint_id=INKLING_SMALL_AMPERE.checkpoint_id,
        runtime_id="inkling-vllm",
        research_role=ResearchRole.TARGET,
        branch_id="atlas",
    )
    base_execution = base.execution_manifest(
        execution_id="base-serving-identity",
        seed=17,
        created_at=NOW,
        parent_state=parent,
    )
    profile = build_atlas_harness_profile(
        base=base.profile,
        condition=_condition(),
        source_revision="c2e7a25",
        created_at=NOW,
    )
    suite = _suite(
        base.environment_fingerprint,
        task_manifest_digest=base_execution.task.task_manifest_digest,
        corpus_digest=base_execution.task.corpus_digest,
    )
    execution = build_atlas_execution_manifest(
        base=base_execution,
        profile=profile,
        campaign_digest=sha256_digest("campaign"),
        condition=_condition(),
        suite=suite,
        seed=19,
        created_at=NOW,
    )
    assert execution.task.task_manifest_digest == base_execution.task.task_manifest_digest
    assert execution.task.corpus_digest == base_execution.task.corpus_digest
    assert execution.task.task_id == suite.suite_id
    assert execution.harness_parameters["reasoning_effort"] == "max"
    assert execution.student_model == base_execution.student_model

    with pytest.raises(ValueError, match="checkpoint"):
        build_atlas_execution_manifest(
            base=base_execution,
            profile=profile,
            campaign_digest=sha256_digest("campaign"),
            condition=_condition(checkpoint="favorable-substitute"),
            suite=suite,
            seed=19,
            created_at=NOW,
        )
