"""Explicit Atlas source validation for reviewed institutional evidence.

Only the trusted broker receives this object or its source descriptions. This
does not authenticate a principal, attest an experiment, or admit training data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import ForensicArtifactRef
from padawan.artifacts.store import ArtifactCatalog
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.contracts import (
    AccessClassification,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasRunManifest,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    CampaignExecutionBinding,
    ContaminationClassification,
    DatasetGovernance,
    EvaluationClass,
    SuiteStatus,
    TrialAllocation,
)
from padawan.atlas.evidence_contracts import (
    AtlasEvidenceDisclosurePolicy,
    AtlasEvidenceOriginReview,
    AtlasTrialEvidenceSource,
)
from padawan.atlas.registry import CapabilityAtlasRegistry
from padawan.models.contracts import RightsUse, SourceRights, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import HarnessProfile, ResearchExecutionManifest
from padawan.models.tables import (
    ArtifactRow,
    AtlasAllocationRow,
    AtlasCampaignExecutionBindingRow,
    AtlasCampaignRow,
    AtlasCampaignSuiteRow,
    AtlasDatasetGovernanceRow,
    AtlasItemRow,
    AtlasRunManifestRow,
    AtlasSuiteItemRow,
    AtlasSuiteRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    Base,
    ExternalCallRow,
    HarnessProfileRow,
    ResearchExecutionRow,
)
from padawan.pprl.contracts import ProjectSplit
from padawan.pprl.evidence_contracts import ProcessEvidenceAdmission, ProcessEvidenceUse


@dataclass(frozen=True)
class AtlasEvidenceSources:
    """Privileged preparation result; never a worker payload."""

    trials: tuple[AtlasTrialEvidenceSource, ...]
    forensic_sources: tuple[ForensicArtifactRef, ...]
    forbidden_identifiers: frozenset[str]
    includes_adaptive_source: bool


class AtlasEvidenceSourceBoundary:
    def __init__(self, *, catalog: ArtifactCatalog, policy: AtlasEvidenceDisclosurePolicy) -> None:
        self.catalog = catalog
        self.policy = AtlasEvidenceDisclosurePolicy.model_validate_json(policy.model_dump_json())
        self.registry = CapabilityAtlasRegistry(artifacts=AtlasArtifactBoundary(catalog))

    async def describe(
        self, session: AsyncSession, *, result_ids: tuple[str, ...]
    ) -> AtlasEvidenceSources:
        """Prepare exact native source identities for a separate explicit review."""
        if not 1 <= len(result_ids) <= 16 or tuple(sorted(set(result_ids))) != result_ids:
            raise PermissionError("Atlas disclosure needs a bounded canonical trial selection")
        trials = []
        artifacts: dict[str, ForensicArtifactRef] = {}
        identifiers: set[str] = set()
        adaptive = False
        for result_id in result_ids:
            source, refs, source_identifiers, is_adaptive = await self._source(session, result_id)
            trials.append(source)
            adaptive |= is_adaptive
            identifiers.update(source_identifiers)
            for reference in refs:
                existing = artifacts.setdefault(reference.artifact.artifact_id, reference)
                if existing != reference:
                    raise PermissionError("Atlas source artifacts have conflicting identities")
        return AtlasEvidenceSources(
            tuple(trials),
            tuple(artifacts[key] for key in sorted(artifacts)),
            frozenset(identifiers),
            adaptive,
        )

    async def validate_review(
        self,
        session: AsyncSession,
        *,
        review: ProcessEvidenceAdmission,
        origin: AtlasEvidenceOriginReview,
        target_split: ProjectSplit,
        candidate_bytes: bytes,
        now: datetime,
    ) -> None:
        policy = self.policy
        if (
            origin.policy_digest != policy.digest
            or origin.disclosure_policy != policy
            or origin.candidate_review_digest != review.digest
            or review.process_reference.execution_digest != policy.target_execution_digest
            or review.contamination_scope != policy.target_contamination_scope
            or review.reviewer_id not in policy.reviewer_ids
            or not policy.created_at <= review.reviewed_at <= now < policy.expires_at
            or review.allowed_uses != (ProcessEvidenceUse.PROCESS,)
            or target_split not in {ProjectSplit.TRAIN, ProjectSplit.ADAPTIVE_DEVELOPMENT}
        ):
            raise PermissionError("Atlas disclosure lacks exact current reviewed use authority")
        sources = await self.describe(
            session, result_ids=tuple(source.result_id for source in origin.trials)
        )
        if sources.trials != origin.trials or sources.forensic_sources != review.forensic_sources:
            raise PermissionError("Atlas review substitutes or omits its exact source set")
        if sources.includes_adaptive_source and target_split != ProjectSplit.ADAPTIVE_DEVELOPMENT:
            raise PermissionError("adaptive Atlas evidence requires an adaptive destination")
        for source in origin.trials:
            row = await _require(session, AtlasTrialResultRow, source.result_id)
            result = _record(row, AtlasTrialResult)
            if _utc(result.completed_at) > review.reviewed_at:
                raise PermissionError("Atlas evidence review predates a source result")
        identifiers = sources.forbidden_identifiers | {
            origin.digest,
            policy.digest,
            policy.policy_id,
            *(source.context_digest for source in origin.trials),
        }
        for identifier in identifiers:
            variants = (identifier, identifier.removeprefix("sha256:"))
            if any(value.encode("utf-8") in candidate_bytes for value in variants):
                raise PermissionError("Atlas derivative contains a privileged source identifier")

    async def _source(
        self, session: AsyncSession, result_id: str
    ) -> tuple[AtlasTrialEvidenceSource, tuple[ForensicArtifactRef, ...], set[str], bool]:
        result_row = await _require(session, AtlasTrialResultRow, result_id)
        result = _record(result_row, AtlasTrialResult)
        request_row = await _require(session, AtlasTrialRequestRow, result.request_id)
        request = _record(request_row, AtlasTrialRequest)
        coordinates = (
            request.campaign_digest,
            request.condition_id,
            request.suite_digest,
            request.research_execution_digest,
        )
        scope = next(
            (scope for scope in self.policy.source_scopes if scope.coordinates == coordinates),
            None,
        )
        if scope is None:
            raise PermissionError("Atlas trial is outside the configured disclosure sources")
        suite_row = await _require(session, AtlasSuiteRow, request.suite_digest)
        suite = _record(suite_row, AtlasSuiteManifest)
        governance_row = await _require(session, AtlasDatasetGovernanceRow, suite.governance_id)
        governance = _record(governance_row, DatasetGovernance)
        campaign_row = await _require(session, AtlasCampaignRow, request.campaign_digest)
        campaign = _record(campaign_row, AtlasCampaignManifest)
        item_row = await _require(session, AtlasItemRow, request.item_digest)
        item = _record(item_row, AtlasItemManifest)
        run_row = await session.scalar(
            select(AtlasRunManifestRow).where(AtlasRunManifestRow.run_id == request.run_id)
        )
        if run_row is None:
            raise PermissionError("Atlas disclosure lost its run manifest")
        run = _record(run_row, AtlasRunManifest)
        execution_row = await _require(
            session, ResearchExecutionRow, request.research_execution_digest
        )
        execution = _record(execution_row, ResearchExecutionManifest)
        profile_row = await _require(session, HarnessProfileRow, execution.harness_profile_digest)
        profile = _record(profile_row, HarnessProfile)
        binding_row = await _require(
            session, AtlasCampaignExecutionBindingRow, run.campaign_execution_binding_digest
        )
        binding = _record(binding_row, CampaignExecutionBinding)
        allocation_row = await _require(session, AtlasAllocationRow, request.allocation_id)
        allocation = _record(allocation_row, TrialAllocation)
        campaign_suite = next(
            (
                entry
                for entry in campaign.suite_bindings
                if entry.suite_digest == suite.content_digest
            ),
            None,
        )
        adaptive = suite.evaluation_class == EvaluationClass.ADAPTIVE_SEARCH
        if (
            suite.content_digest != request.suite_digest
            or campaign.manifest_digest != request.campaign_digest
            or item.item_digest != request.item_digest
            or item.item_id != request.item_id
            or item.prompt_digest != request.prompt_digest
            or item.item_digest not in suite.item_digests
            or suite_row.item_count != len(suite.item_digests)
            or governance_row.rights_digest != sha256_digest(governance.rights)
            or governance_row.executable != governance.executable
            or sha256_digest(execution) != request.research_execution_digest
            or sha256_digest(profile) != execution.harness_profile_digest
            or run.manifest_digest != run_row.manifest_digest
            or run_row.binding_digest != binding.binding_digest
        ):
            raise PermissionError("Atlas disclosure has inconsistent native source identities")
        if (
            campaign_suite is None
            or request.condition_id not in campaign_suite.condition_ids
            or suite.status != SuiteStatus.READY
            or suite.evaluation_class
            not in {EvaluationClass.DEVELOPMENT, EvaluationClass.ADAPTIVE_SEARCH}
            or campaign_suite.evaluation_class != suite.evaluation_class
            or campaign_suite.adaptive != adaptive
            or run.evaluation_class != suite.evaluation_class
            or run.adaptive != adaptive
            or governance.evaluation_class != suite.evaluation_class
            or governance.access != AccessClassification.LOCAL
            or not governance.executable
            or governance.contamination != ContaminationClassification.NO_KNOWN_EXPOSURE
            or _utc(governance.reviewed_at) > self.policy.created_at
            or not all(result.contamination_checks.values())
            or suite.content_digest in campaign.promotion_suite_digests
            or (suite.content_digest in campaign.adaptive_suite_digests) != adaptive
            or (
                run.campaign_digest,
                run.condition_id,
                run.suite_digest,
                run.research_execution_digest,
            )
            != coordinates
            or (
                binding.campaign_digest,
                binding.condition_id,
                binding.suite_digest,
                binding.research_execution_digest,
            )
            != coordinates
            or binding.binding_digest != run.campaign_execution_binding_digest
            or run.harness_profile_digest != execution.harness_profile_digest
            or binding.harness_profile_digest != execution.harness_profile_digest
            or allocation.campaign_digest != request.campaign_digest
            or allocation.condition_id != request.condition_id
            or allocation.suite_digest != request.suite_digest
            or allocation.item_digest != request.item_digest
            or allocation.trial_index != request.trial_index
        ):
            raise PermissionError("Atlas disclosure crosses its experiment or contamination scope")
        _rights(governance.rights, before=self.policy.created_at)
        _rights(scope.output_rights, before=self.policy.created_at)
        memberships, overlapping_adaptive = await self._memberships(session, item)
        refs = await self.registry.validate_trial_artifacts(session, result_id=result_id)
        artifact_rows = [
            await _require(session, ArtifactRow, ref.artifact.artifact_id) for ref in refs
        ]
        call = await session.get(ExternalCallRow, request.request_id)
        context = {
            "suite": suite,
            "governance": governance,
            "campaign": campaign,
            "item": item,
            "run": run,
            "execution": execution,
            "profile": profile,
            "binding": binding,
            "allocation": allocation,
            "memberships": memberships,
            "call": _columns(call) if call is not None else None,
            "artifacts": [_columns(row) for row in artifact_rows],
        }
        if len(canonical_json_bytes(context)) > 4_194_304:
            raise PermissionError("Atlas disclosure exceeds its per-trial context byte bound")
        source = AtlasTrialEvidenceSource(
            result_id=result.result_id,
            result_digest=result.result_digest,
            result_record_digest=sha256_digest(result),
            request_id=request.request_id,
            request_record_digest=sha256_digest(request),
            context_digest=sha256_digest(context),
        )
        identifiers = {
            result.result_id,
            request.request_id,
            request.run_id,
            request.allocation_id,
            suite.suite_id,
            governance.governance_id,
            campaign.campaign_id,
            run.run_manifest_id,
            binding.binding_id,
            execution.execution_id,
            profile.profile_id,
            item.item_id,
            *coordinates,
            source.result_record_digest,
            source.request_record_digest,
            *_digests(context),
            *_digests(result.model_dump(mode="json")),
            *_digests(request.model_dump(mode="json")),
        }
        return source, refs, identifiers, adaptive or overlapping_adaptive

    async def _memberships(
        self, session: AsyncSession, item: AtlasItemManifest
    ) -> tuple[list[Any], bool]:
        """Exclude registered item/prompt overlap; not a semantic-near-duplicate detector."""
        rows = (
            await session.scalars(
                select(AtlasSuiteRow)
                .join(
                    AtlasSuiteItemRow, AtlasSuiteItemRow.suite_digest == AtlasSuiteRow.suite_digest
                )
                .join(AtlasItemRow, AtlasItemRow.item_digest == AtlasSuiteItemRow.item_digest)
                .where(
                    or_(
                        AtlasItemRow.item_digest == item.item_digest,
                        AtlasItemRow.prompt_digest == item.prompt_digest,
                    )
                )
                .order_by(AtlasSuiteRow.suite_digest)
                .distinct()
            )
        ).all()
        if not rows:
            raise PermissionError("Atlas source lost its registered item membership")
        memberships = []
        adaptive = False
        for row in rows:
            suite = _record(row, AtlasSuiteManifest)
            indexed_items = tuple(
                await session.scalars(
                    select(AtlasSuiteItemRow.item_digest)
                    .where(AtlasSuiteItemRow.suite_digest == row.suite_digest)
                    .order_by(AtlasSuiteItemRow.item_digest)
                )
            )
            if indexed_items != suite.item_digests or row.item_count != len(indexed_items):
                raise PermissionError("Atlas suite membership differs from its frozen manifest")
            adaptive |= suite.evaluation_class == EvaluationClass.ADAPTIVE_SEARCH
            if suite.evaluation_class in {
                EvaluationClass.CHALLENGE,
                EvaluationClass.SEALED_PROMOTION,
            }:
                raise PermissionError("Atlas source overlaps challenge or sealed material")
            campaigns = (
                await session.scalars(
                    select(AtlasCampaignRow)
                    .join(
                        AtlasCampaignSuiteRow,
                        AtlasCampaignSuiteRow.campaign_digest == AtlasCampaignRow.campaign_digest,
                    )
                    .where(AtlasCampaignSuiteRow.suite_digest == row.suite_digest)
                    .order_by(AtlasCampaignRow.campaign_digest)
                )
            ).all()
            for campaign_row in campaigns:
                campaign = _record(campaign_row, AtlasCampaignManifest)
                if row.suite_digest in campaign.promotion_suite_digests:
                    raise PermissionError("Atlas source overlaps a promotion partition")
                adaptive |= row.suite_digest in campaign.adaptive_suite_digests
            memberships.append({"suite": suite, "campaigns": [_columns(row) for row in campaigns]})
        return memberships, adaptive


async def _require[T: Base](session: AsyncSession, table: type[T], key: str) -> T:
    row = await session.get(table, key)
    if row is None:
        raise PermissionError("Atlas disclosure lost a required source record")
    return row


def _record[T: StrictRecord](row: Any, model: type[T]) -> T:
    if row is None:
        raise PermissionError("Atlas disclosure lost a required manifest")
    record = model.model_validate(row.record_json, strict=False)
    if hasattr(row, "record_digest") and row.record_digest != sha256_digest(record):
        raise PermissionError("Atlas disclosure source record digest differs")
    values = record.model_dump(mode="python")
    for column in row.__table__.columns:
        if column.name not in values:
            continue
        expected, actual = values[column.name], getattr(row, column.name)
        if isinstance(expected, (dict, tuple, list)):
            continue
        if isinstance(expected, Enum):
            expected = expected.value
        if isinstance(expected, datetime) and isinstance(actual, datetime):
            expected, actual = _utc(expected), _utc(actual)
        if expected != actual:
            raise PermissionError("Atlas disclosure source columns differ from immutable content")
    return record


def _columns(row: Any) -> dict[str, Any]:
    return {
        column.name: _utc(value) if isinstance(value, datetime) else value
        for column in row.__table__.columns
        for value in (getattr(row, column.name),)
    }


def _rights(rights: SourceRights, *, before: datetime) -> None:
    if (
        not rights.permits(RightsUse.INTERNAL_RESEARCH)
        or not rights.permits(RightsUse.EVIDENCE_RETENTION)
        or rights.reviewed_at is None
        or rights.reviewed_at > before
    ):
        raise PermissionError("Atlas source lacks reviewed retention and research rights")


def _digests(value: Any) -> set[str]:
    if isinstance(value, StrictRecord):
        return _digests(value.model_dump(mode="json"))
    if isinstance(value, str):
        return {value} if value.startswith("sha256:") else set()
    if isinstance(value, dict):
        return set().union(*(_digests(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(_digests(item) for item in value))
    return set()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
