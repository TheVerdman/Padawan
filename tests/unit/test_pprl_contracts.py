from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDisposition,
    AmberPolicy,
    AmberStatus,
)
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    RewardAuthorityKind,
    RewardAuthoritySpec,
)
from tests.pprl_helpers import (
    NOW,
    component,
    distribution,
    envelope,
    program,
    worker_model,
)


def test_distribution_requires_repeated_macro_rollouts() -> None:
    manifest = distribution()
    assert manifest.replication.minimum_unique_instances >= 2
    assert manifest.replication.minimum_rollouts_per_instance >= 2
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        manifest.replication.__class__(
            minimum_unique_instances=1,
            minimum_rollouts_per_instance=1,
            maximum_rollouts_per_instance=1,
        )


def test_empirical_and_adjudicated_authorities_cannot_masquerade_as_verifiable() -> None:
    with pytest.raises(ValidationError, match="must require replication"):
        RewardAuthoritySpec(
            authority_id="test.empirical",
            version="1",
            kind=RewardAuthorityKind.EMPIRICAL,
            description="empirical test",
            replication_required=False,
        )
    with pytest.raises(ValidationError, match="blinded assessment"):
        RewardAuthoritySpec(
            authority_id="test.adjudicated",
            version="1",
            kind=RewardAuthorityKind.ADJUDICATED,
            description="adjudicated test",
            blinded=False,
        )


def test_amber_admits_exact_action_and_denies_boundary_drift() -> None:
    distribution_digest = sha256_digest(distribution())
    process_program = program(distribution_digest)
    program_digest = sha256_digest(process_program)
    model = worker_model()
    authorization = envelope(
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        model=model,
    )
    request = AmberActionRequest(
        authorization_digest=authorization.digest,
        rollout_id="rollout-1",
        rollout_sequence=0,
        state_digest=sha256_digest("state-1"),
        lease_token_digest=sha256_digest("lease-1"),
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        split=ProjectSplit.TRAIN,
        persistence_mode=process_program.persistence_mode,
        event_kind=ProcessEventKind.TOOL_INVOKED,
        role_id="researcher",
        worker_model_digest=sha256_digest(model),
        tool_id="calculator",
        tool_digest=component("calculator").digest,
        tool_operation="evaluate",
        target_class="scientific_math",
        environment_fingerprint=authorization.environment.environment_fingerprint,
        projected_usage=ProjectBudgetUsage(actions=1),
        requested_at=NOW + timedelta(minutes=5),
    )
    policy = AmberPolicy()
    admitted = policy.decide(
        envelope=authorization,
        status=AmberStatus.ACTIVE,
        authorization_sequence=2,
        authorization_updated_at=NOW + timedelta(minutes=2),
        request=request,
        active_workers=0,
        decision_id="decision-admitted",
    )
    assert admitted.disposition == AmberAdmissionDisposition.ADMITTED
    assert admitted.reason_codes == ("authorized",)

    drifted = request.model_copy(
        update={"environment_fingerprint": sha256_digest("different-environment")}
    )
    denied = policy.decide(
        envelope=authorization,
        status=AmberStatus.ACTIVE,
        authorization_sequence=2,
        authorization_updated_at=NOW + timedelta(minutes=2),
        request=drifted,
        active_workers=0,
        decision_id="decision-denied",
    )
    assert denied.disposition == AmberAdmissionDisposition.DENIED
    assert "environment_identity_mismatch" in denied.reason_codes

    stale = policy.decide(
        envelope=authorization,
        status=AmberStatus.ACTIVE,
        authorization_sequence=2,
        authorization_updated_at=NOW + timedelta(minutes=6),
        request=request,
        active_workers=0,
        decision_id="decision-stale-request",
    )
    assert stale.disposition == AmberAdmissionDisposition.DENIED
    assert "authorization_state_changed_after_request" in stale.reason_codes


def test_amber_does_not_convert_oversized_action_into_smaller_capability() -> None:
    distribution_digest = sha256_digest(distribution())
    process_program = program(distribution_digest)
    program_digest = sha256_digest(process_program)
    model = worker_model()
    authorization = envelope(
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        model=model,
    )
    request = AmberActionRequest(
        authorization_digest=authorization.digest,
        rollout_id="rollout-1",
        rollout_sequence=0,
        state_digest=sha256_digest("state-1"),
        lease_token_digest=sha256_digest("lease-1"),
        program_digest=program_digest,
        distribution_digest=distribution_digest,
        split=ProjectSplit.TRAIN,
        persistence_mode=process_program.persistence_mode,
        event_kind=ProcessEventKind.WORKER_INVOKED,
        role_id="researcher",
        worker_model_digest=sha256_digest(model),
        target_class="scientific_math",
        environment_fingerprint=authorization.environment.environment_fingerprint,
        projected_usage=ProjectBudgetUsage(actions=authorization.budgets.actions + 1),
        requested_at=NOW + timedelta(minutes=5),
    )
    decision = AmberPolicy().decide(
        envelope=authorization,
        status=AmberStatus.ACTIVE,
        authorization_sequence=2,
        authorization_updated_at=NOW + timedelta(minutes=2),
        request=request,
        active_workers=0,
        decision_id="decision-budget",
    )
    assert decision.disposition == AmberAdmissionDisposition.DENIED
    assert decision.reason_codes == ("action_budget_exhausted",)
