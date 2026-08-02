from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import (
    ArtifactBackend,
    ArtifactCatalog,
    artifact_put_bytes,
    artifact_read_bytes,
)
from padawan.domains.contracts import (
    RewardRecord,
    TrainingEligibilityDecision,
    TrainingLane,
    VerifierResult,
)
from padawan.models.contracts import (
    ArtifactRef,
    AttemptRecord,
    CorpusItemRecord,
    CorpusPool,
    DevelopmentalEpisode,
    GradeOutcome,
    GradeRecord,
    ResearchRole,
    RevisionRecord,
    RightsReviewStatus,
    RightsUse,
    TeacherInterventionRecord,
    TransferTrialRecord,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import CheckpointManifest
from padawan.models.tables import (
    ArtifactRow,
    AttemptRow,
    CheckpointRow,
    CorpusItemRow,
    EpisodeRow,
    GradeRow,
    RevisionRow,
    RewardRow,
    TeacherInterventionRow,
    TemplateFamilyRow,
    TrainingBundleRow,
    TrainingEligibilityRow,
    TrainingSourceDecisionRow,
    TrainingSourceDocumentRow,
    TransferTrialRow,
    VerifierResultRow,
)
from padawan.training.contracts import (
    RIGHTS_POLICY_VERSION,
    TRAINING_COMPILER_VERSION,
    CheckpointSourceIdentity,
    CompiledRow,
    CompilerInvocation,
    ContinuedPretrainingRow,
    EvidenceLedgerEntry,
    EvidenceSourceKind,
    NormalizedEpisodeEntry,
    PolicyIdentity,
    PreferenceTrainingRow,
    ProcessTrainingRow,
    RLVRTrainingRow,
    SealedEvaluationRow,
    SFTTrainingRow,
    TrainingBundleManifest,
    TrainingBundleVerification,
    TrainingExclusionReason,
    TrainingExclusionRecord,
    TrainingProductKind,
    TrainingProductManifest,
    TrainingSourceDecision,
    TrainingSourceDocument,
    TrainingSourceStatus,
)


class TrainingCompilationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrainingBundleBuild:
    manifest: TrainingBundleManifest
    manifest_ref: ArtifactRef


@dataclass(frozen=True)
class _CorpusSource:
    record: CorpusItemRecord
    record_digest: str
    rights_digest: str
    visibility_class: str
    lineage_contaminated: bool


@dataclass(frozen=True)
class _EligibilitySource:
    decision: TrainingEligibilityDecision
    reward_id: str
    record_digest: str


@dataclass(frozen=True)
class _CheckpointSource:
    manifest: CheckpointManifest
    manifest_digest: str
    status: str
    evidence_digest: str


@dataclass(frozen=True)
class _DocumentSource:
    document: TrainingSourceDocument
    record_digest: str
    decisions: tuple[tuple[TrainingSourceDecision, str], ...]

    @property
    def current_decision(self) -> TrainingSourceDecision | None:
        if not self.decisions:
            return None
        return max(
            (decision for decision, _digest in self.decisions),
            key=lambda decision: (_utc(decision.created_at), decision.decision_id),
        )


@dataclass(frozen=True)
class _EvidenceSource:
    kind: EvidenceSourceKind
    source_id: str
    record: dict[str, Any]
    record_digest: str
    research_role: ResearchRole | None
    artifact_refs: tuple[ArtifactRef, ...]
    rights_digests: tuple[str, ...]

    @property
    def reference(self) -> str:
        return _source_ref(self.kind, self.source_id)


@dataclass(frozen=True)
class _Snapshot:
    as_of: datetime
    corpus: dict[str, _CorpusSource]
    episodes: dict[str, dict[str, Any]]
    attempts: dict[str, AttemptRecord]
    grades: dict[str, GradeRecord]
    interventions: dict[str, TeacherInterventionRecord]
    revisions: dict[str, RevisionRecord]
    transfers: dict[str, TransferTrialRecord]
    verifiers: dict[str, tuple[VerifierResult, str]]
    rewards: dict[str, tuple[RewardRecord, str]]
    eligibilities: dict[str, _EligibilitySource]
    checkpoints: dict[str, _CheckpointSource]
    documents: dict[str, _DocumentSource]
    evidence_sources: tuple[_EvidenceSource, ...]
    source_snapshot_digest: str


_ROW_MODELS: dict[TrainingProductKind, type[CompiledRow]] = {
    TrainingProductKind.EVIDENCE_LEDGER: EvidenceLedgerEntry,
    TrainingProductKind.NORMALIZED_EPISODES: NormalizedEpisodeEntry,
    TrainingProductKind.SFT: SFTTrainingRow,
    TrainingProductKind.PREFERENCE: PreferenceTrainingRow,
    TrainingProductKind.RLVR: RLVRTrainingRow,
    TrainingProductKind.NEGATIVE_PROCESS: ProcessTrainingRow,
    TrainingProductKind.CONTINUED_PRETRAINING: ContinuedPretrainingRow,
    TrainingProductKind.SEALED_EVALUATION: SealedEvaluationRow,
    TrainingProductKind.EXCLUSIONS: TrainingExclusionRecord,
}

_ROW_SCHEMA_NAMES: dict[TrainingProductKind, str] = {
    TrainingProductKind.EVIDENCE_LEDGER: "evidence-ledger-entry",
    TrainingProductKind.NORMALIZED_EPISODES: "normalized-episode-entry",
    TrainingProductKind.SFT: "sft-training-row",
    TrainingProductKind.PREFERENCE: "preference-training-row",
    TrainingProductKind.RLVR: "rlvr-training-row",
    TrainingProductKind.NEGATIVE_PROCESS: "process-training-row",
    TrainingProductKind.CONTINUED_PRETRAINING: "continued-pretraining-row",
    TrainingProductKind.SEALED_EVALUATION: "sealed-evaluation-row",
    TrainingProductKind.EXCLUSIONS: "training-exclusion",
}

_PRODUCT_LANES: dict[TrainingProductKind, TrainingLane | None] = {
    TrainingProductKind.EVIDENCE_LEDGER: None,
    TrainingProductKind.NORMALIZED_EPISODES: None,
    TrainingProductKind.SFT: TrainingLane.SFT,
    TrainingProductKind.PREFERENCE: TrainingLane.PREFERENCE,
    TrainingProductKind.RLVR: TrainingLane.RLVR,
    TrainingProductKind.NEGATIVE_PROCESS: TrainingLane.PROCESS,
    TrainingProductKind.CONTINUED_PRETRAINING: TrainingLane.CONTINUED_PRETRAINING,
    TrainingProductKind.SEALED_EVALUATION: TrainingLane.EVALUATION_ONLY,
    TrainingProductKind.EXCLUSIONS: None,
}

_RIGHTS_BY_LANE: dict[TrainingLane, RightsUse] = {
    TrainingLane.CONTINUED_PRETRAINING: RightsUse.CONTINUED_PRETRAINING,
    TrainingLane.SFT: RightsUse.SFT,
    TrainingLane.PREFERENCE: RightsUse.PREFERENCE,
    TrainingLane.RLVR: RightsUse.RLVR,
    TrainingLane.PROCESS: RightsUse.PROCESS,
    TrainingLane.EVALUATION_ONLY: RightsUse.EVALUATION,
}


class TrainingCompiler:
    """Compile immutable internal products from a deterministic database snapshot."""

    def __init__(self, backend: ArtifactBackend, catalog: ArtifactCatalog | None = None) -> None:
        self.backend = backend
        self.catalog = catalog or ArtifactCatalog(backend)

    async def compile(
        self,
        session: AsyncSession,
        *,
        as_of: datetime | None = None,
        eligibility_policy_id: str | None = None,
        eligibility_policy_version: str | None = None,
        checkpoint_ids: tuple[str, ...] = (),
    ) -> TrainingBundleBuild:
        resolved_as_of = _utc(as_of) if as_of is not None else await _snapshot_watermark(session)
        invocation = CompilerInvocation(
            compiler_version=TRAINING_COMPILER_VERSION,
            rights_policy_version=RIGHTS_POLICY_VERSION,
            as_of=resolved_as_of,
            eligibility_policy_id=eligibility_policy_id,
            eligibility_policy_version=eligibility_policy_version,
            checkpoint_ids=tuple(sorted(checkpoint_ids)),
        )
        snapshot = await _load_snapshot(session, invocation)
        products = _compile_rows(snapshot, invocation)
        product_manifests: list[TrainingProductManifest] = []
        for kind in sorted(TrainingProductKind, key=lambda candidate: candidate.value):
            rows = tuple(sorted(products[kind], key=lambda row: row.row_id))
            content = _jsonl_bytes(rows)
            reference = await artifact_put_bytes(
                self.backend,
                content,
                media_type="application/x-ndjson; charset=utf-8",
                restricted=True,
                raw_data=True,
            )
            product_manifests.append(
                TrainingProductManifest(
                    kind=kind,
                    training_lane=_PRODUCT_LANES[kind],
                    row_schema=_ROW_SCHEMA_NAMES[kind],
                    artifact=reference,
                    row_count=len(rows),
                    content_digest=reference.digest,
                )
            )

        exclusions = cast(
            Sequence[TrainingExclusionRecord], products[TrainingProductKind.EXCLUSIONS]
        )
        exclusion_counts: Counter[TrainingExclusionReason] = Counter()
        for exclusion in exclusions:
            exclusion_counts.update(exclusion.reason_codes)
        checkpoint_identities = _checkpoint_identities(snapshot)
        eligibility_policies = tuple(
            PolicyIdentity(policy_id=policy_id, policy_version=policy_version)
            for policy_id, policy_version in sorted(
                {
                    (source.decision.policy_id, source.decision.policy_version)
                    for source in snapshot.eligibilities.values()
                    if _policy_selected(source.decision, invocation)
                }
            )
        )
        rights_digests = tuple(
            sorted(
                {source.rights_digest for source in snapshot.corpus.values()}
                | {source.document.rights_digest for source in snapshot.documents.values()}
                | {
                    sha256_digest(attempt.output_rights.model_dump(mode="json"))
                    for attempt in snapshot.attempts.values()
                    if attempt.output_rights is not None
                }
                | {
                    sha256_digest(intervention.output_rights.model_dump(mode="json"))
                    for intervention in snapshot.interventions.values()
                    if intervention.output_rights is not None
                }
            )
        )
        source_artifact_digests = tuple(
            sorted(
                {
                    artifact.digest
                    for source in snapshot.evidence_sources
                    for artifact in source.artifact_refs
                }
            )
        )
        verifier_fingerprints = tuple(
            sorted(
                {
                    sha256_digest(source.record.verifier_spec.model_dump(mode="json"))
                    for source in snapshot.corpus.values()
                }
            )
        )
        environment_fingerprints = tuple(
            sorted({_environment_fingerprint(source.record) for source in snapshot.corpus.values()})
        )
        manifest_body: dict[str, Any] = {
            "schema_version": "1.0.0",
            "compiler_version": TRAINING_COMPILER_VERSION,
            "invocation": invocation.model_dump(mode="json"),
            "source_snapshot_digest": snapshot.source_snapshot_digest,
            "internal_only": True,
            "checkpoint_identities": [
                identity.model_dump(mode="json") for identity in checkpoint_identities
            ],
            "source_episode_ids": tuple(sorted(snapshot.episodes)),
            "source_document_ids": tuple(sorted(snapshot.documents)),
            "source_artifact_digests": source_artifact_digests,
            "verifier_fingerprints": verifier_fingerprints,
            "environment_fingerprints": environment_fingerprints,
            "rights_manifest_digests": rights_digests,
            "eligibility_policies": [
                policy.model_dump(mode="json") for policy in eligibility_policies
            ],
            "products": [product.model_dump(mode="json") for product in product_manifests],
            "included_counts": {
                product.kind.value: product.row_count for product in product_manifests
            },
            "exclusion_counts": {
                reason.value: count
                for reason, count in sorted(
                    exclusion_counts.items(), key=lambda item: item[0].value
                )
            },
            "created_at": invocation.as_of.isoformat(),
        }
        provisional_manifest = TrainingBundleManifest.model_validate(
            {"bundle_id": "pending", **manifest_body}, strict=False
        )
        bundle_id = f"bundle-{sha256_digest(_manifest_identity(provisional_manifest))[7:39]}"
        manifest = provisional_manifest.model_copy(update={"bundle_id": bundle_id})
        manifest_content = canonical_json_bytes(manifest.model_dump(mode="json"))
        manifest_ref = await artifact_put_bytes(
            self.backend,
            manifest_content,
            media_type="application/vnd.padawan.training-bundle+json",
            restricted=True,
            raw_data=True,
        )
        await self.catalog.reference(
            session,
            manifest_ref,
            owner_type="training_bundle_manifest",
            owner_id=bundle_id,
        )
        for product in product_manifests:
            await self.catalog.reference(
                session,
                product.artifact,
                owner_type="training_bundle_product",
                owner_id=f"{bundle_id}:{product.kind.value}",
            )
        payload = manifest.model_dump(mode="json")
        existing = await session.get(TrainingBundleRow, bundle_id)
        if existing is not None:
            if (
                existing.manifest_digest != manifest_ref.digest
                or existing.manifest_artifact_id != manifest_ref.artifact_id
                or existing.record_json != payload
            ):
                raise TrainingCompilationError(
                    "training bundle identity conflicts with persisted manifest"
                )
        else:
            session.add(
                TrainingBundleRow(
                    bundle_id=bundle_id,
                    compiler_version=TRAINING_COMPILER_VERSION,
                    source_snapshot_digest=snapshot.source_snapshot_digest,
                    manifest_digest=manifest_ref.digest,
                    manifest_artifact_id=manifest_ref.artifact_id,
                    internal_only=True,
                    as_of=invocation.as_of,
                    record_json=payload,
                    created_at=invocation.as_of,
                )
            )
            await session.flush()
        return TrainingBundleBuild(manifest=manifest, manifest_ref=manifest_ref)

    async def get_manifest(
        self, session: AsyncSession, *, bundle_id: str
    ) -> TrainingBundleManifest:
        row = await session.get(TrainingBundleRow, bundle_id)
        if row is None:
            raise KeyError(bundle_id)
        return TrainingBundleManifest.model_validate(row.record_json, strict=False)

    async def verify(self, session: AsyncSession, *, bundle_id: str) -> TrainingBundleVerification:
        row = await session.get(TrainingBundleRow, bundle_id)
        if row is None:
            raise KeyError(bundle_id)
        errors: list[str] = []
        checked: list[TrainingProductKind] = []
        try:
            manifest = TrainingBundleManifest.model_validate(row.record_json, strict=False)
        except ValidationError as exc:
            return TrainingBundleVerification(
                bundle_id=bundle_id,
                manifest_digest=row.manifest_digest,
                valid=False,
                checked_products=(),
                errors=(f"invalid manifest contract: {exc}",),
            )
        if manifest.bundle_id != bundle_id:
            errors.append("bundle row identity differs from its manifest")
        if row.compiler_version != manifest.compiler_version:
            errors.append("bundle compiler version differs from its manifest")
        if row.source_snapshot_digest != manifest.source_snapshot_digest:
            errors.append("bundle snapshot digest differs from its manifest")
        if not row.internal_only or not manifest.internal_only:
            errors.append("training bundle is not marked internal-only")
        if _utc(row.as_of) != _utc(manifest.invocation.as_of):
            errors.append("bundle snapshot time differs from its manifest")
        expected_bundle_id = f"bundle-{sha256_digest(_manifest_identity(manifest))[7:39]}"
        if expected_bundle_id != manifest.bundle_id:
            errors.append("bundle identity is not reproducible from its manifest")
        manifest_row = await session.get(ArtifactRow, row.manifest_artifact_id)
        if manifest_row is None:
            errors.append("bundle manifest artifact is missing from the catalog")
        else:
            manifest_ref = _artifact_ref(manifest_row)
            try:
                manifest_content = await artifact_read_bytes(
                    self.backend, manifest_ref, allow_restricted=True
                )
                if manifest_content != canonical_json_bytes(manifest.model_dump(mode="json")):
                    errors.append("bundle manifest artifact differs from its canonical record")
                if row.manifest_digest != manifest_ref.digest:
                    errors.append("bundle manifest digest differs from its catalog artifact")
            except Exception as exc:  # integrity errors are reported as verification evidence
                errors.append(f"bundle manifest artifact failed verification: {exc}")
        exclusion_counter: Counter[TrainingExclusionReason] = Counter()
        for product in manifest.products:
            try:
                product_row = await session.get(ArtifactRow, product.artifact.artifact_id)
                if product_row is None:
                    errors.append(f"{product.kind.value} artifact is missing from the catalog")
                elif _artifact_ref(product_row) != product.artifact:
                    errors.append(
                        f"{product.kind.value} catalog metadata differs from its manifest"
                    )
                content = await artifact_read_bytes(
                    self.backend, product.artifact, allow_restricted=True
                )
                rows = _parse_product_rows(product.kind, content)
                if len(rows) != product.row_count:
                    errors.append(f"{product.kind.value} row count is invalid")
                row_ids = tuple(item.row_id for item in rows)
                if row_ids != tuple(sorted(row_ids)) or len(row_ids) != len(set(row_ids)):
                    errors.append(f"{product.kind.value} rows are not canonically ordered")
                if product.kind == TrainingProductKind.EXCLUSIONS:
                    for item in rows:
                        exclusion = cast(TrainingExclusionRecord, item)
                        exclusion_counter.update(exclusion.reason_codes)
                checked.append(product.kind)
            except Exception as exc:  # verification must enumerate all broken products
                errors.append(f"{product.kind.value} artifact failed verification: {exc}")
        observed_counts = {
            reason: count
            for reason, count in sorted(exclusion_counter.items(), key=lambda item: item[0].value)
        }
        if observed_counts != manifest.exclusion_counts:
            errors.append("exclusion reason counts differ from the exclusion ledger")
        try:
            snapshot = await _load_snapshot(session, manifest.invocation)
            if snapshot.source_snapshot_digest != manifest.source_snapshot_digest:
                errors.append("source snapshot no longer reproduces the bundle digest")
        except Exception as exc:
            errors.append(f"source snapshot failed verification: {exc}")
        return TrainingBundleVerification(
            bundle_id=bundle_id,
            manifest_digest=row.manifest_digest,
            valid=not errors,
            checked_products=tuple(sorted(set(checked), key=lambda kind: kind.value)),
            errors=tuple(errors),
        )


async def _load_snapshot(session: AsyncSession, invocation: CompilerInvocation) -> _Snapshot:
    as_of = invocation.as_of
    corpus_rows = (
        await session.scalars(
            select(CorpusItemRow)
            .where(CorpusItemRow.created_at <= as_of)
            .order_by(CorpusItemRow.item_id)
        )
    ).all()
    family_rows = (
        await session.scalars(
            select(TemplateFamilyRow)
            .where(TemplateFamilyRow.created_at <= as_of)
            .order_by(TemplateFamilyRow.template_family_id)
        )
    ).all()
    contaminated_families = {
        family_row.template_family_id for family_row in family_rows if family_row.contaminated
    }
    corpus: dict[str, _CorpusSource] = {}
    for corpus_row in corpus_rows:
        corpus_record = _corpus_record(corpus_row)
        rights_digest = sha256_digest(corpus_record.rights.model_dump(mode="json"))
        if corpus_row.rights_digest != rights_digest:
            raise TrainingCompilationError(f"corpus rights digest is invalid: {corpus_row.item_id}")
        payload = {
            **corpus_record.model_dump(mode="json"),
            "visibility_class": corpus_row.visibility_class,
            "lineage_contaminated": (corpus_row.template_family_id in contaminated_families),
            "rights_digest": rights_digest,
        }
        corpus[corpus_row.item_id] = _CorpusSource(
            record=corpus_record,
            record_digest=sha256_digest(payload),
            rights_digest=rights_digest,
            visibility_class=corpus_row.visibility_class,
            lineage_contaminated=(corpus_row.template_family_id in contaminated_families),
        )

    episode_rows = (
        await session.scalars(
            select(EpisodeRow).where(EpisodeRow.created_at <= as_of).order_by(EpisodeRow.episode_id)
        )
    ).all()
    episodes: dict[str, dict[str, Any]] = {}
    for episode_row in episode_rows:
        if episode_row.record_json:
            episode_record = DevelopmentalEpisode.model_validate(
                episode_row.record_json, strict=False
            )
            episode_payload = episode_record.model_dump(mode="json")
        else:
            episode_payload = {
                "episode_id": episode_row.episode_id,
                "student_id": episode_row.student_id,
                "state_before_id": episode_row.state_before_id,
                "state_after_id": episode_row.state_after_id,
                "item_id": episode_row.item_id,
                "status": episode_row.status,
                "created_at": _utc(episode_row.created_at).isoformat(),
                "completed_at": (
                    _utc(episode_row.completed_at).isoformat()
                    if episode_row.completed_at is not None
                    else None
                ),
            }
        episodes[episode_row.episode_id] = episode_payload

    attempt_rows = (
        await session.scalars(
            select(AttemptRow).where(AttemptRow.created_at <= as_of).order_by(AttemptRow.attempt_id)
        )
    ).all()
    attempts = {
        row.attempt_id: AttemptRecord.model_validate(row.record_json, strict=False)
        for row in attempt_rows
        if not invocation.checkpoint_ids
        or cast(str, row.record_json.get("checkpoint_id")) in invocation.checkpoint_ids
    }
    included_episode_ids = {attempt.episode_id for attempt in attempts.values()}
    if invocation.checkpoint_ids:
        episodes = {
            episode_id: record
            for episode_id, record in episodes.items()
            if episode_id in included_episode_ids
        }

    grade_rows = (
        await session.scalars(
            select(GradeRow).where(GradeRow.created_at <= as_of).order_by(GradeRow.grade_id)
        )
    ).all()
    grades = {
        row.grade_id: GradeRecord.model_validate(row.record_json, strict=False)
        for row in grade_rows
        if row.attempt_id in attempts
    }
    intervention_rows = (
        await session.scalars(
            select(TeacherInterventionRow)
            .where(TeacherInterventionRow.created_at <= as_of)
            .order_by(TeacherInterventionRow.intervention_id)
        )
    ).all()
    interventions = {
        row.intervention_id: TeacherInterventionRecord.model_validate(row.record_json, strict=False)
        for row in intervention_rows
        if row.attempt_id in attempts
    }
    revision_rows = (
        await session.scalars(
            select(RevisionRow)
            .where(RevisionRow.created_at <= as_of)
            .order_by(RevisionRow.revision_id)
        )
    ).all()
    revisions = {
        row.revision_id: RevisionRecord(
            revision_id=row.revision_id,
            original_attempt_id=row.original_attempt_id,
            intervention_id=row.intervention_id,
            revised_attempt_id=row.revised_attempt_id,
            revised_grade_id=row.revised_grade_id,
            created_at=_utc(row.created_at),
        )
        for row in revision_rows
        if row.original_attempt_id in attempts or row.revised_attempt_id in attempts
    }
    transfer_rows = (
        await session.scalars(
            select(TransferTrialRow)
            .where(TransferTrialRow.created_at <= as_of)
            .order_by(TransferTrialRow.transfer_trial_id)
        )
    ).all()
    transfers = {
        row.transfer_trial_id: TransferTrialRecord.model_validate(row.record_json, strict=False)
        for row in transfer_rows
        if row.episode_id in episodes
    }

    verifier_rows = (
        await session.scalars(
            select(VerifierResultRow)
            .where(VerifierResultRow.created_at <= as_of)
            .order_by(VerifierResultRow.result_id)
        )
    ).all()
    verifiers: dict[str, tuple[VerifierResult, str]] = {}
    for verifier_row in verifier_rows:
        verifier_record = VerifierResult.model_validate(verifier_row.record_json, strict=False)
        if verifier_row.record_digest != sha256_digest(verifier_record.model_dump(mode="json")):
            raise TrainingCompilationError(
                f"verifier result digest is invalid: {verifier_row.result_id}"
            )
        verifiers[verifier_row.result_id] = (
            verifier_record,
            verifier_row.record_digest,
        )

    reward_rows = (
        await session.scalars(
            select(RewardRow).where(RewardRow.created_at <= as_of).order_by(RewardRow.reward_id)
        )
    ).all()
    rewards: dict[str, tuple[RewardRecord, str]] = {}
    for reward_row in reward_rows:
        reward_record = RewardRecord.model_validate(reward_row.record_json, strict=False)
        if reward_row.record_digest != sha256_digest(reward_record.model_dump(mode="json")):
            raise TrainingCompilationError(
                f"reward record digest is invalid: {reward_row.reward_id}"
            )
        rewards[reward_row.reward_id] = (reward_record, reward_row.record_digest)

    eligibility_rows = (
        await session.scalars(
            select(TrainingEligibilityRow)
            .where(TrainingEligibilityRow.created_at <= as_of)
            .order_by(TrainingEligibilityRow.decision_id)
        )
    ).all()
    eligibilities: dict[str, _EligibilitySource] = {}
    for eligibility_row in eligibility_rows:
        eligibility_decision = TrainingEligibilityDecision.model_validate(
            eligibility_row.record_json, strict=False
        )
        eligibility_digest = sha256_digest(
            {
                "reward_id": eligibility_row.reward_id,
                "decision": eligibility_row.record_json,
            }
        )
        if eligibility_row.record_digest != eligibility_digest:
            raise TrainingCompilationError(
                f"training eligibility digest is invalid: {eligibility_row.decision_id}"
            )
        if eligibility_row.reward_id not in rewards:
            raise TrainingCompilationError(
                f"training eligibility reward is missing: {eligibility_row.decision_id}"
            )
        eligibilities[eligibility_row.decision_id] = _EligibilitySource(
            decision=eligibility_decision,
            reward_id=eligibility_row.reward_id,
            record_digest=eligibility_row.record_digest,
        )

    checkpoint_rows = (
        await session.scalars(
            select(CheckpointRow)
            .where(CheckpointRow.created_at <= as_of)
            .order_by(CheckpointRow.checkpoint_id)
        )
    ).all()
    checkpoints: dict[str, _CheckpointSource] = {}
    for checkpoint_row in checkpoint_rows:
        checkpoint_manifest = CheckpointManifest.model_validate(
            checkpoint_row.record_json, strict=False
        )
        if checkpoint_row.manifest_digest != sha256_digest(
            checkpoint_manifest.model_dump(mode="json")
        ):
            raise TrainingCompilationError(
                f"checkpoint manifest digest is invalid: {checkpoint_row.checkpoint_id}"
            )
        checkpoint_evidence = {
            "manifest": checkpoint_manifest.model_dump(mode="json"),
            "status": checkpoint_row.status,
        }
        checkpoints[checkpoint_row.checkpoint_id] = _CheckpointSource(
            manifest=checkpoint_manifest,
            manifest_digest=checkpoint_row.manifest_digest,
            status=checkpoint_row.status,
            evidence_digest=sha256_digest(checkpoint_evidence),
        )

    source_rows = (
        await session.scalars(
            select(TrainingSourceDocumentRow)
            .where(TrainingSourceDocumentRow.created_at <= as_of)
            .order_by(TrainingSourceDocumentRow.document_id)
        )
    ).all()
    decision_rows = (
        await session.scalars(
            select(TrainingSourceDecisionRow)
            .where(TrainingSourceDecisionRow.created_at <= as_of)
            .order_by(
                TrainingSourceDecisionRow.document_id,
                TrainingSourceDecisionRow.created_at,
                TrainingSourceDecisionRow.decision_id,
            )
        )
    ).all()
    decisions_by_document: dict[str, list[tuple[TrainingSourceDecision, str]]] = defaultdict(list)
    for source_decision_row in decision_rows:
        source_decision = TrainingSourceDecision.model_validate(
            source_decision_row.record_json, strict=False
        )
        source_decision_digest = sha256_digest(source_decision.model_dump(mode="json"))
        if source_decision_row.record_digest != source_decision_digest:
            raise TrainingCompilationError(
                f"training source decision digest is invalid: {source_decision_row.decision_id}"
            )
        decisions_by_document[source_decision_row.document_id].append(
            (source_decision, source_decision_digest)
        )
    documents: dict[str, _DocumentSource] = {}
    for source_row in source_rows:
        source_document = TrainingSourceDocument.model_validate(
            source_row.record_json, strict=False
        )
        source_digest = sha256_digest(source_document.model_dump(mode="json"))
        if (
            source_row.record_digest != source_digest
            or source_row.rights_digest != source_document.rights_digest
        ):
            raise TrainingCompilationError(
                f"training source document digest is invalid: {source_row.document_id}"
            )
        documents[source_row.document_id] = _DocumentSource(
            document=source_document,
            record_digest=source_digest,
            decisions=tuple(decisions_by_document.get(source_row.document_id, ())),
        )

    provisional = _Snapshot(
        as_of=as_of,
        corpus=corpus,
        episodes=episodes,
        attempts=attempts,
        grades=grades,
        interventions=interventions,
        revisions=revisions,
        transfers=transfers,
        verifiers=verifiers,
        rewards=rewards,
        eligibilities=eligibilities,
        checkpoints=checkpoints,
        documents=documents,
        evidence_sources=(),
        source_snapshot_digest=sha256_digest([]),
    )
    evidence_sources = _evidence_sources(provisional)
    snapshot_digest = sha256_digest(
        [
            {"source_ref": source.reference, "record_digest": source.record_digest}
            for source in evidence_sources
        ]
    )
    return _Snapshot(
        **{
            **provisional.__dict__,
            "evidence_sources": evidence_sources,
            "source_snapshot_digest": snapshot_digest,
        }
    )


def _compile_rows(
    snapshot: _Snapshot, invocation: CompilerInvocation
) -> dict[TrainingProductKind, list[CompiledRow]]:
    products: dict[TrainingProductKind, list[CompiledRow]] = {
        kind: [] for kind in TrainingProductKind
    }
    products[TrainingProductKind.EVIDENCE_LEDGER].extend(_evidence_rows(snapshot))
    products[TrainingProductKind.NORMALIZED_EPISODES].extend(_normalized_episode_rows(snapshot))
    exclusions: list[TrainingExclusionRecord] = []
    teacher_influenced = _teacher_influenced_attempt_ids(snapshot)
    grades_by_attempt = {grade.attempt_id: grade for grade in snapshot.grades.values()}

    included_preference_attempts: set[str] = set()
    preference_groups: dict[tuple[str, str], list[AttemptRecord]] = defaultdict(list)
    for attempt in snapshot.attempts.values():
        if (
            attempt.research_role == ResearchRole.TARGET
            and attempt.attempt_id not in teacher_influenced
        ):
            preference_groups[(attempt.item_id, attempt.rendered_prompt)].append(attempt)
    for (_item_id, _prompt), candidates in sorted(preference_groups.items()):
        graded: list[tuple[AttemptRecord, GradeRecord]] = []
        for candidate_attempt in candidates:
            candidate_grade = grades_by_attempt.get(candidate_attempt.attempt_id)
            if candidate_grade is not None:
                graded.append((candidate_attempt, candidate_grade))
        if len(graded) < 2:
            continue
        ordered = sorted(
            graded,
            key=lambda pair: (pair[1].score, pair[0].attempt_id),
        )
        rejected_attempt, rejected_grade = ordered[0]
        chosen_attempt, chosen_grade = ordered[-1]
        if chosen_grade.score <= rejected_grade.score:
            continue
        pair = _compile_preference_pair(
            snapshot,
            invocation,
            chosen_attempt,
            chosen_grade,
            rejected_attempt,
            rejected_grade,
        )
        if isinstance(pair, TrainingExclusionRecord):
            exclusions.append(pair)
        else:
            products[TrainingProductKind.PREFERENCE].append(pair)
            included_preference_attempts.update(
                (chosen_attempt.attempt_id, rejected_attempt.attempt_id)
            )

    for attempt in sorted(snapshot.attempts.values(), key=lambda item: item.attempt_id):
        grade = grades_by_attempt.get(attempt.attempt_id)
        for kind, lane in (
            (TrainingProductKind.SFT, TrainingLane.SFT),
            (TrainingProductKind.RLVR, TrainingLane.RLVR),
            (TrainingProductKind.NEGATIVE_PROCESS, TrainingLane.PROCESS),
        ):
            reasons = _attempt_reasons(
                snapshot,
                invocation,
                attempt,
                grade,
                lane=lane,
                teacher_influenced=attempt.attempt_id in teacher_influenced,
            )
            if (
                kind == TrainingProductKind.SFT
                and not reasons
                and (
                    grade is None
                    or not grade.deterministic
                    or grade.outcome != GradeOutcome.CORRECT
                    or grade.infrastructure_failure
                    or grade.student_failure
                    or attempt.final_answer is None
                )
            ):
                reasons[TrainingExclusionReason.UNSUCCESSFUL_SFT_TRAJECTORY] = (
                    "SFT requires a deterministic, successful target grade and final answer"
                )
            if (
                kind == TrainingProductKind.RLVR
                and not reasons
                and attempt.item_id not in snapshot.corpus
            ):
                reasons[TrainingExclusionReason.MISSING_RLVR_ENVIRONMENT] = (
                    "RLVR requires a governed task and verifier environment"
                )
            if reasons:
                exclusions.append(
                    _attempt_exclusion(
                        snapshot,
                        invocation,
                        attempt,
                        grade,
                        kind,
                        lane,
                        reasons,
                    )
                )
                continue
            decisions = _allowed_decisions(snapshot, invocation, attempt, grade, lane)
            rewards = _reward_records(snapshot, decisions)
            if kind == TrainingProductKind.SFT:
                products[kind].append(
                    _sft_row(snapshot, attempt, cast(GradeRecord, grade), decisions, rewards)
                )
            elif kind == TrainingProductKind.RLVR:
                products[kind].append(_rlvr_row(snapshot, attempt, grade, decisions, rewards))
            else:
                products[kind].append(_process_row(snapshot, attempt, grade, decisions))
        if attempt.attempt_id not in included_preference_attempts:
            preference_reasons = _attempt_reasons(
                snapshot,
                invocation,
                attempt,
                grade,
                lane=TrainingLane.PREFERENCE,
                teacher_influenced=attempt.attempt_id in teacher_influenced,
            )
            if not preference_reasons:
                preference_reasons[TrainingExclusionReason.NO_STRONGER_PREFERENCE_PAIR] = (
                    "no same-prompt target output pair has a strictly stronger verified score"
                )
            exclusions.append(
                _attempt_exclusion(
                    snapshot,
                    invocation,
                    attempt,
                    grade,
                    TrainingProductKind.PREFERENCE,
                    TrainingLane.PREFERENCE,
                    preference_reasons,
                )
            )
        exclusions.append(
            _attempt_exclusion(
                snapshot,
                invocation,
                attempt,
                grade,
                TrainingProductKind.CONTINUED_PRETRAINING,
                TrainingLane.CONTINUED_PRETRAINING,
                {
                    TrainingExclusionReason.EPISODE_NOT_SOURCE_CORPUS: (
                        "episode outputs are retained as evidence but are not admitted source "
                        "documents for continued pretraining"
                    )
                },
            )
        )
        if attempt.private_reasoning_ref is not None:
            exclusions.append(
                _attempt_exclusion(
                    snapshot,
                    invocation,
                    attempt,
                    grade,
                    TrainingProductKind.NEGATIVE_PROCESS,
                    TrainingLane.PROCESS,
                    {
                        TrainingExclusionReason.PRIVATE_REASONING_RESTRICTED: (
                            "private reasoning remains a restricted evidence artifact and is "
                            "never materialized into a training row"
                        )
                    },
                    candidate_id=f"{attempt.attempt_id}:private_reasoning",
                )
            )

    for intervention in sorted(
        snapshot.interventions.values(), key=lambda item: item.intervention_id
    ):
        lineage = _intervention_lineage(snapshot, intervention)
        for kind, lane in (
            (TrainingProductKind.SFT, TrainingLane.SFT),
            (TrainingProductKind.PREFERENCE, TrainingLane.PREFERENCE),
            (TrainingProductKind.RLVR, TrainingLane.RLVR),
            (TrainingProductKind.NEGATIVE_PROCESS, TrainingLane.PROCESS),
            (TrainingProductKind.CONTINUED_PRETRAINING, TrainingLane.CONTINUED_PRETRAINING),
        ):
            exclusions.append(
                _exclusion(
                    candidate_id=intervention.intervention_id,
                    candidate_kind="teacher_intervention",
                    product_kind=kind,
                    lane=lane,
                    reasons={
                        TrainingExclusionReason.TEACHER_OUTPUT: (
                            "teacher output is retained in evidence and normalized episodes but "
                            "excluded from target training products by default"
                        )
                    },
                    lineage=lineage,
                )
            )

    sealed_rows, sealed_exclusions = _sealed_evaluation_rows(snapshot)
    products[TrainingProductKind.SEALED_EVALUATION].extend(sealed_rows)
    exclusions.extend(sealed_exclusions)
    source_rows, source_exclusions = _continued_pretraining_rows(snapshot)
    products[TrainingProductKind.CONTINUED_PRETRAINING].extend(source_rows)
    exclusions.extend(source_exclusions)
    products[TrainingProductKind.EXCLUSIONS].extend(exclusions)
    return products


def _evidence_rows(snapshot: _Snapshot) -> list[EvidenceLedgerEntry]:
    rows: list[EvidenceLedgerEntry] = []
    for source in snapshot.evidence_sources:
        identity = {
            "kind": source.kind.value,
            "source_id": source.source_id,
            "record_digest": source.record_digest,
        }
        rows.append(
            EvidenceLedgerEntry(
                row_id=_row_id("evidence", identity),
                source_evidence_refs=(source.reference,),
                source_record_digests=(source.record_digest,),
                rights_digests=source.rights_digests,
                source_kind=source.kind,
                source_id=source.source_id,
                research_role=source.research_role,
                record=source.record,
                artifact_refs=source.artifact_refs,
            )
        )
    return rows


def _normalized_episode_rows(snapshot: _Snapshot) -> list[NormalizedEpisodeEntry]:
    rows: list[NormalizedEpisodeEntry] = []
    sources_by_ref = {source.reference: source for source in snapshot.evidence_sources}
    for episode_id, episode in sorted(snapshot.episodes.items()):
        item_id = str(episode.get("task_item_id") or episode.get("item_id") or "")
        corpus = snapshot.corpus.get(item_id)
        if corpus is None:
            raise TrainingCompilationError(f"episode has no governed corpus item: {episode_id}")
        attempts = sorted(
            (attempt for attempt in snapshot.attempts.values() if attempt.episode_id == episode_id),
            key=lambda item: item.attempt_id,
        )
        attempt_ids = {attempt.attempt_id for attempt in attempts}
        grades = sorted(
            (grade for grade in snapshot.grades.values() if grade.attempt_id in attempt_ids),
            key=lambda item: item.grade_id,
        )
        interventions = sorted(
            (
                intervention
                for intervention in snapshot.interventions.values()
                if intervention.episode_id == episode_id
            ),
            key=lambda item: item.intervention_id,
        )
        intervention_ids = {item.intervention_id for item in interventions}
        revisions = sorted(
            (
                revision
                for revision in snapshot.revisions.values()
                if revision.intervention_id in intervention_ids
                or revision.original_attempt_id in attempt_ids
                or revision.revised_attempt_id in attempt_ids
            ),
            key=lambda item: item.revision_id,
        )
        transfers = sorted(
            (
                transfer
                for transfer in snapshot.transfers.values()
                if transfer.treatment_attempt_id in attempt_ids
                or transfer.control_attempt_id in attempt_ids
            ),
            key=lambda item: item.transfer_trial_id,
        )
        candidate_refs = {episode_id, item_id, *attempt_ids, *[grade.grade_id for grade in grades]}
        eligibility = sorted(
            (
                source
                for source in snapshot.eligibilities.values()
                if _eligibility_subjects(snapshot, source).intersection(candidate_refs)
            ),
            key=lambda item: item.decision.decision_id,
        )
        reward_ids = {source.reward_id for source in eligibility}
        rewards = [snapshot.rewards[reward_id][0] for reward_id in sorted(reward_ids)]
        evidence_refs = {
            _source_ref(EvidenceSourceKind.CORPUS_ITEM, item_id),
            _source_ref(EvidenceSourceKind.EPISODE, episode_id),
            *[_source_ref(EvidenceSourceKind.ATTEMPT, attempt.attempt_id) for attempt in attempts],
            *[_source_ref(EvidenceSourceKind.GRADE, grade.grade_id) for grade in grades],
            *[
                _source_ref(EvidenceSourceKind.TEACHER_INTERVENTION, intervention.intervention_id)
                for intervention in interventions
            ],
            *[
                _source_ref(EvidenceSourceKind.REVISION, revision.revision_id)
                for revision in revisions
            ],
            *[
                _source_ref(EvidenceSourceKind.TRANSFER_TRIAL, transfer.transfer_trial_id)
                for transfer in transfers
            ],
            *[_source_ref(EvidenceSourceKind.REWARD, reward.reward_id) for reward in rewards],
            *[
                _source_ref(
                    EvidenceSourceKind.TRAINING_ELIGIBILITY,
                    source.decision.decision_id,
                )
                for source in eligibility
            ],
        }
        selected_sources = [sources_by_ref[reference] for reference in sorted(evidence_refs)]
        record_digests = tuple(sorted({source.record_digest for source in selected_sources}))
        roles = tuple(
            sorted(
                {attempt.research_role for attempt in attempts},
                key=lambda role: role.value,
            )
        )
        rows.append(
            NormalizedEpisodeEntry(
                row_id=_row_id(
                    "episode",
                    {
                        "episode_id": episode_id,
                        "source_record_digests": record_digests,
                    },
                ),
                source_evidence_refs=tuple(sorted(evidence_refs)),
                source_record_digests=record_digests,
                rights_digests=tuple(
                    sorted(
                        {corpus.rights_digest}
                        | {
                            sha256_digest(attempt.output_rights.model_dump(mode="json"))
                            for attempt in attempts
                            if attempt.output_rights is not None
                        }
                        | {
                            sha256_digest(intervention.output_rights.model_dump(mode="json"))
                            for intervention in interventions
                            if intervention.output_rights is not None
                        }
                    )
                ),
                episode_id=episode_id,
                item_id=item_id,
                research_roles=roles,
                corpus_item=corpus.record.model_dump(mode="json"),
                episode=episode,
                attempts=tuple(attempt.model_dump(mode="json") for attempt in attempts),
                grades=tuple(grade.model_dump(mode="json") for grade in grades),
                teacher_interventions=tuple(
                    intervention.model_dump(mode="json") for intervention in interventions
                ),
                revisions=tuple(revision.model_dump(mode="json") for revision in revisions),
                transfer_trials=tuple(transfer.model_dump(mode="json") for transfer in transfers),
                rewards=tuple(reward.model_dump(mode="json") for reward in rewards),
                eligibility_decisions=tuple(
                    source.decision.model_dump(mode="json") for source in eligibility
                ),
            )
        )
    return rows


def _attempt_reasons(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    *,
    lane: TrainingLane,
    teacher_influenced: bool,
) -> dict[TrainingExclusionReason, str]:
    if attempt.research_role != ResearchRole.TARGET:
        reason = {
            ResearchRole.BASELINE: TrainingExclusionReason.BASELINE_OUTPUT,
            ResearchRole.TEACHER: TrainingExclusionReason.TEACHER_OUTPUT,
        }.get(attempt.research_role, TrainingExclusionReason.NON_TARGET_OUTPUT)
        return {
            reason: (
                f"{attempt.research_role.value} output is maintained as evidence but excluded "
                "from target training products by default"
            )
        }
    if teacher_influenced:
        return {
            TrainingExclusionReason.TEACHER_INFLUENCED: (
                "the target prompt directly follows a teacher intervention; a teacherless "
                "successful replay is required for default target-training admission"
            )
        }
    source = snapshot.corpus.get(attempt.item_id)
    if source is None:
        return {TrainingExclusionReason.QUARANTINED_SOURCE: "attempt has no governed corpus source"}
    reasons: dict[TrainingExclusionReason, str] = {}
    if source.record.pool == CorpusPool.SEALED_ANCHOR:
        reasons[TrainingExclusionReason.SEALED_SOURCE] = (
            "sealed anchors are structurally evaluation-only"
        )
    elif source.record.pool == CorpusPool.ROTATING_SHADOW:
        reasons[TrainingExclusionReason.EVALUATION_SOURCE] = (
            "rotating-shadow items are evaluation-only"
        )
    elif source.record.pool == CorpusPool.QUARANTINE or source.record.status.value == "quarantined":
        reasons[TrainingExclusionReason.QUARANTINED_SOURCE] = (
            "quarantined items cannot enter training products"
        )
    if source.lineage_contaminated:
        reasons[TrainingExclusionReason.CONTAMINATED_LINEAGE] = (
            "the corpus template lineage is marked contaminated"
        )
    rights_use = _RIGHTS_BY_LANE[lane]
    if source.record.rights.review_status != RightsReviewStatus.CONFIRMED:
        reasons[TrainingExclusionReason.RIGHTS_REVIEW_REQUIRED] = (
            "source rights are not confirmed for training use"
        )
    elif not source.record.rights.permits(rights_use):
        reasons[TrainingExclusionReason.RIGHTS_USE_NOT_PERMITTED] = (
            f"source rights do not permit {rights_use.value}"
        )
    if attempt.output_rights is None:
        reasons[TrainingExclusionReason.OUTPUT_RIGHTS_MISSING] = (
            "attempt has no versioned output-rights declaration"
        )
    elif attempt.output_rights.review_status != RightsReviewStatus.CONFIRMED:
        reasons[TrainingExclusionReason.OUTPUT_RIGHTS_REVIEW_REQUIRED] = (
            "attempt output rights are not confirmed for target training"
        )
    elif not attempt.output_rights.permits(rights_use):
        reasons[TrainingExclusionReason.OUTPUT_RIGHTS_USE_NOT_PERMITTED] = (
            f"attempt output rights do not permit {rights_use.value}"
        )
    checkpoint = snapshot.checkpoints.get(attempt.checkpoint_id)
    if checkpoint is None:
        reasons[TrainingExclusionReason.MISSING_CHECKPOINT_MANIFEST] = (
            "training products require a registered model and tokenizer manifest"
        )
    elif checkpoint.manifest.model_id != attempt.model_id:
        reasons[TrainingExclusionReason.CHECKPOINT_IDENTITY_CONFLICT] = (
            "attempt model identity differs from its registered checkpoint manifest"
        )
    if (grade is None or not grade.deterministic) and lane in {
        TrainingLane.SFT,
        TrainingLane.PREFERENCE,
    }:
        reasons[TrainingExclusionReason.MISSING_VALID_GRADE] = (
            "this training lane requires a persisted deterministic grade"
        )
    eligibility_reasons = _eligibility_reasons(snapshot, invocation, attempt, grade, lane)
    reasons.update(eligibility_reasons)
    return reasons


def _eligibility_reasons(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    lane: TrainingLane,
) -> dict[TrainingExclusionReason, str]:
    decisions = _candidate_decisions(snapshot, invocation, attempt, grade)
    if not decisions:
        return {
            TrainingExclusionReason.MISSING_ELIGIBILITY_DECISION: (
                "no explicit eligibility decision is scoped to this candidate"
            )
        }
    allowed = [source for source in decisions if lane in source.decision.allowed_lanes]
    explicitly_excluded = [source for source in decisions if lane in source.decision.excluded_lanes]
    if allowed and explicitly_excluded:
        return {
            TrainingExclusionReason.ELIGIBILITY_CONFLICT: (
                "selected eligibility evidence both allows and excludes this lane"
            )
        }
    if not allowed:
        details = sorted({source.decision.excluded_lanes[lane] for source in explicitly_excluded})
        detail = "; ".join(details) if details else "lane was not expressly allowed"
        return {TrainingExclusionReason.ELIGIBILITY_LANE_EXCLUDED: detail}
    failed_rewards = [
        source.reward_id
        for source in allowed
        if any(not gate.passed for gate in snapshot.rewards[source.reward_id][0].hard_gates)
    ]
    if failed_rewards:
        return {
            TrainingExclusionReason.HARD_GATE_FAILED: (
                "eligibility cites reward evidence with a failed hard gate: "
                + ", ".join(sorted(failed_rewards))
            )
        }
    return {}


def _candidate_decisions(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
) -> tuple[_EligibilitySource, ...]:
    candidate_refs = {attempt.attempt_id, attempt.episode_id, attempt.item_id}
    if grade is not None:
        candidate_refs.add(grade.grade_id)
    return tuple(
        sorted(
            (
                source
                for source in snapshot.eligibilities.values()
                if _policy_selected(source.decision, invocation)
                and _eligibility_subjects(snapshot, source).intersection(candidate_refs)
            ),
            key=lambda source: source.decision.decision_id,
        )
    )


def _allowed_decisions(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    lane: TrainingLane,
) -> tuple[_EligibilitySource, ...]:
    return tuple(
        source
        for source in _candidate_decisions(snapshot, invocation, attempt, grade)
        if lane in source.decision.allowed_lanes
    )


def _eligibility_subjects(snapshot: _Snapshot, source: _EligibilitySource) -> set[str]:
    if source.decision.subject_refs:
        return set(source.decision.subject_refs)
    reward = snapshot.rewards[source.reward_id][0]
    subjects = set(source.decision.evidence_refs)
    for gate in reward.hard_gates:
        subjects.update(gate.evidence_refs)
    for component in reward.components:
        subjects.update(component.evidence_refs)
    for result_id in tuple(subjects):
        verifier = snapshot.verifiers.get(result_id)
        if verifier is not None:
            subjects.add(verifier[0].scope)
    return subjects


def _policy_selected(decision: TrainingEligibilityDecision, invocation: CompilerInvocation) -> bool:
    if invocation.eligibility_policy_id is None:
        return True
    return (
        decision.policy_id == invocation.eligibility_policy_id
        and decision.policy_version == invocation.eligibility_policy_version
    )


def _sft_row(
    snapshot: _Snapshot,
    attempt: AttemptRecord,
    grade: GradeRecord,
    decisions: tuple[_EligibilitySource, ...],
    rewards: tuple[RewardRecord, ...],
) -> SFTTrainingRow:
    lineage = _attempt_lineage(snapshot, attempt, grade, decisions)
    decision_ids = tuple(sorted(source.decision.decision_id for source in decisions))
    identity = {
        "attempt_id": attempt.attempt_id,
        "decision_ids": decision_ids,
        "lineage": lineage[1],
    }
    return SFTTrainingRow(
        row_id=_row_id("sft", identity),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        episode_id=attempt.episode_id,
        attempt_id=attempt.attempt_id,
        checkpoint_id=attempt.checkpoint_id,
        messages=attempt.rendered_messages,
        prompt=attempt.rendered_prompt,
        public_derivation=attempt.public_derivation,
        final_answer=cast(dict[str, Any] | str, attempt.final_answer),
        grade=grade.model_dump(mode="json"),
        reward_records=tuple(reward.model_dump(mode="json") for reward in rewards),
        eligibility_decision_ids=decision_ids,
    )


def _rlvr_row(
    snapshot: _Snapshot,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    decisions: tuple[_EligibilitySource, ...],
    rewards: tuple[RewardRecord, ...],
) -> RLVRTrainingRow:
    source = snapshot.corpus[attempt.item_id]
    lineage = _attempt_lineage(snapshot, attempt, grade, decisions)
    decision_ids = tuple(sorted(item.decision.decision_id for item in decisions))
    environment_fingerprint = _environment_fingerprint(source.record)
    return RLVRTrainingRow(
        row_id=_row_id(
            "rlvr",
            {
                "attempt_id": attempt.attempt_id,
                "environment_fingerprint": environment_fingerprint,
                "decision_ids": decision_ids,
            },
        ),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        episode_id=attempt.episode_id,
        attempt_id=attempt.attempt_id,
        checkpoint_id=attempt.checkpoint_id,
        task={
            "item_id": source.record.item_id,
            "competency_id": source.record.competency_id,
            "prompt": source.record.prompt,
            "expected_answer": source.record.expected_answer,
            "pool": source.record.pool.value,
            "lineage": {
                "template_family_id": source.record.template_family_id,
                "instance_group_id": source.record.instance_group_id,
                "generator_version": source.record.generator_version,
            },
        },
        environment_fingerprint=environment_fingerprint,
        verifier_spec=source.record.verifier_spec.model_dump(mode="json"),
        observed_output=attempt.final_answer,
        grade=grade.model_dump(mode="json") if grade is not None else None,
        reward_records=tuple(reward.model_dump(mode="json") for reward in rewards),
        eligibility_decision_ids=decision_ids,
    )


def _process_row(
    snapshot: _Snapshot,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    decisions: tuple[_EligibilitySource, ...],
) -> ProcessTrainingRow:
    lineage = _attempt_lineage(snapshot, attempt, grade, decisions)
    decision_ids = tuple(sorted(item.decision.decision_id for item in decisions))
    outcome_class = grade.outcome.value if grade is not None else "ungraded"
    return ProcessTrainingRow(
        row_id=_row_id(
            "process",
            {
                "attempt_id": attempt.attempt_id,
                "outcome_class": outcome_class,
                "decision_ids": decision_ids,
            },
        ),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        episode_id=attempt.episode_id,
        attempt_id=attempt.attempt_id,
        checkpoint_id=attempt.checkpoint_id,
        prompt=attempt.rendered_prompt,
        public_derivation=attempt.public_derivation,
        final_answer=attempt.final_answer,
        grade=grade.model_dump(mode="json") if grade is not None else None,
        outcome_class=outcome_class,
        eligibility_decision_ids=decision_ids,
    )


def _compile_preference_pair(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    chosen: AttemptRecord,
    chosen_grade: GradeRecord,
    rejected: AttemptRecord,
    rejected_grade: GradeRecord,
) -> PreferenceTrainingRow | TrainingExclusionRecord:
    reasons: dict[TrainingExclusionReason, str] = {}
    for candidate, grade in ((chosen, chosen_grade), (rejected, rejected_grade)):
        candidate_reasons = _attempt_reasons(
            snapshot,
            invocation,
            candidate,
            grade,
            lane=TrainingLane.PREFERENCE,
            teacher_influenced=False,
        )
        reasons.update(candidate_reasons)
    if chosen.checkpoint_id != rejected.checkpoint_id:
        reasons[TrainingExclusionReason.CHECKPOINT_IDENTITY_CONFLICT] = (
            "preference pairs require outputs from the same checkpoint identity"
        )
    if chosen.final_answer is None or rejected.final_answer is None:
        reasons[TrainingExclusionReason.MISSING_VALID_GRADE] = (
            "preference pairs require two materialized outputs"
        )
    chosen_decisions = _allowed_decisions(
        snapshot, invocation, chosen, chosen_grade, TrainingLane.PREFERENCE
    )
    rejected_decisions = _allowed_decisions(
        snapshot, invocation, rejected, rejected_grade, TrainingLane.PREFERENCE
    )
    decisions = tuple(
        sorted(
            {
                source.decision.decision_id: source
                for source in (*chosen_decisions, *rejected_decisions)
            }.values(),
            key=lambda source: source.decision.decision_id,
        )
    )
    lineage = _pair_lineage(snapshot, chosen, chosen_grade, rejected, rejected_grade, decisions)
    pair_id = f"{chosen.attempt_id}:{rejected.attempt_id}"
    if reasons:
        return _exclusion(
            candidate_id=pair_id,
            candidate_kind="attempt_pair",
            product_kind=TrainingProductKind.PREFERENCE,
            lane=TrainingLane.PREFERENCE,
            reasons=reasons,
            lineage=lineage,
        )
    decision_ids = tuple(source.decision.decision_id for source in decisions)
    return PreferenceTrainingRow(
        row_id=_row_id(
            "preference",
            {
                "chosen_attempt_id": chosen.attempt_id,
                "rejected_attempt_id": rejected.attempt_id,
                "decision_ids": decision_ids,
            },
        ),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        episode_id=chosen.episode_id,
        checkpoint_id=chosen.checkpoint_id,
        prompt=chosen.rendered_prompt,
        chosen_attempt_id=chosen.attempt_id,
        rejected_attempt_id=rejected.attempt_id,
        chosen=cast(dict[str, Any] | str, chosen.final_answer),
        rejected=cast(dict[str, Any] | str, rejected.final_answer),
        chosen_grade=chosen_grade.model_dump(mode="json"),
        rejected_grade=rejected_grade.model_dump(mode="json"),
        eligibility_decision_ids=decision_ids,
    )


def _continued_pretraining_rows(
    snapshot: _Snapshot,
) -> tuple[list[ContinuedPretrainingRow], list[TrainingExclusionRecord]]:
    rows: list[ContinuedPretrainingRow] = []
    exclusions: list[TrainingExclusionRecord] = []
    active_by_digest: dict[str, list[_DocumentSource]] = defaultdict(list)
    for source in snapshot.documents.values():
        decision = source.current_decision
        if decision is not None and decision.status == TrainingSourceStatus.ACTIVE:
            active_by_digest[source.document.content_digest].append(source)
    selected_ids: set[str] = set()
    for candidates in active_by_digest.values():
        selected = max(
            candidates,
            key=lambda source: (_utc(source.document.created_at), source.document.document_id),
        )
        selected_ids.add(selected.document.document_id)
    for source in sorted(snapshot.documents.values(), key=lambda item: item.document.document_id):
        document = source.document
        decision = source.current_decision
        lineage = _document_lineage(source)
        reasons: dict[TrainingExclusionReason, str] = {}
        if decision is None or decision.status != TrainingSourceStatus.ACTIVE:
            status = decision.status.value if decision is not None else "undecided"
            reasons[TrainingExclusionReason.SOURCE_DOCUMENT_NOT_ACTIVE] = (
                f"source document status is {status}"
            )
        elif decision.contaminated:
            reasons[TrainingExclusionReason.SOURCE_DOCUMENT_CONTAMINATED] = (
                "source document is marked contaminated"
            )
        if document.rights.review_status != RightsReviewStatus.CONFIRMED:
            reasons[TrainingExclusionReason.RIGHTS_REVIEW_REQUIRED] = (
                "source rights are not confirmed"
            )
        elif not document.rights.permits(RightsUse.CONTINUED_PRETRAINING):
            reasons[TrainingExclusionReason.RIGHTS_USE_NOT_PERMITTED] = (
                "source rights do not permit continued pretraining"
            )
        if not document.quality_evidence_refs:
            reasons[TrainingExclusionReason.SOURCE_DOCUMENT_NOT_ACTIVE] = (
                "source document lacks quality-gate evidence"
            )
        if (
            not reasons
            and document.document_id not in selected_ids
            and len(active_by_digest[document.content_digest]) > 1
        ):
            reasons[TrainingExclusionReason.DUPLICATE_SOURCE_CONTENT] = (
                "a newer active source version owns this content digest"
            )
        if reasons:
            exclusions.append(
                _exclusion(
                    candidate_id=document.document_id,
                    candidate_kind="training_source_document",
                    product_kind=TrainingProductKind.CONTINUED_PRETRAINING,
                    lane=TrainingLane.CONTINUED_PRETRAINING,
                    reasons=reasons,
                    lineage=lineage,
                )
            )
            continue
        rows.append(
            ContinuedPretrainingRow(
                row_id=_row_id(
                    "continued-pretraining",
                    {
                        "document_id": document.document_id,
                        "content_digest": document.content_digest,
                        "decision_id": decision.decision_id if decision is not None else None,
                    },
                ),
                source_evidence_refs=lineage[0],
                source_record_digests=lineage[1],
                rights_digests=lineage[2],
                document_id=document.document_id,
                source_id=document.source_id,
                source_version=document.source_version,
                language=document.language,
                media_type=document.media_type,
                content_ref=document.content_ref,
                content_digest=document.content_digest,
            )
        )
    return rows, exclusions


def _sealed_evaluation_rows(
    snapshot: _Snapshot,
) -> tuple[list[SealedEvaluationRow], list[TrainingExclusionRecord]]:
    rows: list[SealedEvaluationRow] = []
    exclusions: list[TrainingExclusionRecord] = []
    for source in sorted(snapshot.corpus.values(), key=lambda item: item.record.item_id):
        record = source.record
        if record.pool != CorpusPool.SEALED_ANCHOR:
            continue
        lineage = (
            (_source_ref(EvidenceSourceKind.CORPUS_ITEM, record.item_id),),
            (source.record_digest,),
            (source.rights_digest,),
        )
        reasons: dict[TrainingExclusionReason, str] = {}
        if source.lineage_contaminated:
            reasons[TrainingExclusionReason.CONTAMINATED_LINEAGE] = (
                "contaminated sealed lineage cannot enter an evaluation suite"
            )
        if record.rights.review_status != RightsReviewStatus.CONFIRMED:
            reasons[TrainingExclusionReason.RIGHTS_REVIEW_REQUIRED] = (
                "sealed source rights are not confirmed for evaluation"
            )
        elif not record.rights.permits(RightsUse.EVALUATION):
            reasons[TrainingExclusionReason.RIGHTS_USE_NOT_PERMITTED] = (
                "sealed source rights do not permit evaluation"
            )
        if reasons:
            exclusions.append(
                _exclusion(
                    candidate_id=record.item_id,
                    candidate_kind="corpus_item",
                    product_kind=TrainingProductKind.SEALED_EVALUATION,
                    lane=TrainingLane.EVALUATION_ONLY,
                    reasons=reasons,
                    lineage=lineage,
                )
            )
            continue
        rows.append(
            SealedEvaluationRow(
                row_id=_row_id(
                    "sealed-evaluation",
                    {"item_id": record.item_id, "record_digest": source.record_digest},
                ),
                source_evidence_refs=lineage[0],
                source_record_digests=lineage[1],
                rights_digests=lineage[2],
                item_id=record.item_id,
                competency_id=record.competency_id,
                task_manifest={
                    "item_id": record.item_id,
                    "prompt": record.prompt,
                    "source": record.source,
                    "rights": record.rights.model_dump(mode="json"),
                    "split": record.pool.value,
                    "template_family_id": record.template_family_id,
                    "instance_group_id": record.instance_group_id,
                    "generator_version": record.generator_version,
                    "environment_fingerprint": _environment_fingerprint(record),
                },
                verifier_spec=record.verifier_spec.model_dump(mode="json"),
                expected_answer=record.expected_answer,
            )
        )
    return rows, exclusions


def _attempt_lineage(
    snapshot: _Snapshot,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    decisions: tuple[_EligibilitySource, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    corpus = snapshot.corpus[attempt.item_id]
    refs = {
        _source_ref(EvidenceSourceKind.CORPUS_ITEM, attempt.item_id),
        _source_ref(EvidenceSourceKind.EPISODE, attempt.episode_id),
        _source_ref(EvidenceSourceKind.ATTEMPT, attempt.attempt_id),
    }
    digests = {
        corpus.record_digest,
        sha256_digest(snapshot.episodes[attempt.episode_id]),
        sha256_digest(attempt.model_dump(mode="json")),
    }
    if grade is not None:
        refs.add(_source_ref(EvidenceSourceKind.GRADE, grade.grade_id))
        digests.add(sha256_digest(grade.model_dump(mode="json")))
    for influence_id in attempt.influence_refs:
        intervention = snapshot.interventions.get(influence_id)
        if intervention is None:
            raise TrainingCompilationError(
                f"attempt teacher influence is missing: {attempt.attempt_id}"
            )
        refs.add(_source_ref(EvidenceSourceKind.TEACHER_INTERVENTION, influence_id))
        digests.add(sha256_digest(intervention.model_dump(mode="json")))
    for decision in decisions:
        refs.add(
            _source_ref(
                EvidenceSourceKind.TRAINING_ELIGIBILITY,
                decision.decision.decision_id,
            )
        )
        refs.add(_source_ref(EvidenceSourceKind.REWARD, decision.reward_id))
        digests.add(decision.record_digest)
        digests.add(snapshot.rewards[decision.reward_id][1])
    checkpoint = snapshot.checkpoints.get(attempt.checkpoint_id)
    if checkpoint is not None:
        refs.add(_source_ref(EvidenceSourceKind.CHECKPOINT, attempt.checkpoint_id))
        digests.add(checkpoint.evidence_digest)
    return (
        tuple(sorted(refs)),
        tuple(sorted(digests)),
        _attempt_rights(snapshot, attempt),
    )


def _pair_lineage(
    snapshot: _Snapshot,
    chosen: AttemptRecord,
    chosen_grade: GradeRecord,
    rejected: AttemptRecord,
    rejected_grade: GradeRecord,
    decisions: tuple[_EligibilitySource, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    first = _attempt_lineage(snapshot, chosen, chosen_grade, decisions)
    second = _attempt_lineage(snapshot, rejected, rejected_grade, decisions)
    return (
        tuple(sorted(set(first[0]).union(second[0]))),
        tuple(sorted(set(first[1]).union(second[1]))),
        tuple(sorted(set(first[2]).union(second[2]))),
    )


def _intervention_lineage(
    snapshot: _Snapshot, intervention: TeacherInterventionRecord
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    attempt = snapshot.attempts[intervention.targeted_attempt_id]
    corpus = snapshot.corpus[attempt.item_id]
    return (
        tuple(
            sorted(
                (
                    _source_ref(
                        EvidenceSourceKind.TEACHER_INTERVENTION,
                        intervention.intervention_id,
                    ),
                    _source_ref(EvidenceSourceKind.ATTEMPT, attempt.attempt_id),
                    _source_ref(EvidenceSourceKind.CORPUS_ITEM, attempt.item_id),
                )
            )
        ),
        tuple(
            sorted(
                (
                    sha256_digest(intervention.model_dump(mode="json")),
                    sha256_digest(attempt.model_dump(mode="json")),
                    corpus.record_digest,
                )
            )
        ),
        tuple(
            sorted(
                {corpus.rights_digest}
                | (
                    {sha256_digest(intervention.output_rights.model_dump(mode="json"))}
                    if intervention.output_rights is not None
                    else set()
                )
            )
        ),
    )


def _document_lineage(
    source: _DocumentSource,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    refs = {_source_ref(EvidenceSourceKind.TRAINING_SOURCE_DOCUMENT, source.document.document_id)}
    digests = {source.record_digest}
    decision = source.current_decision
    if decision is not None:
        refs.add(_source_ref(EvidenceSourceKind.TRAINING_SOURCE_DECISION, decision.decision_id))
        digests.add(sha256_digest(decision.model_dump(mode="json")))
    return tuple(sorted(refs)), tuple(sorted(digests)), (source.document.rights_digest,)


def _attempt_exclusion(
    snapshot: _Snapshot,
    invocation: CompilerInvocation,
    attempt: AttemptRecord,
    grade: GradeRecord | None,
    kind: TrainingProductKind,
    lane: TrainingLane,
    reasons: dict[TrainingExclusionReason, str],
    *,
    candidate_id: str | None = None,
) -> TrainingExclusionRecord:
    decisions = _candidate_decisions(
        snapshot,
        invocation,
        attempt,
        grade,
    )
    return _exclusion(
        candidate_id=candidate_id or attempt.attempt_id,
        candidate_kind="attempt",
        product_kind=kind,
        lane=lane,
        reasons=reasons,
        lineage=_attempt_lineage(snapshot, attempt, grade, decisions),
    )


def _exclusion(
    *,
    candidate_id: str,
    candidate_kind: str,
    product_kind: TrainingProductKind,
    lane: TrainingLane | None,
    reasons: dict[TrainingExclusionReason, str],
    lineage: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> TrainingExclusionRecord:
    ordered_reasons = tuple(sorted(reasons, key=lambda reason: reason.value))
    ordered_details = {reason: reasons[reason] for reason in ordered_reasons}
    return TrainingExclusionRecord(
        row_id=_row_id(
            "exclusion",
            {
                "candidate_id": candidate_id,
                "product_kind": product_kind.value,
                "reason_codes": [reason.value for reason in ordered_reasons],
                "source_record_digests": lineage[1],
            },
        ),
        source_evidence_refs=lineage[0],
        source_record_digests=lineage[1],
        rights_digests=lineage[2],
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        product_kind=product_kind,
        training_lane=lane,
        reason_codes=ordered_reasons,
        details=ordered_details,
    )


def _reward_records(
    snapshot: _Snapshot, decisions: tuple[_EligibilitySource, ...]
) -> tuple[RewardRecord, ...]:
    return tuple(
        snapshot.rewards[reward_id][0]
        for reward_id in sorted({decision.reward_id for decision in decisions})
    )


def _teacher_influenced_attempt_ids(snapshot: _Snapshot) -> set[str]:
    identifiers = {
        attempt.attempt_id for attempt in snapshot.attempts.values() if attempt.influence_refs
    }
    identifiers.update(revision.revised_attempt_id for revision in snapshot.revisions.values())
    identifiers.update(transfer.treatment_attempt_id for transfer in snapshot.transfers.values())
    return identifiers


def _checkpoint_identities(snapshot: _Snapshot) -> tuple[CheckpointSourceIdentity, ...]:
    attempts_by_checkpoint: dict[str, list[AttemptRecord]] = defaultdict(list)
    for attempt in snapshot.attempts.values():
        attempts_by_checkpoint[attempt.checkpoint_id].append(attempt)
    identities: list[CheckpointSourceIdentity] = []
    for checkpoint_id, attempts in sorted(attempts_by_checkpoint.items()):
        registered = snapshot.checkpoints.get(checkpoint_id)
        if registered is None:
            model_ids = sorted({attempt.model_id for attempt in attempts})
            identities.append(
                CheckpointSourceIdentity(
                    checkpoint_id=checkpoint_id,
                    model_id=model_ids[0] if len(model_ids) == 1 else "identity-conflict",
                    registered=False,
                )
            )
        else:
            identities.append(
                CheckpointSourceIdentity(
                    checkpoint_id=checkpoint_id,
                    model_id=registered.manifest.model_id,
                    tokenizer_id=registered.manifest.tokenizer_id,
                    model_digest=registered.manifest.model_digest,
                    tokenizer_digest=registered.manifest.tokenizer_digest,
                    manifest_digest=registered.manifest_digest,
                    registered=True,
                )
            )
    return tuple(identities)


def _evidence_sources(snapshot: _Snapshot) -> tuple[_EvidenceSource, ...]:
    sources: list[_EvidenceSource] = []
    attempt_by_id = snapshot.attempts
    for item_id, source in snapshot.corpus.items():
        payload = {
            **source.record.model_dump(mode="json"),
            "visibility_class": source.visibility_class,
            "lineage_contaminated": source.lineage_contaminated,
            "rights_digest": source.rights_digest,
        }
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.CORPUS_ITEM,
                item_id,
                payload,
                record_digest=source.record_digest,
                rights_digests=(source.rights_digest,),
            )
        )
    for episode_id, record in snapshot.episodes.items():
        item_id = str(record.get("task_item_id") or record.get("item_id") or "")
        rights = _rights_for_item(snapshot, item_id)
        role_value = record.get("research_role")
        role = ResearchRole(str(role_value)) if role_value is not None else None
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.EPISODE,
                episode_id,
                record,
                research_role=role,
                rights_digests=rights,
            )
        )
    for attempt_id, attempt in snapshot.attempts.items():
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.ATTEMPT,
                attempt_id,
                attempt.model_dump(mode="json"),
                research_role=attempt.research_role,
                rights_digests=_attempt_rights(snapshot, attempt),
            )
        )
    for grade_id, grade in snapshot.grades.items():
        graded_attempt = attempt_by_id[grade.attempt_id]
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.GRADE,
                grade_id,
                grade.model_dump(mode="json"),
                research_role=graded_attempt.research_role,
                rights_digests=_attempt_rights(snapshot, graded_attempt),
            )
        )
    for intervention_id, intervention in snapshot.interventions.items():
        targeted_attempt = attempt_by_id[intervention.targeted_attempt_id]
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.TEACHER_INTERVENTION,
                intervention_id,
                intervention.model_dump(mode="json"),
                research_role=ResearchRole.TEACHER,
                rights_digests=tuple(
                    sorted(
                        set(_rights_for_item(snapshot, targeted_attempt.item_id))
                        | (
                            {sha256_digest(intervention.output_rights.model_dump(mode="json"))}
                            if intervention.output_rights is not None
                            else set()
                        )
                    )
                ),
            )
        )
    for revision_id, revision in snapshot.revisions.items():
        revised_attempt = attempt_by_id.get(revision.revised_attempt_id)
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.REVISION,
                revision_id,
                revision.model_dump(mode="json"),
                research_role=(
                    revised_attempt.research_role if revised_attempt is not None else None
                ),
                rights_digests=(
                    _attempt_rights(snapshot, revised_attempt)
                    if revised_attempt is not None
                    else ()
                ),
            )
        )
    for transfer_id, transfer in snapshot.transfers.items():
        treatment_attempt = attempt_by_id.get(transfer.treatment_attempt_id)
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.TRANSFER_TRIAL,
                transfer_id,
                transfer.model_dump(mode="json"),
                research_role=(
                    treatment_attempt.research_role if treatment_attempt is not None else None
                ),
                rights_digests=(
                    _attempt_rights(snapshot, treatment_attempt)
                    if treatment_attempt is not None
                    else ()
                ),
            )
        )
    for result_id, (verifier_record, verifier_digest) in snapshot.verifiers.items():
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.VERIFIER_RESULT,
                result_id,
                verifier_record.model_dump(mode="json"),
                record_digest=verifier_digest,
            )
        )
    for reward_id, (reward_record, reward_digest) in snapshot.rewards.items():
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.REWARD,
                reward_id,
                reward_record.model_dump(mode="json"),
                record_digest=reward_digest,
            )
        )
    for decision_id, eligibility_source in snapshot.eligibilities.items():
        eligibility_payload = {
            "reward_id": eligibility_source.reward_id,
            "decision": eligibility_source.decision.model_dump(mode="json"),
        }
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.TRAINING_ELIGIBILITY,
                decision_id,
                eligibility_payload,
                record_digest=eligibility_source.record_digest,
            )
        )
    for checkpoint_id, checkpoint_source in snapshot.checkpoints.items():
        checkpoint_payload = {
            "manifest": checkpoint_source.manifest.model_dump(mode="json"),
            "status": checkpoint_source.status,
        }
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.CHECKPOINT,
                checkpoint_id,
                checkpoint_payload,
                record_digest=checkpoint_source.evidence_digest,
            )
        )
    for document_id, document_source in snapshot.documents.items():
        sources.append(
            _make_evidence_source(
                EvidenceSourceKind.TRAINING_SOURCE_DOCUMENT,
                document_id,
                document_source.document.model_dump(mode="json"),
                record_digest=document_source.record_digest,
                rights_digests=(document_source.document.rights_digest,),
            )
        )
        for source_decision, source_decision_digest in document_source.decisions:
            sources.append(
                _make_evidence_source(
                    EvidenceSourceKind.TRAINING_SOURCE_DECISION,
                    source_decision.decision_id,
                    source_decision.model_dump(mode="json"),
                    record_digest=source_decision_digest,
                    rights_digests=(document_source.document.rights_digest,),
                )
            )
    return tuple(sorted(sources, key=lambda source: (source.kind.value, source.source_id)))


def _make_evidence_source(
    kind: EvidenceSourceKind,
    source_id: str,
    record: dict[str, Any],
    *,
    record_digest: str | None = None,
    research_role: ResearchRole | None = None,
    rights_digests: tuple[str, ...] = (),
) -> _EvidenceSource:
    return _EvidenceSource(
        kind=kind,
        source_id=source_id,
        record=record,
        record_digest=record_digest or sha256_digest(record),
        research_role=research_role,
        artifact_refs=_extract_artifact_refs(record),
        rights_digests=tuple(sorted(set(rights_digests))),
    )


def _rights_for_item(snapshot: _Snapshot, item_id: str) -> tuple[str, ...]:
    source = snapshot.corpus.get(item_id)
    return (source.rights_digest,) if source is not None else ()


def _attempt_rights(snapshot: _Snapshot, attempt: AttemptRecord) -> tuple[str, ...]:
    digests = set(_rights_for_item(snapshot, attempt.item_id))
    if attempt.output_rights is not None:
        digests.add(sha256_digest(attempt.output_rights.model_dump(mode="json")))
    return tuple(sorted(digests))


def _corpus_record(row: CorpusItemRow) -> CorpusItemRecord:
    return CorpusItemRecord.model_validate(
        {
            "competency_id": row.competency_id,
            "template_family_id": row.template_family_id,
            "instance_group_id": row.instance_group_id,
            "item_id": row.item_id,
            "generation_seed": row.generation_seed,
            "generator_version": row.generator_version,
            "difficulty": row.difficulty,
            "prompt": row.prompt,
            "expected_answer": row.expected_answer,
            "verifier_spec": row.verifier_spec,
            "pool": row.pool,
            "status": row.status,
            "source": row.source,
            "rights": row.rights_json,
            "license": row.license,
            "contamination_scope": row.contamination_scope,
            "created_at": row.created_at,
            "retired_at": row.retired_at,
            "retirement_reason": row.retirement_reason,
            "artifacts": row.metadata_json.get("artifact_refs", []),
        },
        strict=False,
    )


async def _snapshot_watermark(session: AsyncSession) -> datetime:
    tables = (
        CorpusItemRow,
        EpisodeRow,
        AttemptRow,
        GradeRow,
        TeacherInterventionRow,
        RevisionRow,
        TransferTrialRow,
        VerifierResultRow,
        RewardRow,
        TrainingEligibilityRow,
        CheckpointRow,
        TrainingSourceDocumentRow,
        TrainingSourceDecisionRow,
    )
    values = [await session.scalar(select(func.max(table.created_at))) for table in tables]
    present = [_utc(value) for value in values if value is not None]
    return max(present, default=datetime(1970, 1, 1, tzinfo=UTC))


def _environment_fingerprint(record: CorpusItemRecord) -> str:
    return sha256_digest(
        {
            "competency_id": record.competency_id,
            "template_family_id": record.template_family_id,
            "generator_version": record.generator_version,
            "verifier_spec": record.verifier_spec.model_dump(mode="json"),
        }
    )


def _extract_artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: dict[str, ArtifactRef] = {}

    def visit(candidate: Any) -> None:
        if isinstance(candidate, dict):
            required = {"artifact_id", "uri", "digest", "media_type", "size_bytes"}
            if required.issubset(candidate):
                try:
                    reference = ArtifactRef.model_validate(candidate, strict=False)
                except ValidationError:
                    pass
                else:
                    found[reference.artifact_id] = reference
                    return
            for nested in candidate.values():
                visit(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)

    visit(value)
    return tuple(found[key] for key in sorted(found))


def _jsonl_bytes(rows: Sequence[CompiledRow]) -> bytes:
    return b"".join(canonical_json_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)


def _parse_product_rows(kind: TrainingProductKind, content: bytes) -> tuple[CompiledRow, ...]:
    model = _ROW_MODELS[kind]
    if not content:
        return ()
    if not content.endswith(b"\n"):
        raise ValueError("JSONL artifact lacks a terminal newline")
    rows: list[CompiledRow] = []
    for line in content.splitlines():
        payload = json.loads(line)
        rows.append(model.model_validate(payload, strict=False))
    if _jsonl_bytes(rows) != content:
        raise ValueError("JSONL artifact is not in canonical form")
    return tuple(rows)


def _artifact_ref(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=row.artifact_id,
        uri=row.uri,
        digest=row.digest,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        restricted=row.restricted,
        raw_data=row.raw_data,
    )


def _manifest_identity(manifest: TrainingBundleManifest) -> dict[str, Any]:
    payload = manifest.model_dump(mode="json")
    payload.pop("bundle_id")
    return payload


def _source_ref(kind: EvidenceSourceKind, source_id: str) -> str:
    return f"{kind.value}:{source_id}"


def _row_id(prefix: str, identity: Any) -> str:
    return f"{prefix}-{sha256_digest(identity)[7:39]}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
