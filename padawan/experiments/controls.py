from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    ComparabilityDisposition,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ResearchAxis,
    ResearchComparabilityAssessment,
    ResearchExecutionManifest,
    StudyManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.models.tables import (
    ExperimentRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    StudentStateRow,
    StudyExperimentRow,
    StudyRow,
)


@dataclass(frozen=True)
class ResearchControlConfiguration:
    """In-memory inputs used to mint one immutable execution manifest per run."""

    profile: HarnessProfile
    student_model: ModelServingIdentity
    auxiliary_models: tuple[ModelServingIdentity, ...]
    task: TaskCorpusIdentity
    harness_parameters: dict[str, str]
    environment: VersionedComponentIdentity
    environment_parameters: dict[str, str]
    environment_fingerprint: str

    def execution_manifest(
        self,
        *,
        execution_id: str,
        seed: int,
        created_at: datetime,
    ) -> ResearchExecutionManifest:
        profile_digest = sha256_digest(self.profile.model_dump(mode="json"))
        return ResearchExecutionManifest(
            execution_id=execution_id,
            harness_profile_id=self.profile.profile_id,
            harness_profile_version=self.profile.version,
            harness_profile_digest=profile_digest,
            student_model=self.student_model,
            auxiliary_models=self.auxiliary_models,
            task=self.task,
            harness_parameters=dict(sorted(self.harness_parameters.items())),
            environment=self.environment,
            environment_parameters=dict(sorted(self.environment_parameters.items())),
            environment_fingerprint=self.environment_fingerprint,
            seed=seed,
            created_at=created_at,
        )


@dataclass(frozen=True)
class StudyControlAssessment:
    study_id: str
    provenance_complete: bool
    comparable: bool
    execution_digests: tuple[str, ...]
    differing_axes: tuple[ResearchAxis, ...]
    declared_comparison_axes: tuple[ResearchAxis, ...]
    blocking_differences: tuple[ResearchAxis, ...]
    provenance_gaps: tuple[str, ...]


class ResearchControlRegistry:
    """Immutable harness/execution registry plus fail-closed comparison checks."""

    async def register_profile(
        self, session: AsyncSession, profile: HarnessProfile
    ) -> HarnessProfileRow:
        payload = profile.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(HarnessProfileRow, digest)
        if existing is not None:
            if existing.record_json != payload:
                raise ValueError("harness profile digest conflicts with persisted content")
            return existing
        identity_owner = await session.scalar(
            select(HarnessProfileRow).where(
                HarnessProfileRow.profile_id == profile.profile_id,
                HarnessProfileRow.version == profile.version,
            )
        )
        if identity_owner is not None:
            raise ValueError("harness profile version cannot be rewritten")
        row = HarnessProfileRow(
            profile_digest=digest,
            profile_id=profile.profile_id,
            version=profile.version,
            tier=profile.tier,
            purpose=profile.purpose,
            record_json=payload,
            created_at=profile.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_execution(
        self,
        session: AsyncSession,
        manifest: ResearchExecutionManifest,
        *,
        parent_state_id: str,
    ) -> ResearchExecutionRow:
        profile = await session.get(HarnessProfileRow, manifest.harness_profile_digest)
        if profile is None:
            raise ValueError("research execution cites an unregistered harness profile")
        if (profile.profile_id, profile.version) != (
            manifest.harness_profile_id,
            manifest.harness_profile_version,
        ):
            raise ValueError("research execution harness identity disagrees with its digest")
        state = await session.get(StudentStateRow, parent_state_id)
        if state is None:
            raise ValueError("research execution cites an unknown parent state")
        if state.checkpoint_id != manifest.student_model.checkpoint.component_id:
            raise ValueError("research execution checkpoint differs from parent state")
        if state.runtime_id != manifest.student_model.runtime.component_id:
            raise ValueError("research execution runtime differs from parent state")
        if state.research_role != manifest.student_model.research_role.value:
            raise ValueError("research execution role differs from parent state")
        if manifest.environment.digest != manifest.environment_fingerprint:
            raise ValueError("environment component digest must equal the environment fingerprint")

        payload = manifest.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(ResearchExecutionRow, digest)
        if existing is not None:
            if existing.record_json != payload:
                raise ValueError("research execution digest conflicts with persisted content")
            return existing
        identity_owner = await session.scalar(
            select(ResearchExecutionRow).where(
                ResearchExecutionRow.execution_id == manifest.execution_id
            )
        )
        if identity_owner is not None:
            raise ValueError("research execution ID cannot be rewritten")
        row = ResearchExecutionRow(
            execution_digest=digest,
            execution_id=manifest.execution_id,
            harness_profile_digest=manifest.harness_profile_digest,
            checkpoint_id=manifest.student_model.checkpoint.component_id,
            runtime_id=manifest.student_model.runtime.component_id,
            research_role=manifest.student_model.research_role.value,
            task_id=manifest.task.task_id,
            task_manifest_digest=manifest.task.task_manifest_digest,
            corpus_digest=manifest.task.corpus_digest,
            environment_fingerprint=manifest.environment_fingerprint,
            seed=manifest.seed,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def compare(
        self,
        session: AsyncSession,
        *,
        left_execution_digest: str,
        right_execution_digest: str,
        allowed_differences: tuple[ResearchAxis, ...] = (),
    ) -> ResearchComparabilityAssessment:
        allowed = tuple(sorted(set(allowed_differences), key=lambda axis: axis.value))
        left = await self._load_control(session, left_execution_digest)
        right = await self._load_control(session, right_execution_digest)
        gaps: list[str] = []
        if left is None:
            gaps.append(f"missing research execution: {left_execution_digest}")
        if right is None:
            gaps.append(f"missing research execution: {right_execution_digest}")
        differences: tuple[ResearchAxis, ...] = ()
        if left is not None and right is not None:
            left_manifest, left_profile = left
            right_manifest, right_profile = right
            differences = _differing_axes(
                ((left_manifest, left_profile), (right_manifest, right_profile))
            )
            gaps.extend(_provenance_gaps(left_manifest, left_profile, label="left"))
            gaps.extend(_provenance_gaps(right_manifest, right_profile, label="right"))
        blocking = tuple(axis for axis in differences if axis not in allowed)
        complete = not gaps
        disposition = (
            ComparabilityDisposition.INSUFFICIENT_PROVENANCE
            if not complete
            else ComparabilityDisposition.NOT_COMPARABLE
            if blocking
            else ComparabilityDisposition.CONTROLLED_DIFFERENCE
            if differences
            else ComparabilityDisposition.IDENTICAL_CONTROLS
        )
        return ResearchComparabilityAssessment(
            left_execution_digest=left_execution_digest,
            right_execution_digest=right_execution_digest,
            disposition=disposition,
            comparable=complete and not blocking,
            provenance_complete=complete,
            differing_axes=differences,
            allowed_differences=allowed,
            blocking_differences=blocking,
            provenance_gaps=tuple(sorted(set(gaps))),
        )

    async def validate_execution_configuration(
        self,
        session: AsyncSession,
        *,
        execution_digest: str,
        configuration: ResearchControlConfiguration,
        seed: int,
    ) -> None:
        """Reject a resume when the active process would change recorded controls."""

        control = await self._load_control(session, execution_digest)
        if control is None:
            raise ValueError("active run cites unavailable research controls")
        manifest, profile = control
        expected = configuration.execution_manifest(
            execution_id=manifest.execution_id,
            seed=seed,
            created_at=manifest.created_at,
        )
        if profile != configuration.profile or manifest != expected:
            raise ValueError("active run research controls differ from this invocation")

    async def assess_study(self, session: AsyncSession, *, study_id: str) -> StudyControlAssessment:
        study = await session.get(StudyRow, study_id)
        if study is None:
            raise KeyError(study_id)
        manifest = StudyManifest.model_validate(study.record_json, strict=False)
        bindings = (
            await session.scalars(
                select(StudyExperimentRow)
                .where(StudyExperimentRow.study_id == study_id)
                .order_by(StudyExperimentRow.experiment_id)
            )
        ).all()
        controls: list[tuple[ResearchExecutionManifest, HarnessProfile]] = []
        gaps: list[str] = []
        digests: list[str] = []
        for binding in bindings:
            experiment = await session.get(ExperimentRow, binding.experiment_id)
            if experiment is None:
                gaps.append(f"missing experiment: {binding.experiment_id}")
                continue
            digest = binding.research_execution_digest
            if digest is None:
                gaps.append(f"experiment has no research execution: {binding.experiment_id}")
                continue
            digests.append(digest)
            if experiment.research_execution_digest != digest:
                gaps.append(
                    f"study binding differs from experiment research execution: "
                    f"{binding.experiment_id}"
                )
                continue
            if experiment.design.get("research_execution_digest") != digest:
                gaps.append(
                    f"experiment design differs from research execution: {binding.experiment_id}"
                )
                continue
            control = await self._load_control(session, digest)
            if control is None:
                gaps.append(f"missing research execution: {digest}")
                continue
            execution, profile = control
            if binding.checkpoint_id != execution.student_model.checkpoint.component_id:
                gaps.append(f"checkpoint binding mismatch: {binding.experiment_id}")
            if binding.environment_fingerprint != execution.environment_fingerprint:
                gaps.append(f"environment binding mismatch: {binding.experiment_id}")
            gaps.extend(_provenance_gaps(execution, profile, label=binding.experiment_id))
            controls.append(control)
        differences = _differing_axes(tuple(controls)) if controls else ()
        blocking = tuple(axis for axis in differences if axis not in manifest.comparison_axes)
        complete = len(controls) == len(bindings) and bool(bindings) and not gaps
        return StudyControlAssessment(
            study_id=study_id,
            provenance_complete=complete,
            comparable=complete and not blocking,
            execution_digests=tuple(digests),
            differing_axes=differences,
            declared_comparison_axes=manifest.comparison_axes,
            blocking_differences=blocking,
            provenance_gaps=tuple(sorted(set(gaps))),
        )

    @staticmethod
    async def _load_control(
        session: AsyncSession, execution_digest: str
    ) -> tuple[ResearchExecutionManifest, HarnessProfile] | None:
        row = await session.get(ResearchExecutionRow, execution_digest)
        if row is None:
            return None
        profile_row = await session.get(HarnessProfileRow, row.harness_profile_digest)
        if profile_row is None:
            return None
        if row.execution_digest != sha256_digest(row.record_json):
            return None
        if profile_row.profile_digest != sha256_digest(profile_row.record_json):
            return None
        try:
            execution = ResearchExecutionManifest.model_validate(row.record_json, strict=False)
            profile = HarnessProfile.model_validate(profile_row.record_json, strict=False)
        except ValidationError:
            return None
        execution_columns = (
            row.execution_id,
            row.harness_profile_digest,
            row.checkpoint_id,
            row.runtime_id,
            row.research_role,
            row.task_id,
            row.task_manifest_digest,
            row.corpus_digest,
            row.environment_fingerprint,
            row.seed,
        )
        execution_record = (
            execution.execution_id,
            execution.harness_profile_digest,
            execution.student_model.checkpoint.component_id,
            execution.student_model.runtime.component_id,
            execution.student_model.research_role.value,
            execution.task.task_id,
            execution.task.task_manifest_digest,
            execution.task.corpus_digest,
            execution.environment_fingerprint,
            execution.seed,
        )
        if execution_columns != execution_record:
            return None
        if (
            profile_row.profile_id,
            profile_row.version,
            profile_row.tier,
            profile_row.purpose,
        ) != (profile.profile_id, profile.version, profile.tier, profile.purpose):
            return None
        if (
            execution.harness_profile_id,
            execution.harness_profile_version,
        ) != (profile.profile_id, profile.version):
            return None
        return execution, profile


def _differing_axes(
    controls: tuple[tuple[ResearchExecutionManifest, HarnessProfile], ...]
    | list[tuple[ResearchExecutionManifest, HarnessProfile]],
) -> tuple[ResearchAxis, ...]:
    if len(controls) < 2:
        return ()
    values = [_axis_payloads(execution, profile) for execution, profile in controls]
    return tuple(
        axis
        for axis in sorted(ResearchAxis, key=lambda item: item.value)
        if len({sha256_digest(payload[axis]) for payload in values}) > 1
    )


def _axis_payloads(
    execution: ResearchExecutionManifest, profile: HarnessProfile
) -> dict[ResearchAxis, Any]:
    student = execution.student_model
    return {
        ResearchAxis.CHECKPOINT: student.checkpoint.model_dump(mode="json"),
        ResearchAxis.QUANTIZATION: student.quantization.model_dump(mode="json"),
        ResearchAxis.TASK: execution.task.model_dump(mode="json"),
        ResearchAxis.HARNESS: {
            "tier": profile.tier,
            "purpose": profile.purpose,
            "parameters": execution.harness_parameters,
        },
        ResearchAxis.CONTINUATION: profile.continuation.model_dump(mode="json"),
        ResearchAxis.CONTEXT_POLICY: profile.context.model_dump(mode="json"),
        ResearchAxis.PROMPTS: [item.model_dump(mode="json") for item in profile.prompt_templates],
        ResearchAxis.TOOLS: [item.model_dump(mode="json") for item in profile.tools],
        ResearchAxis.BUDGET: profile.budgets.model_dump(mode="json"),
        ResearchAxis.SERVING: {
            "purpose": student.purpose,
            "research_role": student.research_role.value,
            "model_id": student.model_id,
            "runtime": student.runtime.model_dump(mode="json"),
            "serving_artifact": student.serving_artifact.model_dump(mode="json"),
            "protocol": student.protocol,
            "runtime_parameters": student.runtime_parameters,
            "runtime_parameters_digest": student.runtime_parameters_digest,
        },
        ResearchAxis.AUXILIARY_MODELS: [
            item.model_dump(mode="json") for item in execution.auxiliary_models
        ],
        ResearchAxis.ENVIRONMENT: {
            "identity": execution.environment.model_dump(mode="json"),
            "parameters": execution.environment_parameters,
            "fingerprint": execution.environment_fingerprint,
        },
        ResearchAxis.INSTRUMENTATION: profile.instrumentation.model_dump(mode="json"),
        ResearchAxis.SEED: execution.seed,
    }


def _provenance_gaps(
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
    *,
    label: str,
) -> tuple[str, ...]:
    components = [
        execution.student_model.checkpoint,
        execution.student_model.quantization,
        execution.student_model.runtime,
        execution.student_model.serving_artifact,
        execution.environment,
        *profile.prompt_templates,
        *profile.tools,
    ]
    for model in execution.auxiliary_models:
        components.extend(
            (model.checkpoint, model.quantization, model.runtime, model.serving_artifact)
        )
    gaps = [
        f"{label}: unknown component identity {component.component_id}@{component.version}"
        for component in components
        if component.evidence_status == IdentityEvidenceStatus.UNKNOWN
    ]
    if execution.task.evidence_status == IdentityEvidenceStatus.UNKNOWN:
        gaps.append(f"{label}: task or corpus identity is unknown")
    if (
        profile.context.configured_context_window_tokens is None
        and profile.context.effective_input_limit_tokens is None
    ):
        gaps.append(f"{label}: effective context limit is not established")
    return tuple(gaps)
