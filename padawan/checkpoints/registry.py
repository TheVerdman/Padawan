from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.domains.contracts import VerifierResult
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    CheckpointComparisonRecord,
    CheckpointDecisionAction,
    CheckpointDecisionRecord,
    CheckpointEvaluationRecord,
    CheckpointManifest,
    CheckpointPromotionPolicy,
    CheckpointStatus,
    EvaluationSuiteManifest,
    MetricObservation,
    PromotionMetricRule,
)
from padawan.models.tables import (
    CheckpointComparisonRow,
    CheckpointDecisionRow,
    CheckpointEvaluationRow,
    CheckpointPromotionPolicyRow,
    CheckpointRow,
    EvaluationSuiteRow,
    StudyRow,
    VerifierResultRow,
)


@dataclass(frozen=True)
class CheckpointComparisonRecomputation:
    comparison_id: str
    valid: bool
    stored_recommendation: bool
    recomputed_recommendation: bool | None
    errors: tuple[str, ...]


@dataclass(frozen=True)
class CheckpointDecisionVerification:
    decision_id: str
    valid: bool
    errors: tuple[str, ...]


class CheckpointRegistry:
    """Govern externally produced checkpoints without pretending to train them."""

    async def register_checkpoint(
        self, session: AsyncSession, manifest: CheckpointManifest
    ) -> CheckpointRow:
        payload = manifest.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(CheckpointRow, manifest.checkpoint_id)
        if existing is not None:
            if existing.manifest_digest != digest or existing.record_json != payload:
                raise ValueError("checkpoint ID conflicts with persisted manifest")
            return existing
        if manifest.parent_checkpoint_id is not None:
            parent = await session.get(CheckpointRow, manifest.parent_checkpoint_id)
            if parent is None:
                raise ValueError("checkpoint parent is not registered")
            if parent.model_id != manifest.model_id:
                raise ValueError("checkpoint parent belongs to another model lineage")
        digest_owner = await session.scalar(
            select(CheckpointRow).where(CheckpointRow.model_digest == manifest.model_digest)
        )
        if digest_owner is not None:
            raise ValueError("checkpoint content is already registered under another identity")
        row = CheckpointRow(
            checkpoint_id=manifest.checkpoint_id,
            parent_checkpoint_id=manifest.parent_checkpoint_id,
            model_id=manifest.model_id,
            tokenizer_id=manifest.tokenizer_id,
            model_digest=manifest.model_digest,
            tokenizer_digest=manifest.tokenizer_digest,
            training_bundle_manifest_digest=manifest.training_bundle_manifest_digest,
            manifest_digest=digest,
            status=CheckpointStatus.CANDIDATE.value,
            record_json=payload,
            created_at=manifest.created_at,
            updated_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_suite(self, session: AsyncSession, manifest: EvaluationSuiteManifest) -> str:
        payload = manifest.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(EvaluationSuiteRow, digest)
        if existing is not None:
            if existing.record_json != payload:
                raise ValueError("evaluation suite digest conflicts with persisted manifest")
            return digest
        identity_owner = await session.scalar(
            select(EvaluationSuiteRow).where(
                EvaluationSuiteRow.suite_id == manifest.suite_id,
                EvaluationSuiteRow.version == manifest.version,
            )
        )
        if identity_owner is not None:
            raise ValueError("evaluation suite version cannot be rewritten")
        session.add(
            EvaluationSuiteRow(
                manifest_digest=digest,
                suite_id=manifest.suite_id,
                version=manifest.version,
                sealed=manifest.sealed,
                record_json=payload,
                created_at=manifest.created_at,
            )
        )
        await session.flush()
        return digest

    async def register_promotion_policy(
        self, session: AsyncSession, policy: CheckpointPromotionPolicy
    ) -> CheckpointPromotionPolicyRow:
        payload = policy.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(
            CheckpointPromotionPolicyRow, (policy.policy_id, policy.version)
        )
        if existing is not None:
            if existing.policy_digest != digest or existing.record_json != payload:
                raise ValueError("checkpoint promotion policy version cannot be rewritten")
            return existing
        session.add(
            CheckpointPromotionPolicyRow(
                policy_id=policy.policy_id,
                version=policy.version,
                policy_digest=digest,
                record_json=payload,
                created_at=policy.created_at,
            )
        )
        await session.flush()
        return await session.get_one(
            CheckpointPromotionPolicyRow, (policy.policy_id, policy.version)
        )

    async def start_evaluation(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: str,
        actor: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        decision_id: str | None = None,
        created_at: datetime | None = None,
    ) -> CheckpointDecisionRecord:
        return await self._transition(
            session,
            checkpoint_id=checkpoint_id,
            action=CheckpointDecisionAction.START_EVALUATION,
            to_status=CheckpointStatus.EVALUATING,
            actor=actor,
            reason=reason,
            evidence_refs=evidence_refs,
            decision_id=decision_id,
            created_at=created_at,
        )

    async def record_evaluation(
        self, session: AsyncSession, record: CheckpointEvaluationRecord
    ) -> CheckpointEvaluationRow:
        payload = record.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(CheckpointEvaluationRow, record.evaluation_id)
        if existing is not None:
            if existing.record_digest != digest or existing.record_json != payload:
                raise ValueError("checkpoint evaluation ID conflicts with persisted record")
            return existing
        checkpoint = await session.get(CheckpointRow, record.checkpoint_id)
        if checkpoint is None:
            raise ValueError("checkpoint evaluation cites an unknown checkpoint")
        if CheckpointStatus(checkpoint.status) not in {
            CheckpointStatus.EVALUATING,
            CheckpointStatus.PROMOTED,
        }:
            raise ValueError("checkpoint is not eligible to receive evaluation evidence")
        suite = await session.get(EvaluationSuiteRow, record.suite_manifest_digest)
        if suite is None:
            raise ValueError("checkpoint evaluation suite is not registered")
        study = await session.get(StudyRow, record.study_id)
        if study is None:
            raise ValueError("checkpoint evaluation study is not registered")
        if study.suite_manifest_digest != record.suite_manifest_digest:
            raise ValueError("checkpoint evaluation and study use different suite manifests")
        await self._validate_gate_evidence(session, record)
        row = CheckpointEvaluationRow(
            evaluation_id=record.evaluation_id,
            checkpoint_id=record.checkpoint_id,
            study_id=record.study_id,
            suite_manifest_digest=record.suite_manifest_digest,
            record_digest=digest,
            record_json=payload,
            created_at=record.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def compare(
        self,
        session: AsyncSession,
        *,
        comparison_id: str,
        baseline_evaluation_id: str,
        candidate_evaluation_id: str,
        policy_id: str,
        policy_version: str,
        created_at: datetime | None = None,
    ) -> CheckpointComparisonRecord:
        existing = await session.get(CheckpointComparisonRow, comparison_id)
        if existing is not None:
            stored = CheckpointComparisonRecord.model_validate(existing.record_json, strict=False)
            if (
                stored.baseline_evaluation_id != baseline_evaluation_id
                or stored.candidate_evaluation_id != candidate_evaluation_id
                or stored.policy_id != policy_id
                or stored.policy_version != policy_version
            ):
                raise ValueError("checkpoint comparison ID conflicts with persisted inputs")
            return stored
        policy_row = await session.get(CheckpointPromotionPolicyRow, (policy_id, policy_version))
        if policy_row is None:
            raise KeyError(f"unknown checkpoint policy: {policy_id}@{policy_version}")
        policy = CheckpointPromotionPolicy.model_validate(policy_row.record_json, strict=False)
        baseline_row = await session.get(CheckpointEvaluationRow, baseline_evaluation_id)
        candidate_row = await session.get(CheckpointEvaluationRow, candidate_evaluation_id)
        if baseline_row is None or candidate_row is None:
            raise ValueError("checkpoint comparison requires two persisted evaluations")
        if baseline_row.checkpoint_id == candidate_row.checkpoint_id:
            raise ValueError("checkpoint comparison requires distinct checkpoints")
        if baseline_row.suite_manifest_digest != candidate_row.suite_manifest_digest:
            raise ValueError("checkpoint comparisons require an identical suite manifest")
        suite_row = await session.get(EvaluationSuiteRow, baseline_row.suite_manifest_digest)
        if suite_row is None or not suite_row.sealed:
            raise ValueError("checkpoint promotion comparisons require a sealed suite")
        candidate_checkpoint = await session.get(CheckpointRow, candidate_row.checkpoint_id)
        if (
            candidate_checkpoint is None
            or candidate_checkpoint.status != CheckpointStatus.EVALUATING
        ):
            raise ValueError("candidate checkpoint is not in evaluation")
        baseline = CheckpointEvaluationRecord.model_validate(baseline_row.record_json, strict=False)
        candidate = CheckpointEvaluationRecord.model_validate(
            candidate_row.record_json, strict=False
        )
        record = _build_comparison_record(
            comparison_id=comparison_id,
            baseline=baseline,
            candidate=candidate,
            policy=policy,
            policy_digest=policy_row.policy_digest,
            created_at=created_at or datetime.now(UTC),
        )
        payload = record.model_dump(mode="json")
        digest = sha256_digest(payload)
        session.add(
            CheckpointComparisonRow(
                comparison_id=comparison_id,
                baseline_evaluation_id=baseline_evaluation_id,
                candidate_evaluation_id=candidate_evaluation_id,
                suite_manifest_digest=record.suite_manifest_digest,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                recommended=record.promotion_recommended,
                record_digest=digest,
                record_json=payload,
                created_at=record.created_at,
            )
        )
        await session.flush()
        return record

    async def recompute_comparison(
        self, session: AsyncSession, *, comparison_id: str
    ) -> CheckpointComparisonRecomputation:
        row = await session.get(CheckpointComparisonRow, comparison_id)
        if row is None:
            raise KeyError(comparison_id)
        stored = CheckpointComparisonRecord.model_validate(row.record_json, strict=False)
        errors: list[str] = []
        policy_row = await session.get(
            CheckpointPromotionPolicyRow, (stored.policy_id, stored.policy_version)
        )
        baseline_row = await session.get(CheckpointEvaluationRow, stored.baseline_evaluation_id)
        candidate_row = await session.get(CheckpointEvaluationRow, stored.candidate_evaluation_id)
        if policy_row is None:
            errors.append("checkpoint promotion policy is missing")
        if baseline_row is None:
            errors.append("baseline evaluation is missing")
        if candidate_row is None:
            errors.append("candidate evaluation is missing")
        if policy_row is None or baseline_row is None or candidate_row is None:
            return CheckpointComparisonRecomputation(
                comparison_id=comparison_id,
                valid=False,
                stored_recommendation=stored.promotion_recommended,
                recomputed_recommendation=None,
                errors=tuple(errors),
            )
        if policy_row.policy_digest != sha256_digest(policy_row.record_json):
            errors.append("checkpoint promotion policy digest is invalid")
        if baseline_row.record_digest != sha256_digest(baseline_row.record_json):
            errors.append("baseline evaluation digest is invalid")
        if candidate_row.record_digest != sha256_digest(candidate_row.record_json):
            errors.append("candidate evaluation digest is invalid")
        if row.record_digest != sha256_digest(row.record_json):
            errors.append("checkpoint comparison digest is invalid")
        policy = CheckpointPromotionPolicy.model_validate(policy_row.record_json, strict=False)
        baseline = CheckpointEvaluationRecord.model_validate(baseline_row.record_json, strict=False)
        candidate = CheckpointEvaluationRecord.model_validate(
            candidate_row.record_json, strict=False
        )
        if baseline.suite_manifest_digest != candidate.suite_manifest_digest:
            errors.append("checkpoint evaluations no longer share one suite manifest")
        suite_row = await session.get(EvaluationSuiteRow, baseline.suite_manifest_digest)
        if suite_row is None:
            errors.append("checkpoint evaluation suite is missing")
        elif not suite_row.sealed:
            errors.append("checkpoint promotion comparison suite is not sealed")
        if stored.policy_digest != policy_row.policy_digest:
            errors.append("stored comparison policy digest differs from registered policy")
        for label, evaluation in (("baseline", baseline), ("candidate", candidate)):
            try:
                await self._validate_gate_evidence(session, evaluation)
            except ValueError as exc:
                errors.append(f"{label} gate evidence is invalid: {exc}")
        rebuilt = _build_comparison_record(
            comparison_id=stored.comparison_id,
            baseline=baseline,
            candidate=candidate,
            policy=policy,
            policy_digest=policy_row.policy_digest,
            created_at=stored.created_at,
        )
        if stored != rebuilt:
            errors.append("stored checkpoint comparison differs from recomputed decision")
        return CheckpointComparisonRecomputation(
            comparison_id=comparison_id,
            valid=not errors,
            stored_recommendation=stored.promotion_recommended,
            recomputed_recommendation=rebuilt.promotion_recommended,
            errors=tuple(errors),
        )

    async def verify_decision(
        self, session: AsyncSession, *, decision_id: str
    ) -> CheckpointDecisionVerification:
        row = await session.get(CheckpointDecisionRow, decision_id)
        if row is None:
            raise KeyError(decision_id)
        stored = CheckpointDecisionRecord.model_validate(row.record_json, strict=False)
        errors: list[str] = []
        if row.record_digest != sha256_digest(row.record_json):
            errors.append("checkpoint decision digest is invalid")
        if (
            row.checkpoint_id != stored.checkpoint_id
            or row.comparison_id != stored.comparison_id
            or row.action != stored.action.value
            or row.from_status != stored.from_status.value
            or row.to_status != stored.to_status.value
            or row.actor != stored.actor
        ):
            errors.append("checkpoint decision columns differ from its immutable record")
        if stored.comparison_id is not None:
            comparison_row = await session.get(CheckpointComparisonRow, stored.comparison_id)
            if comparison_row is None:
                errors.append("checkpoint decision comparison is missing")
            else:
                recomputation = await self.recompute_comparison(
                    session, comparison_id=stored.comparison_id
                )
                if not recomputation.valid:
                    errors.extend(f"comparison: {error}" for error in recomputation.errors)
                comparison = CheckpointComparisonRecord.model_validate(
                    comparison_row.record_json, strict=False
                )
                candidate_evaluation = await session.get(
                    CheckpointEvaluationRow, comparison.candidate_evaluation_id
                )
                if (
                    candidate_evaluation is None
                    or candidate_evaluation.checkpoint_id != stored.checkpoint_id
                ):
                    errors.append("checkpoint decision cites another candidate checkpoint")
                if (
                    stored.policy_id != comparison.policy_id
                    or stored.policy_version != comparison.policy_version
                ):
                    errors.append("checkpoint decision policy differs from its comparison")
                if (
                    stored.action == CheckpointDecisionAction.PROMOTE
                    and recomputation.recomputed_recommendation is not True
                ):
                    errors.append("checkpoint promotion is not supported by recomputed evidence")
        elif stored.action in {
            CheckpointDecisionAction.PROMOTE,
            CheckpointDecisionAction.REJECT,
        }:
            errors.append("checkpoint promotion decision has no comparison")
        return CheckpointDecisionVerification(
            decision_id=decision_id,
            valid=not errors,
            errors=tuple(errors),
        )

    async def finalize(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: str,
        comparison_id: str,
        promote: bool,
        actor: str,
        reason: str,
        decision_id: str | None = None,
        created_at: datetime | None = None,
    ) -> CheckpointDecisionRecord:
        comparison_row = await session.get(CheckpointComparisonRow, comparison_id)
        if comparison_row is None:
            raise KeyError(comparison_id)
        candidate_evaluation = await session.get(
            CheckpointEvaluationRow, comparison_row.candidate_evaluation_id
        )
        if candidate_evaluation is None or candidate_evaluation.checkpoint_id != checkpoint_id:
            raise ValueError("comparison does not evaluate the selected candidate checkpoint")
        comparison = CheckpointComparisonRecord.model_validate(
            comparison_row.record_json, strict=False
        )
        if promote and not comparison.promotion_recommended:
            raise ValueError("checkpoint comparison does not permit promotion")
        return await self._transition(
            session,
            checkpoint_id=checkpoint_id,
            action=(
                CheckpointDecisionAction.PROMOTE if promote else CheckpointDecisionAction.REJECT
            ),
            to_status=(CheckpointStatus.PROMOTED if promote else CheckpointStatus.REJECTED),
            actor=actor,
            reason=reason,
            evidence_refs=(comparison_id,),
            comparison_id=comparison_id,
            policy_id=comparison.policy_id,
            policy_version=comparison.policy_version,
            decision_id=decision_id,
            created_at=created_at,
        )

    async def quarantine(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: str,
        actor: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        decision_id: str | None = None,
        created_at: datetime | None = None,
    ) -> CheckpointDecisionRecord:
        return await self._transition(
            session,
            checkpoint_id=checkpoint_id,
            action=CheckpointDecisionAction.QUARANTINE,
            to_status=CheckpointStatus.QUARANTINED,
            actor=actor,
            reason=reason,
            evidence_refs=evidence_refs,
            decision_id=decision_id,
            created_at=created_at,
        )

    async def revoke(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: str,
        actor: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        decision_id: str | None = None,
        created_at: datetime | None = None,
    ) -> CheckpointDecisionRecord:
        return await self._transition(
            session,
            checkpoint_id=checkpoint_id,
            action=CheckpointDecisionAction.REVOKE,
            to_status=CheckpointStatus.REVOKED,
            actor=actor,
            reason=reason,
            evidence_refs=evidence_refs,
            decision_id=decision_id,
            created_at=created_at,
        )

    async def _transition(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: str,
        action: CheckpointDecisionAction,
        to_status: CheckpointStatus,
        actor: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        comparison_id: str | None = None,
        policy_id: str | None = None,
        policy_version: str | None = None,
        decision_id: str | None = None,
        created_at: datetime | None = None,
    ) -> CheckpointDecisionRecord:
        assigned_id = decision_id or f"checkpoint-decision-{uuid4()}"
        existing = await session.get(CheckpointDecisionRow, assigned_id)
        if existing is not None:
            stored = CheckpointDecisionRecord.model_validate(existing.record_json, strict=False)
            if (
                stored.checkpoint_id != checkpoint_id
                or stored.action != action
                or stored.to_status != to_status
                or stored.actor != actor
                or stored.reason != reason
                or stored.comparison_id != comparison_id
                or stored.policy_id != policy_id
                or stored.policy_version != policy_version
                or stored.evidence_refs != evidence_refs
            ):
                raise ValueError("checkpoint decision ID conflicts with persisted intent")
            return stored
        checkpoint = await session.scalar(
            select(CheckpointRow)
            .where(CheckpointRow.checkpoint_id == checkpoint_id)
            .with_for_update()
        )
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        timestamp = created_at or datetime.now(UTC)
        record = CheckpointDecisionRecord(
            decision_id=assigned_id,
            checkpoint_id=checkpoint_id,
            action=action,
            from_status=CheckpointStatus(checkpoint.status),
            to_status=to_status,
            actor=actor,
            reason=reason,
            comparison_id=comparison_id,
            policy_id=policy_id,
            policy_version=policy_version,
            evidence_refs=evidence_refs,
            created_at=timestamp,
        )
        payload = record.model_dump(mode="json")
        checkpoint.status = to_status.value
        checkpoint.updated_at = timestamp
        session.add(
            CheckpointDecisionRow(
                decision_id=record.decision_id,
                checkpoint_id=record.checkpoint_id,
                comparison_id=record.comparison_id,
                action=record.action.value,
                from_status=record.from_status.value,
                to_status=record.to_status.value,
                actor=record.actor,
                record_digest=sha256_digest(payload),
                record_json=payload,
                created_at=record.created_at,
            )
        )
        await session.flush()
        return record

    async def _validate_gate_evidence(
        self, session: AsyncSession, record: CheckpointEvaluationRecord
    ) -> None:
        for gate in record.hard_gates:
            if not gate.evidence_refs:
                raise ValueError(f"checkpoint gate has no verifier evidence: {gate.gate_id}")
            for result_id in gate.evidence_refs:
                result = await session.get(VerifierResultRow, result_id)
                if result is None:
                    raise ValueError(f"checkpoint gate cites unknown verifier result: {result_id}")
                if result.record_digest != sha256_digest(result.record_json):
                    raise ValueError(f"checkpoint gate cites corrupt verifier result: {result_id}")
                evidence = VerifierResult.model_validate(result.record_json, strict=False)
                if (
                    evidence.result_id != result.result_id
                    or evidence.disposition.value != result.disposition
                ):
                    raise ValueError(f"checkpoint verifier evidence index is corrupt: {result_id}")
                if result.disposition != gate.disposition.value:
                    raise ValueError("checkpoint gate conflicts with verifier disposition")


def _build_comparison_record(
    *,
    comparison_id: str,
    baseline: CheckpointEvaluationRecord,
    candidate: CheckpointEvaluationRecord,
    policy: CheckpointPromotionPolicy,
    policy_digest: str,
    created_at: datetime,
) -> CheckpointComparisonRecord:
    baseline_metrics = {metric.metric_id: metric for metric in baseline.metrics}
    candidate_metrics = {metric.metric_id: metric for metric in candidate.metrics}
    deltas: list[MetricObservation] = []
    regressions: list[str] = []
    missing_required: list[str] = []
    for rule in policy.metric_rules:
        baseline_metric = baseline_metrics.get(rule.metric_id)
        candidate_metric = candidate_metrics.get(rule.metric_id)
        if (
            baseline_metric is None
            or candidate_metric is None
            or baseline_metric.value is None
            or candidate_metric.value is None
        ):
            reasons = []
            if baseline_metric is None:
                reasons.append("baseline metric absent")
            elif baseline_metric.value is None:
                reasons.append(f"baseline: {baseline_metric.missing_reason}")
            if candidate_metric is None:
                reasons.append("candidate metric absent")
            elif candidate_metric.value is None:
                reasons.append(f"candidate: {candidate_metric.missing_reason}")
            deltas.append(
                MetricObservation(
                    metric_id=rule.metric_id,
                    value=None,
                    evidence_refs=(baseline.evaluation_id, candidate.evaluation_id),
                    missing_reason="; ".join(reasons),
                )
            )
            if rule.required:
                missing_required.append(rule.metric_id)
            continue
        delta = candidate_metric.value - baseline_metric.value
        deltas.append(
            MetricObservation(
                metric_id=rule.metric_id,
                value=delta,
                evidence_refs=(baseline.evaluation_id, candidate.evaluation_id),
            )
        )
        if delta < -rule.max_regression:
            regressions.append(
                f"{rule.metric_id}: delta {delta} exceeds regression tolerance "
                f"{-rule.max_regression}"
            )
        if rule.minimum_delta is not None and delta < rule.minimum_delta:
            regressions.append(
                f"{rule.metric_id}: delta {delta} is below required {rule.minimum_delta}"
            )
    hard_gate_failures = tuple(
        [f"baseline:{gate.gate_id}" for gate in baseline.hard_gates if not gate.passed]
        + [f"candidate:{gate.gate_id}" for gate in candidate.hard_gates if not gate.passed]
    )
    return CheckpointComparisonRecord(
        comparison_id=comparison_id,
        baseline_evaluation_id=baseline.evaluation_id,
        candidate_evaluation_id=candidate.evaluation_id,
        suite_manifest_digest=baseline.suite_manifest_digest,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_digest=policy_digest,
        metric_deltas=tuple(deltas),
        regression_breaches=tuple(regressions),
        hard_gate_failures=hard_gate_failures,
        missing_required_metrics=tuple(missing_required),
        promotion_recommended=not (regressions or hard_gate_failures or missing_required),
        created_at=created_at,
    )


def default_promotion_policy(*, created_at: datetime | None = None) -> CheckpointPromotionPolicy:
    return CheckpointPromotionPolicy(
        policy_id="padawan.checkpoint.lexicographic",
        version="1.0.0",
        description=(
            "Require valid evidence and non-regression before held-out capability, transfer, "
            "retention, and efficiency can support checkpoint promotion."
        ),
        metric_rules=(
            PromotionMetricRule(
                metric_id="held_out_capability", max_regression=0.0, minimum_delta=0.0
            ),
            PromotionMetricRule(metric_id="unseen_transfer", max_regression=0.02),
            PromotionMetricRule(metric_id="delayed_retention", max_regression=0.02),
            PromotionMetricRule(metric_id="non_interference", max_regression=0.0),
            PromotionMetricRule(metric_id="efficiency", required=False, max_regression=0.10),
        ),
        created_at=created_at or datetime.now(UTC),
    )
