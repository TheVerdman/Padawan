from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.domains.contracts import (
    HardGateResult,
    RewardComponent,
    RewardComponentPolicy,
    RewardMissingAction,
    RewardObservation,
    RewardPolicy,
    RewardRecord,
    TrainingEligibilityDecision,
    TrainingLane,
    VerifierResult,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    RewardPolicyRow,
    RewardRow,
    TrainingEligibilityRow,
    VerifierResultRow,
)


@dataclass(frozen=True)
class RewardRecomputation:
    reward_id: str
    valid: bool
    stored_utility: float | None
    recomputed_utility: float | None
    errors: tuple[str, ...]


@dataclass(frozen=True)
class TrainingEligibilityVerification:
    decision_id: str
    valid: bool
    errors: tuple[str, ...]


class RewardEngine:
    """Append-only verifier and reward ledger with deterministic recomputation."""

    async def register_policy(self, session: AsyncSession, policy: RewardPolicy) -> RewardPolicyRow:
        payload = policy.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(RewardPolicyRow, (policy.policy_id, policy.version))
        if existing is not None:
            if existing.policy_digest != digest or existing.record_json != payload:
                raise ValueError("reward policy version conflicts with persisted policy")
            return existing
        digest_owner = await session.scalar(
            select(RewardPolicyRow).where(RewardPolicyRow.policy_digest == digest)
        )
        if digest_owner is not None:
            raise ValueError("identical reward policy content is registered under another identity")
        row = RewardPolicyRow(
            policy_id=policy.policy_id,
            version=policy.version,
            policy_digest=digest,
            record_json=payload,
            created_at=policy.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def record_verifier_result(
        self, session: AsyncSession, result: VerifierResult
    ) -> VerifierResultRow:
        payload = result.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(VerifierResultRow, result.result_id)
        if existing is not None:
            if existing.record_digest != digest or existing.record_json != payload:
                raise ValueError("verifier result ID conflicts with persisted evidence")
            return existing
        digest_owner = await session.scalar(
            select(VerifierResultRow).where(VerifierResultRow.record_digest == digest)
        )
        if digest_owner is not None:
            raise ValueError("identical verifier evidence is registered under another result ID")
        row = VerifierResultRow(
            result_id=result.result_id,
            verifier_id=result.verifier_id,
            verifier_version=result.verifier_version,
            scope=result.scope,
            disposition=result.disposition.value,
            deterministic=result.deterministic,
            record_digest=digest,
            record_json=payload,
            created_at=result.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def compute(
        self,
        session: AsyncSession,
        *,
        reward_id: str,
        policy_id: str,
        policy_version: str,
        hard_gates: tuple[HardGateResult, ...],
        observations: tuple[RewardObservation, ...],
        created_at: datetime | None = None,
    ) -> RewardRecord:
        policy_row = await session.get(RewardPolicyRow, (policy_id, policy_version))
        if policy_row is None:
            raise KeyError(f"unknown reward policy: {policy_id}@{policy_version}")
        policy = RewardPolicy.model_validate(policy_row.record_json, strict=False)
        await self._validate_gate_evidence(session, hard_gates)
        existing = await session.get(RewardRow, reward_id)
        effective_created_at = created_at or (
            RewardRecord.model_validate(existing.record_json, strict=False).created_at
            if existing is not None
            else datetime.now(UTC)
        )
        record = _build_reward(
            reward_id=reward_id,
            policy=policy,
            policy_digest=policy_row.policy_digest,
            hard_gates=hard_gates,
            observations=observations,
            created_at=effective_created_at,
        )
        payload = record.model_dump(mode="json")
        record_digest = sha256_digest(payload)
        if existing is not None:
            if existing.record_digest != record_digest or existing.record_json != payload:
                raise ValueError("reward ID conflicts with persisted computation")
            return RewardRecord.model_validate(existing.record_json, strict=False)
        input_owner = await session.scalar(
            select(RewardRow).where(RewardRow.input_digest == record.input_digest)
        )
        if input_owner is not None:
            raise ValueError(f"reward inputs are already recorded as {input_owner.reward_id}")
        session.add(
            RewardRow(
                reward_id=record.reward_id,
                policy_id=record.policy_id,
                policy_version=record.policy_version,
                policy_digest=record.policy_digest,
                input_digest=record.input_digest,
                record_digest=record_digest,
                eligible=record.eligible,
                derived_utility=record.derived_utility,
                record_json=payload,
                created_at=record.created_at,
            )
        )
        await session.flush()
        return record

    async def get(self, session: AsyncSession, *, reward_id: str) -> RewardRecord:
        row = await session.get(RewardRow, reward_id)
        if row is None:
            raise KeyError(reward_id)
        return RewardRecord.model_validate(row.record_json, strict=False)

    async def recompute(self, session: AsyncSession, *, reward_id: str) -> RewardRecomputation:
        row = await session.get(RewardRow, reward_id)
        if row is None:
            raise KeyError(reward_id)
        stored = RewardRecord.model_validate(row.record_json, strict=False)
        policy_row = await session.get(RewardPolicyRow, (stored.policy_id, stored.policy_version))
        if policy_row is None:
            return RewardRecomputation(
                reward_id=reward_id,
                valid=False,
                stored_utility=stored.derived_utility,
                recomputed_utility=None,
                errors=("reward policy is missing",),
            )
        policy = RewardPolicy.model_validate(policy_row.record_json, strict=False)
        observations = tuple(
            RewardObservation(
                component_id=component.component_id,
                value=component.value,
                evidence_refs=component.evidence_refs,
                missing_reason=component.missing_reason,
            )
            for component in stored.components
        )
        rebuilt = _build_reward(
            reward_id=stored.reward_id,
            policy=policy,
            policy_digest=policy_row.policy_digest,
            hard_gates=stored.hard_gates,
            observations=observations,
            created_at=stored.created_at,
        )
        errors: list[str] = []
        if policy_row.policy_digest != sha256_digest(policy_row.record_json):
            errors.append("registered reward policy digest is invalid")
        if row.policy_digest != policy_row.policy_digest:
            errors.append("stored policy digest differs from registered policy")
        if (
            row.policy_id != stored.policy_id
            or row.policy_version != stored.policy_version
            or row.policy_digest != stored.policy_digest
            or row.input_digest != stored.input_digest
            or row.eligible != stored.eligible
            or row.derived_utility != stored.derived_utility
        ):
            errors.append("reward index columns differ from its immutable record")
        if row.input_digest != rebuilt.input_digest:
            errors.append("stored reward input digest is not reproducible")
        if row.record_digest != sha256_digest(row.record_json):
            errors.append("stored reward record digest is invalid")
        try:
            await self._validate_gate_evidence(session, stored.hard_gates)
        except ValueError as exc:
            errors.append(f"stored hard-gate evidence is invalid: {exc}")
        if stored != rebuilt:
            errors.append("stored reward record differs from recomputed record")
        return RewardRecomputation(
            reward_id=reward_id,
            valid=not errors,
            stored_utility=stored.derived_utility,
            recomputed_utility=rebuilt.derived_utility,
            errors=tuple(errors),
        )

    async def record_training_eligibility(
        self,
        session: AsyncSession,
        *,
        reward_id: str,
        decision: TrainingEligibilityDecision,
    ) -> TrainingEligibilityRow:
        reward_row = await session.get(RewardRow, reward_id)
        if reward_row is None:
            raise KeyError(reward_id)
        reward = RewardRecord.model_validate(reward_row.record_json, strict=False)
        if reward_id not in decision.evidence_refs:
            raise ValueError("training eligibility must cite its source reward")
        if not reward.eligible and any(
            lane != TrainingLane.EVALUATION_ONLY for lane in decision.allowed_lanes
        ):
            raise ValueError("hard-gate failures cannot enter a training lane")
        payload = decision.model_dump(mode="json")
        digest = sha256_digest({"reward_id": reward_id, "decision": payload})
        existing = await session.get(TrainingEligibilityRow, decision.decision_id)
        if existing is not None:
            if (
                existing.reward_id != reward_id
                or existing.record_digest != digest
                or existing.record_json != payload
            ):
                raise ValueError("training eligibility ID conflicts with persisted decision")
            return existing
        row = TrainingEligibilityRow(
            decision_id=decision.decision_id,
            reward_id=reward_id,
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            record_digest=digest,
            record_json=payload,
            created_at=decision.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def verify_training_eligibility(
        self, session: AsyncSession, *, decision_id: str
    ) -> TrainingEligibilityVerification:
        row = await session.get(TrainingEligibilityRow, decision_id)
        if row is None:
            raise KeyError(decision_id)
        decision = TrainingEligibilityDecision.model_validate(row.record_json, strict=False)
        errors: list[str] = []
        expected_digest = sha256_digest({"reward_id": row.reward_id, "decision": row.record_json})
        if row.record_digest != expected_digest:
            errors.append("training eligibility decision digest is invalid")
        if (
            row.policy_id != decision.policy_id
            or row.policy_version != decision.policy_version
            or _as_utc(row.created_at) != _as_utc(decision.created_at)
        ):
            errors.append("training eligibility columns differ from its immutable record")
        reward_row = await session.get(RewardRow, row.reward_id)
        if reward_row is None:
            errors.append("training eligibility source reward is missing")
        else:
            reward = RewardRecord.model_validate(reward_row.record_json, strict=False)
            if row.reward_id not in decision.evidence_refs:
                errors.append("training eligibility does not cite its source reward")
            if not reward.eligible and any(
                lane != TrainingLane.EVALUATION_ONLY for lane in decision.allowed_lanes
            ):
                errors.append("hard-gate failure was admitted to a training lane")
        return TrainingEligibilityVerification(
            decision_id=decision_id,
            valid=not errors,
            errors=tuple(errors),
        )

    async def _validate_gate_evidence(
        self, session: AsyncSession, gates: tuple[HardGateResult, ...]
    ) -> None:
        if not gates:
            raise ValueError("reward computation requires at least one hard gate")
        for gate in gates:
            if not gate.evidence_refs:
                raise ValueError(f"hard gate has no verifier evidence: {gate.gate_id}")
            for result_id in gate.evidence_refs:
                result = await session.get(VerifierResultRow, result_id)
                if result is None:
                    raise ValueError(f"hard gate cites unknown verifier evidence: {result_id}")
                if result.record_digest != sha256_digest(result.record_json):
                    raise ValueError(f"hard gate cites corrupt verifier evidence: {result_id}")
                evidence = VerifierResult.model_validate(result.record_json, strict=False)
                if (
                    evidence.result_id != result.result_id
                    or evidence.disposition.value != result.disposition
                ):
                    raise ValueError(f"verifier evidence index is corrupt: {result_id}")
                if result.disposition != gate.disposition.value:
                    raise ValueError(
                        f"hard gate disposition conflicts with verifier evidence: {gate.gate_id}"
                    )


def default_meta_utility_policy(*, created_at: datetime | None = None) -> RewardPolicy:
    weights = {
        "held_out_capability_delta": 1.0,
        "unseen_transfer": 0.5,
        "delayed_retention": 0.5,
        "interference_and_regression": -1.0,
        "harmful_interventions": -1.0,
        "contamination": -2.0,
        "normalized_cost": -0.1,
    }
    return RewardPolicy(
        policy_id="padawan.meta_utility",
        version="1.0.0",
        description=(
            "Analysis utility over held-out capability, transfer, retention, regression, harm, "
            "contamination, and normalized cost; it never overrides hard gates."
        ),
        components=tuple(
            RewardComponentPolicy(component_id=component_id, weight=weight)
            for component_id, weight in weights.items()
        ),
        created_at=created_at or datetime.now(UTC),
    )


def _build_reward(
    *,
    reward_id: str,
    policy: RewardPolicy,
    policy_digest: str,
    hard_gates: tuple[HardGateResult, ...],
    observations: tuple[RewardObservation, ...],
    created_at: datetime,
) -> RewardRecord:
    observation_by_id = {observation.component_id: observation for observation in observations}
    if len(observation_by_id) != len(observations):
        raise ValueError("reward observation IDs must be unique")
    expected = {component.component_id for component in policy.components}
    if set(observation_by_id) != expected:
        missing = sorted(expected - set(observation_by_id))
        unknown = sorted(set(observation_by_id) - expected)
        raise ValueError(
            f"reward observations differ from policy; missing={missing}, unknown={unknown}"
        )
    components: list[RewardComponent] = []
    utility = 0.0
    utility_available = all(gate.passed for gate in hard_gates)
    for component_policy in policy.components:
        observation = observation_by_id[component_policy.component_id]
        normalized: float | None = None
        if observation.value is None:
            if component_policy.missing_action == RewardMissingAction.INVALIDATE_UTILITY:
                utility_available = False
        else:
            normalized = component_policy.normalization.apply(observation.value)
            utility += normalized * component_policy.weight
        components.append(
            RewardComponent(
                component_id=observation.component_id,
                value=observation.value,
                normalized_value=normalized,
                weight=component_policy.weight,
                evidence_refs=observation.evidence_refs,
                missing_reason=observation.missing_reason,
            )
        )
    ordered_gates = tuple(sorted(hard_gates, key=lambda gate: gate.gate_id))
    input_digest = sha256_digest(
        {
            "policy_digest": policy_digest,
            "hard_gates": [gate.model_dump(mode="json") for gate in ordered_gates],
            "observations": [
                observation_by_id[component.component_id].model_dump(mode="json")
                for component in policy.components
            ],
        }
    )
    return RewardRecord(
        reward_id=reward_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_digest=policy_digest,
        input_digest=input_digest,
        hard_gates=ordered_gates,
        components=tuple(components),
        derived_utility=utility if utility_available else None,
        created_at=created_at,
    )


def _as_utc(value: datetime) -> datetime:
    # SQLite returns timezone-aware columns as naive values. Padawan writes UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
