from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.checkpoints import CheckpointRegistry
from padawan.domains.contracts import (
    HardGateResult,
    RewardObservation,
    TrainingEligibilityDecision,
    TrainingLane,
    VerifierDisposition,
    VerifierResult,
)
from padawan.models.contracts import (
    ResearchRole,
    RightsReviewStatus,
    SourceRights,
    project_authored_internal_rights,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import CheckpointManifest
from padawan.models.tables import AttemptRow, GradeRow
from padawan.rewards import RewardEngine, default_meta_utility_policy
from padawan.training import TrainingCompiler
from padawan.training.contracts import (
    EvidenceLedgerEntry,
    TrainingExclusionReason,
    TrainingProductKind,
    TrainingSourceDecision,
    TrainingSourceDocument,
    TrainingSourceStatus,
)
from padawan.training.sources import TrainingSourceRegistry
from tests.helpers import build_test_workflow


async def _complete_episode(database, artifact_root, *, role=ResearchRole.TARGET) -> None:
    _, supervisor, runs, _, _, state_id = await build_test_workflow(
        database, artifact_root, research_role=role
    )
    async with database.transaction() as session:
        await runs.create(
            session,
            run_id=f"run-training-{role.value}",
            payload={
                "student_id": "student-test",
                "state_id": state_id,
                "research_role": role.value,
                "pool": "curriculum",
                "experiment_seed": 41,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )
    await supervisor.run(budget=256)


async def _admit_one_attempt(database) -> str:
    timestamp = datetime.now(UTC)
    rewards = RewardEngine()
    policy = default_meta_utility_policy(created_at=timestamp)
    verifier = VerifierResult(
        result_id="training-compiler-integrity",
        verifier_id="test.training.integrity",
        verifier_version="1",
        scope="pending",
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="test evidence passed",
        evidence={"kind": "deterministic-test"},
        created_at=timestamp,
    )
    async with database.transaction() as session:
        attempt_row = await session.scalar(
            select(AttemptRow).where(AttemptRow.request_id.like("%:student:cold"))
        )
        assert attempt_row is not None
        grade_row = await session.scalar(
            select(GradeRow).where(GradeRow.attempt_id == attempt_row.attempt_id)
        )
        assert grade_row is not None
        attempt = attempt_row.record_json
        verifier = verifier.model_copy(update={"scope": attempt_row.attempt_id})
        await CheckpointRegistry().register_checkpoint(
            session,
            CheckpointManifest(
                checkpoint_id=str(attempt["checkpoint_id"]),
                model_id=str(attempt["model_id"]),
                tokenizer_id="test-tokenizer",
                model_digest=f"sha256:{'b' * 64}",
                tokenizer_digest=f"sha256:{'c' * 64}",
                architecture={"family": "test-open-weight"},
                runtime_compatibility={"responses_api": True},
                created_at=timestamp,
            ),
        )
        await rewards.register_policy(session, policy)
        await rewards.record_verifier_result(session, verifier)
        reward = await rewards.compute(
            session,
            reward_id="training-compiler-reward",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            hard_gates=(
                HardGateResult(
                    gate_id="training-integrity",
                    passed=True,
                    disposition=VerifierDisposition.VERIFIED,
                    evidence_refs=(verifier.result_id,),
                    reason="deterministic integrity evidence passed",
                ),
            ),
            observations=tuple(
                RewardObservation(
                    component_id=component.component_id,
                    value=0.25,
                    evidence_refs=(attempt_row.attempt_id, grade_row.grade_id),
                )
                for component in policy.components
            ),
            created_at=timestamp,
        )
        decision = TrainingEligibilityDecision(
            decision_id="training-compiler-eligibility",
            policy_id="test.training.eligibility",
            policy_version="1",
            allowed_lanes=(
                TrainingLane.PREFERENCE,
                TrainingLane.PROCESS,
                TrainingLane.RLVR,
                TrainingLane.SFT,
            ),
            excluded_lanes={
                TrainingLane.CONTINUED_PRETRAINING: ("episode evidence is not a source corpus")
            },
            evidence_refs=(reward.reward_id, verifier.result_id),
            subject_refs=(attempt_row.attempt_id,),
            created_at=timestamp,
        )
        await rewards.record_training_eligibility(
            session, reward_id=reward.reward_id, decision=decision
        )
        return attempt_row.attempt_id


def _product_lines(store, manifest, kind: TrainingProductKind) -> list[dict[str, object]]:
    product = next(product for product in manifest.products if product.kind == kind)
    text = store.read_text(product.artifact, allow_restricted=True)
    return [json.loads(line) for line in text.splitlines()]


async def test_compiler_is_digest_stable_and_keeps_excluded_evidence(database, tmp_path) -> None:
    artifact_root = tmp_path / "training-artifacts"
    await _complete_episode(database, artifact_root)
    admitted_attempt_id = await _admit_one_attempt(database)
    store = LocalArtifactStore(artifact_root)
    compiler = TrainingCompiler(store)

    async with database.transaction() as session:
        first = await compiler.compile(session)
    async with database.transaction() as session:
        second = await compiler.compile(session)
        verification = await compiler.verify(session, bundle_id=first.manifest.bundle_id)

    assert first.manifest == second.manifest
    assert first.manifest_ref == second.manifest_ref
    assert verification.valid, verification.errors
    assert first.manifest.internal_only is True
    assert first.manifest.included_counts[TrainingProductKind.NORMALIZED_EPISODES] == 1
    assert first.manifest.included_counts[TrainingProductKind.RLVR] == 1
    assert first.manifest.included_counts[TrainingProductKind.NEGATIVE_PROCESS] == 1
    assert first.manifest.included_counts[TrainingProductKind.SFT] == 0

    evidence = _product_lines(store, first.manifest, TrainingProductKind.EVIDENCE_LEDGER)
    attempts = [row for row in evidence if row["source_kind"] == "attempt"]
    interventions = [row for row in evidence if row["source_kind"] == "teacher_intervention"]
    assert attempts
    assert interventions
    assert any(row["research_role"] == "target" for row in attempts)
    assert all(row["record"]["output_rights"]["review_status"] == "confirmed" for row in attempts)
    assert all(
        row["record"]["output_rights"]["review_status"] == "review_required"
        for row in interventions
    )
    process = _product_lines(store, first.manifest, TrainingProductKind.NEGATIVE_PROCESS)
    assert [row["attempt_id"] for row in process] == [admitted_attempt_id]
    serialized_process = json.dumps(process, sort_keys=True)
    assert "private_reasoning_ref" not in serialized_process
    assert "raw_generation_ref" not in serialized_process
    assert first.manifest.exclusion_counts[TrainingExclusionReason.TEACHER_OUTPUT] >= 5
    assert first.manifest.exclusion_counts[
        TrainingExclusionReason.EPISODE_NOT_SOURCE_CORPUS
    ] >= len(attempts)


async def test_baseline_outputs_are_labeled_retained_and_excluded(database, tmp_path) -> None:
    artifact_root = tmp_path / "baseline-artifacts"
    await _complete_episode(database, artifact_root, role=ResearchRole.BASELINE)
    store = LocalArtifactStore(artifact_root)
    compiler = TrainingCompiler(store)
    async with database.transaction() as session:
        build = await compiler.compile(session)

    evidence = _product_lines(store, build.manifest, TrainingProductKind.EVIDENCE_LEDGER)
    attempt_evidence = [
        EvidenceLedgerEntry.model_validate(row, strict=False)
        for row in evidence
        if row["source_kind"] == "attempt"
    ]
    assert attempt_evidence
    assert all(row.research_role == ResearchRole.BASELINE for row in attempt_evidence)
    assert build.manifest.included_counts[TrainingProductKind.SFT] == 0
    assert build.manifest.included_counts[TrainingProductKind.RLVR] == 0
    assert build.manifest.exclusion_counts[TrainingExclusionReason.BASELINE_OUTPUT] >= (
        len(attempt_evidence) * 4
    )


async def test_continued_pretraining_requires_active_deduplicated_source(
    database, tmp_path
) -> None:
    timestamp = datetime(2026, 8, 1, 12, tzinfo=UTC)
    store = LocalArtifactStore(tmp_path / "source-artifacts")
    catalog = ArtifactCatalog(store)
    registry = TrainingSourceRegistry(catalog)
    content_ref = store.put_text(
        "Project-authored research text.\n",
        restricted=True,
        raw_data=True,
    )
    rights = project_authored_internal_rights(reviewed_at=timestamp)
    rights_digest = sha256_digest(rights.model_dump(mode="json"))

    def document(version: str, created_at: datetime, supersedes: str | None = None):
        return TrainingSourceDocument(
            document_id=f"source-document-{version}",
            source_id="padawan.internal.notes",
            source_version=version,
            supersedes_document_id=supersedes,
            title="Internal research notes",
            language="en",
            media_type=content_ref.media_type,
            content_ref=content_ref,
            content_digest=content_ref.digest,
            rights=rights,
            rights_digest=rights_digest,
            quality_evidence_refs=(f"quality-{version}",),
            admitted_by="test-reviewer",
            created_at=created_at,
        )

    first_document = document("1", timestamp)
    second_document = document("2", timestamp + timedelta(seconds=2), first_document.document_id)
    async with database.transaction() as session:
        for source in (first_document, second_document):
            decision = TrainingSourceDecision(
                decision_id=f"source-decision-{source.source_version}",
                document_id=source.document_id,
                status=TrainingSourceStatus.ACTIVE,
                contaminated=False,
                reason="rights and quality gates passed",
                evidence_refs=source.quality_evidence_refs,
                decided_by="test-reviewer",
                created_at=source.created_at,
            )
            await registry.admit(session, document=source, initial_decision=decision)
    with pytest.raises(ValueError, match="predates document admission"):
        async with database.transaction() as session:
            await registry.decide(
                session,
                decision=TrainingSourceDecision(
                    decision_id="source-decision-backdated",
                    document_id=first_document.document_id,
                    status=TrainingSourceStatus.REVIEW_REQUIRED,
                    contaminated=False,
                    reason="invalid backdated decision",
                    evidence_refs=(),
                    decided_by="test-reviewer",
                    created_at=timestamp - timedelta(seconds=1),
                ),
            )
    compiler = TrainingCompiler(store, catalog)
    async with database.transaction() as session:
        build = await compiler.compile(session)
        verification = await compiler.verify(session, bundle_id=build.manifest.bundle_id)

    rows = _product_lines(store, build.manifest, TrainingProductKind.CONTINUED_PRETRAINING)
    assert verification.valid, verification.errors
    assert [row["document_id"] for row in rows] == [second_document.document_id]
    assert build.manifest.exclusion_counts[TrainingExclusionReason.DUPLICATE_SOURCE_CONTENT] == 1


async def test_source_registry_refuses_active_unreviewed_rights(database, tmp_path) -> None:
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    store = LocalArtifactStore(tmp_path / "review-artifacts")
    catalog = ArtifactCatalog(store)
    content_ref = store.put_text("review me", restricted=True, raw_data=True)
    confirmed = project_authored_internal_rights(reviewed_at=timestamp)
    unreviewed_payload = confirmed.model_dump(mode="json")
    unreviewed_payload.update(
        {
            "review_status": RightsReviewStatus.REVIEW_REQUIRED.value,
            "reviewed_by": None,
            "reviewed_at": None,
        }
    )
    unreviewed = SourceRights.model_validate(unreviewed_payload, strict=False)
    source = TrainingSourceDocument(
        document_id="source-review-required",
        source_id="third-party.pending",
        source_version="1",
        title="Pending source",
        language="en",
        media_type=content_ref.media_type,
        content_ref=content_ref,
        content_digest=content_ref.digest,
        rights=unreviewed,
        rights_digest=sha256_digest(unreviewed.model_dump(mode="json")),
        quality_evidence_refs=("quality-passed",),
        admitted_by="test-reviewer",
        created_at=timestamp,
    )
    decision = TrainingSourceDecision(
        decision_id="source-review-active-invalid",
        document_id=source.document_id,
        status=TrainingSourceStatus.ACTIVE,
        contaminated=False,
        reason="invalid attempted activation",
        evidence_refs=("quality-passed",),
        decided_by="test-reviewer",
        created_at=timestamp,
    )
    with pytest.raises(ValueError, match="confirmed continued-pretraining rights"):
        async with database.transaction() as session:
            await TrainingSourceRegistry(catalog).admit(
                session, document=source, initial_decision=decision
            )
