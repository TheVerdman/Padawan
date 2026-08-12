from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import ItemStatus, ResearchRole
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    ComparabilityDisposition,
    EvaluationSuiteManifest,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ParentStateIdentity,
    ResearchAxis,
    ResearchComparabilityAssessment,
    ResearchExecutionManifest,
    StudyManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.models.tables import (
    CorpusItemRow,
    EvaluationSuiteRow,
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
        parent_state: ParentStateIdentity,
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
            parent_state=parent_state,
            harness_parameters=dict(sorted(self.harness_parameters.items())),
            environment=self.environment,
            environment_parameters=dict(sorted(self.environment_parameters.items())),
            environment_fingerprint=self.environment_fingerprint,
            seed=seed,
            created_at=created_at,
        )


@dataclass(frozen=True)
class ResearchWorkerConfiguration:
    """Effective non-secret composition that may execute controlled work."""

    profile: HarnessProfile
    task: TaskCorpusIdentity
    corpus_competency_ids: tuple[str, ...]
    harness_parameters: dict[str, str]
    student_model: ModelServingIdentity
    auxiliary_models: tuple[ModelServingIdentity, ...]
    environment: VersionedComponentIdentity
    environment_parameters: dict[str, str]
    environment_fingerprint: str
    domain_id: str
    workflow: str


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
        if manifest.parent_state != parent_state_identity(state):
            raise ValueError("research execution parent-state identity differs from parent state")
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
            parent_state_id=manifest.parent_state.state_id,
            parent_state_hash=manifest.parent_state.state_hash,
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
            parent_state=manifest.parent_state,
        )
        if profile != configuration.profile or manifest != expected:
            raise ValueError("active run research controls differ from this invocation")

    async def validate_run_payload(
        self,
        session: AsyncSession,
        *,
        execution_digest: str,
        payload: dict[str, Any],
        retry_budget: int,
    ) -> None:
        """Reject controlled work whose durable payload contradicts its manifest."""

        control = await self._load_control(session, execution_digest)
        if control is None:
            raise ValueError("controlled run cites unavailable research controls")
        execution, profile = control
        for key in (
            "domain_id",
            "pool",
            "teacher_mode",
            "treatment_condition",
            "control_condition",
        ):
            expected = execution.harness_parameters.get(key)
            if expected is None:
                raise ValueError(f"research execution omits controlled payload field: {key}")
            if payload.get(key) != expected:
                raise ValueError(f"run {key} differs from its research execution")
        if execution.task.task_id != execution.harness_parameters["domain_id"]:
            raise ValueError("research execution task differs from its harness domain")
        if execution.task.split != execution.harness_parameters["pool"]:
            raise ValueError("research execution task split differs from its harness pool")
        retries = profile.budgets.retries
        if retries.disposition == BudgetDisposition.CAPPED and (
            retries.value is None or float(retry_budget) != retries.value
        ):
            raise ValueError("run retry budget differs from its harness profile")

    async def validate_experiment_conditions(
        self,
        session: AsyncSession,
        *,
        execution_digest: str,
        treatment_condition: str,
        control_condition: str,
    ) -> None:
        control = await self._load_control(session, execution_digest)
        if control is None:
            raise ValueError("controlled experiment cites unavailable research controls")
        execution, _profile = control
        if execution.harness_parameters.get("treatment_condition") != treatment_condition:
            raise ValueError("experiment treatment condition differs from its research execution")
        if execution.harness_parameters.get("control_condition") != control_condition:
            raise ValueError("experiment control condition differs from its research execution")

    async def compatible_execution_digests(
        self,
        session: AsyncSession,
        *,
        worker: ResearchWorkerConfiguration,
        require_current_corpus: bool = True,
    ) -> tuple[str, ...]:
        """Return executions this worker may start, or continue after sampling is frozen."""

        effective_task = worker.task
        if require_current_corpus and worker.corpus_competency_ids:
            rows = (
                await session.scalars(
                    select(CorpusItemRow)
                    .where(
                        CorpusItemRow.pool == worker.task.split,
                        CorpusItemRow.competency_id.in_(worker.corpus_competency_ids),
                        CorpusItemRow.status.in_(
                            (ItemStatus.ACTIVE.value, ItemStatus.LEASED.value)
                        ),
                    )
                    .order_by(CorpusItemRow.item_id)
                )
            ).all()
            effective_task = worker.task.model_copy(
                update={"corpus_digest": research_corpus_digest(rows)}
            )
        digests = (
            await session.scalars(
                select(ResearchExecutionRow.execution_digest).order_by(
                    ResearchExecutionRow.execution_digest
                )
            )
        ).all()
        compatible: list[str] = []
        for digest in digests:
            control = await self._load_control(session, digest)
            if control is None:
                continue
            execution, profile = control
            if _worker_matches_execution(
                worker,
                effective_task,
                execution,
                profile,
                require_current_corpus=require_current_corpus,
            ):
                compatible.append(digest)
        return tuple(compatible)

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
        suite_manifest: EvaluationSuiteManifest | None = None
        suite = await session.get(EvaluationSuiteRow, manifest.suite_manifest_digest)
        if suite is None:
            gaps.append("study evaluation suite is not registered")
        elif suite.manifest_digest != sha256_digest(suite.record_json):
            gaps.append("study evaluation suite digest is invalid")
        else:
            try:
                suite_manifest = EvaluationSuiteManifest.model_validate(
                    suite.record_json, strict=False
                )
            except ValidationError:
                gaps.append("study evaluation suite manifest is invalid")
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
            parent_state = await session.get(StudentStateRow, experiment.parent_state_id)
            if parent_state is None:
                gaps.append(f"experiment parent state is missing: {binding.experiment_id}")
            elif execution.parent_state != parent_state_identity(parent_state):
                gaps.append(f"parent-state binding mismatch: {binding.experiment_id}")
            if binding.checkpoint_id != execution.student_model.checkpoint.component_id:
                gaps.append(f"checkpoint binding mismatch: {binding.experiment_id}")
            if binding.environment_fingerprint != execution.environment_fingerprint:
                gaps.append(f"environment binding mismatch: {binding.experiment_id}")
            if experiment.design.get("treatment_condition") != execution.harness_parameters.get(
                "treatment_condition"
            ):
                gaps.append(f"treatment-condition mismatch: {binding.experiment_id}")
            if experiment.design.get("control_condition") != execution.harness_parameters.get(
                "control_condition"
            ):
                gaps.append(f"control-condition mismatch: {binding.experiment_id}")
            if (
                suite_manifest is not None
                and execution.task.task_manifest_digest not in suite_manifest.task_manifest_digests
            ):
                gaps.append(f"task is outside evaluation suite: {binding.experiment_id}")
            if (
                suite_manifest is not None
                and execution.environment_fingerprint not in suite_manifest.environment_fingerprints
            ):
                gaps.append(f"environment is outside evaluation suite: {binding.experiment_id}")
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
            row.parent_state_id,
            row.parent_state_hash,
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
            execution.parent_state.state_id,
            execution.parent_state.state_hash,
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
        parent_state = await session.get(StudentStateRow, execution.parent_state.state_id)
        if parent_state is None or execution.parent_state != parent_state_identity(parent_state):
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
        ResearchAxis.PARENT_STATE: execution.parent_state.model_dump(mode="json"),
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
            "transport_artifact": (
                student.transport_artifact.model_dump(mode="json")
                if student.transport_artifact is not None
                else None
            ),
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
    if execution.student_model.transport_artifact is not None:
        components.append(execution.student_model.transport_artifact)
    if profile.context.compactor is not None:
        components.append(profile.context.compactor)
    components.extend(
        component
        for component in (
            profile.instrumentation.capability_atlas_schema,
            profile.instrumentation.interactive_trajectory_schema,
            profile.instrumentation.mechanistic_telemetry_schema,
            profile.instrumentation.checkpoint_evaluation_schema,
        )
        if component is not None
    )
    for model in execution.auxiliary_models:
        components.extend(
            (model.checkpoint, model.quantization, model.runtime, model.serving_artifact)
        )
        if model.transport_artifact is not None:
            components.append(model.transport_artifact)
    gaps = [
        f"{label}: unknown component identity {component.component_id}@{component.version}"
        for component in components
        if component.evidence_status == IdentityEvidenceStatus.UNKNOWN
    ]
    for model in (execution.student_model, *execution.auxiliary_models):
        transport = model.transport_artifact
        if transport is not None and transport.evidence_status not in {
            IdentityEvidenceStatus.PINNED,
            IdentityEvidenceStatus.VERIFIED,
        }:
            gaps.append(
                f"{label}: transport artifact is not endpoint-verified "
                f"{transport.component_id}@{transport.version}"
            )
        if "edge_image_digest" in model.runtime_parameters and transport is None:
            gaps.append(f"{label}: serving edge has no transport artifact identity")
    if execution.task.evidence_status == IdentityEvidenceStatus.UNKNOWN:
        gaps.append(f"{label}: task or corpus identity is unknown")
    if (
        profile.context.configured_context_window_tokens is None
        and profile.context.effective_input_limit_tokens is None
    ):
        gaps.append(f"{label}: effective context limit is not established")
    return tuple(gaps)


def parent_state_identity(state: StudentStateRow) -> ParentStateIdentity:
    return ParentStateIdentity(
        state_id=state.state_id,
        state_hash=state.state_hash,
        student_id=state.student_id,
        checkpoint_id=state.checkpoint_id,
        runtime_id=state.runtime_id,
        research_role=ResearchRole(state.research_role),
        parent_state_id=state.parent_state_id,
        branch_id=state.branch_id,
    )


def _worker_matches_execution(
    worker: ResearchWorkerConfiguration,
    effective_task: TaskCorpusIdentity,
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
    *,
    require_current_corpus: bool,
) -> bool:
    task_matches = (
        execution.task == effective_task
        if require_current_corpus
        else execution.task.model_copy(update={"corpus_digest": worker.task.corpus_digest})
        == worker.task
    )
    return (
        profile == worker.profile
        and task_matches
        and execution.harness_parameters == worker.harness_parameters
        and execution.student_model == worker.student_model
        and execution.auxiliary_models == worker.auxiliary_models
        and execution.environment == worker.environment
        and execution.environment_parameters == worker.environment_parameters
        and execution.environment_fingerprint == worker.environment_fingerprint
        and execution.task.task_id == worker.domain_id
        and execution.harness_parameters.get("domain_id") == worker.domain_id
        and execution.harness_parameters.get("workflow") == worker.workflow
    )


def research_corpus_digest(rows: Sequence[CorpusItemRow]) -> str:
    """Digest the exact non-secret corpus inventory executable by a live worker."""

    eligible_statuses = {ItemStatus.ACTIVE.value, ItemStatus.LEASED.value}
    return sha256_digest(
        [
            {
                "item_id": row.item_id,
                "competency_id": row.competency_id,
                "template_family_id": row.template_family_id,
                "instance_group_id": row.instance_group_id,
                "generation_seed": row.generation_seed,
                "generator_version": row.generator_version,
                "difficulty": row.difficulty,
                "prompt_digest": sha256_digest(row.prompt),
                "expected_answer_digest": sha256_digest(row.expected_answer),
                "verifier_spec_digest": sha256_digest(row.verifier_spec),
                "pool": row.pool,
                "source": row.source,
                "rights_digest": row.rights_digest,
                "contamination_scope": row.contamination_scope,
                # Active-to-leased is a transient ownership change, not a corpus
                # mutation. Retirement or quarantine removes the item entirely.
                "status": "executable",
            }
            for row in sorted(
                (item for item in rows if item.status in eligible_statuses),
                key=lambda item: item.item_id,
            )
        ]
    )
