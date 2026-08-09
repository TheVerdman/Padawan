from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select

from padawan.adapters.base import (
    GenerationRequest,
    GenerationResult,
    rendered_messages,
    rendered_prompt,
)
from padawan.artifacts.store import (
    ArtifactBackend,
    ArtifactCatalog,
    artifact_put_bytes,
    artifact_put_text,
    redact_secrets,
)
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.developmental.contracts import DevelopmentalAuthority
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import (
    AttemptRecord,
    CorpusItemRecord,
    CorpusPool,
    DevelopmentalEpisode,
    ExposureRecord,
    ExposureType,
    GradeOutcome,
    GradeRecord,
    LifecycleStatus,
    MemoryWriteRecord,
    ResearchRole,
    RevisionRecord,
    RunState,
    StudentOutcome,
    StudentStateRecord,
    SystemOutcome,
    TeacherInterventionRecord,
    TeacherMode,
    TeachingOutcome,
    TransferTrialRecord,
    internally_generated_target_output_rights,
    unreviewed_provider_output_rights,
)
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AttemptRow,
    EpisodeRow,
    ExperimentBlockRow,
    ExposureRow,
    GradeRow,
    TeacherInterventionRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import ClaimedRun
from padawan.orchestration.supervisor import WorkResult
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.teaching.service import TeacherContext, TeacherExhaustedError, TeacherService
from padawan.updates.backends import ConsolidationBackend, ConsolidationDecision


class InventoryExhaustedError(RuntimeError):
    pass


class _BoundGenerationClient:
    def __init__(
        self,
        *,
        executor: IdempotentGenerationExecutor,
        run_id: str,
        purpose: str,
        provider: str,
    ) -> None:
        self.executor = executor
        self.run_id = run_id
        self.purpose = purpose
        self.provider = provider

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return await self.executor.execute(
            run_id=self.run_id,
            purpose=self.purpose,
            provider=self.provider,
            request=request,
        )


class DomainDevelopmentalWorkflowHandler:
    """Crash-resumable matched developmental workflow driven by domain authority.

    The control plane owns causal isolation, exposure, persistence, teacher validation,
    and memory governance. The supplied authority alone renders student requests and
    decides task correctness.
    """

    def __init__(
        self,
        *,
        authority: DevelopmentalAuthority,
        database: Database,
        artifacts: ArtifactBackend,
        registry: CorpusRegistry,
        states: StateStore,
        episodes: EpisodeStore,
        experiments: ExperimentEngine,
        provenance: ProvenanceLedger,
        student_calls: IdempotentGenerationExecutor,
        teacher_calls: IdempotentGenerationExecutor,
        teacher_provider: str,
        memory: LessonMemory,
        memory_backend: ConsolidationBackend,
        student_runtime_id: str,
        student_runtime_version: str,
        student_checkpoint_id: str,
        student_role: ResearchRole,
        lease_for: timedelta = timedelta(hours=2),
    ) -> None:
        self.authority = authority
        self.database = database
        self.artifacts = artifacts
        self.catalog = ArtifactCatalog(artifacts)
        self.registry = registry
        self.states = states
        self.episodes = episodes
        self.experiments = experiments
        self.provenance = provenance
        self.student_calls = student_calls
        self.teacher_calls = teacher_calls
        self.teacher_provider = teacher_provider
        self.memory = memory
        self.memory_backend = memory_backend
        self.student_runtime_id = student_runtime_id
        self.student_runtime_version = student_runtime_version
        self.student_checkpoint_id = student_checkpoint_id
        self.student_role = student_role
        self.lease_for = lease_for

    @classmethod
    def from_context(
        cls,
        *,
        authority: DevelopmentalAuthority,
        context: Any,
    ) -> DomainDevelopmentalWorkflowHandler:
        return cls(
            authority=authority,
            database=context.database,
            artifacts=context.artifacts,
            registry=context.corpus_registry,
            states=context.states,
            episodes=context.episodes,
            experiments=context.experiments,
            provenance=context.provenance,
            student_calls=context.student_calls,
            teacher_calls=context.teacher_calls,
            teacher_provider=context.teacher_provider,
            memory=context.memory,
            memory_backend=context.memory_backend,
            student_runtime_id=context.student_runtime_id,
            student_runtime_version=context.student_runtime_version,
            student_checkpoint_id=context.student_checkpoint_id,
            student_role=context.student_role,
            lease_for=context.lease_for,
        )

    async def handle(self, run: ClaimedRun) -> WorkResult:
        run_role = ResearchRole(str(run.payload.get("research_role", ResearchRole.TARGET.value)))
        if run_role != run.research_role or run_role != self.student_role:
            raise RuntimeError("persisted, payload, and runtime research roles must match")
        selected_domain = run.payload.get("domain_id")
        if selected_domain is not None and str(selected_domain) != self.authority.domain_id:
            raise RuntimeError("run domain differs from the selected developmental authority")
        handlers = {
            RunState.CREATED: self._lease_items,
            RunState.DOMAIN_ITEMS_LEASED: self._fork_state,
            RunState.DOMAIN_STATE_FORKED: self._run_cold,
            RunState.DOMAIN_COLD_ATTEMPT_STORED: self._verify_cold,
            RunState.DOMAIN_COLD_VERIFIED: self._run_teacher,
            RunState.DOMAIN_TEACHER_STORED: self._run_revision,
            RunState.DOMAIN_REVISION_VERIFIED: self._run_transfer,
            RunState.DOMAIN_TRANSFER_VERIFIED: self._decide_memory,
            RunState.DOMAIN_MEMORY_DECIDED: self._commit_episode,
            RunState.DOMAIN_EPISODE_COMMITTED: self._complete,
        }
        handler = handlers.get(run.state)
        if handler is None:
            raise RuntimeError(
                f"no {self.authority.domain_id} developmental handler for {run.state.value}"
            )
        return await handler(run)

    async def _lease_items(self, run: ClaimedRun) -> WorkResult:
        pool = CorpusPool(str(run.payload.get("pool", CorpusPool.CURRICULUM.value)))
        requested_competency = (
            str(run.payload["competency_id"]) if run.payload.get("competency_id") else None
        )
        if (
            requested_competency is not None
            and requested_competency not in self.authority.competency_ids
        ):
            raise RuntimeError("requested competency does not belong to the selected domain")
        async with self.database.transaction() as session:
            leases = await self.registry.lease_matched_group(
                session,
                owner=run.run_id,
                pool=pool,
                count=3,
                lease_for=self.lease_for,
                student_id=str(run.payload["student_id"]),
                competency_id=requested_competency,
                competency_ids=(
                    None if requested_competency is not None else self.authority.competency_ids
                ),
            )
            if len(leases) != 3:
                raise InventoryExhaustedError(
                    f"no unseen three-sibling group is available for {self.authority.domain_id}"
                )
            episode_id = f"episode-{run.run_id}"
            await self.episodes.create(
                session,
                episode_id=episode_id,
                student_id=str(run.payload["student_id"]),
                state_before_id=str(run.payload["state_id"]),
                item_id=leases[0].item.item_id,
            )
            event_id = await self._event(
                session,
                run,
                event_type="DomainCorpusItemsLeased",
                payload={
                    "domain_id": self.authority.domain_id,
                    "episode_id": episode_id,
                    "item_ids": [lease.item.item_id for lease in leases],
                    "instance_group_id": leases[0].item.instance_group_id,
                    "pool": pool.value,
                },
                episode_id=episode_id,
            )
        return self._result(
            RunState.DOMAIN_ITEMS_LEASED,
            run,
            {
                "domain_id": self.authority.domain_id,
                "episode_id": episode_id,
                "leases": [
                    {
                        "item": lease.item.model_dump(mode="json"),
                        "token": lease.token,
                        "expires_at": lease.expires_at.isoformat(),
                    }
                    for lease in leases
                ],
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _fork_state(self, run: ClaimedRun) -> WorkResult:
        leases = run.payload["leases"]
        transfer_a = leases[1]["item"]
        transfer_b = leases[2]["item"]
        experiment_id = f"experiment-{run.run_id}"
        async with self.database.transaction() as session:
            state = await self.states.get(session, state_id=str(run.payload["state_id"]))
            if state.research_role != self.student_role:
                raise RuntimeError("student state role differs from the selected runtime role")
            _, assignments = await self.experiments.create(
                session,
                parent_state_id=state.state_id,
                seed=int(run.payload.get("experiment_seed", 0)),
                blocks=(
                    MatchedBlock(
                        instance_group_id=str(transfer_a["instance_group_id"]),
                        item_a_id=str(transfer_a["item_id"]),
                        item_b_id=str(transfer_b["item_id"]),
                    ),
                ),
                treatment_condition=str(
                    run.payload.get("treatment_condition", "frontier_teacher_critique")
                ),
                control_condition=str(run.payload.get("control_condition", "no_intervention")),
                experiment_id=experiment_id,
            )
            block = await session.get(ExperimentBlockRow, assignments[0].block_id)
            if block is None:
                raise RuntimeError("experiment block was not persisted")
            assignment = dict(block.assignment)
            event_id = await self._event(
                session,
                run,
                event_type="DomainStateForkCreated",
                payload={
                    "domain_id": self.authority.domain_id,
                    "experiment_id": experiment_id,
                    "block_id": block.block_id,
                    "assignment": assignment,
                    "base_state_hash": state.state_hash,
                },
                state_lineage_id=state.state_id,
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.DOMAIN_STATE_FORKED,
            run,
            {
                "base_state_hash": state.state_hash,
                "experiment_id": experiment_id,
                "experiment_block_id": assignments[0].block_id,
                **assignment,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _run_cold(self, run: ClaimedRun) -> WorkResult:
        item = self._item(run, 0)
        state = await self._load_state(str(run.payload["state_id"]))
        request_id = f"{run.run_id}:student:cold"
        retrieval = await self._retrieve_lessons(
            state=state,
            item=item,
            request_id=request_id,
        )
        request = self.authority.build_student_request(
            request_id=request_id,
            item=item,
            state=state,
            intervention=None,
            retrieved_lessons=retrieval,
        )
        exposure_id = await self._persist_exposure(
            run,
            exposure_id=f"exposure-{run.run_id}-cold-prompt",
            lease=self._lease(run, item.item_id),
            state_id=state.state_id,
            exposure_type=ExposureType.PROMPT,
            prompt_exposed=True,
        )
        generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="domain_cold_attempt",
            provider="student_runtime",
            request=request,
        )
        attempt = await self._attempt_from_generation(
            generation,
            episode_id=str(run.payload["episode_id"]),
            item=item,
            state_id=state.state_id,
            request=request,
            label="cold",
        )
        async with self.database.transaction() as session:
            await self.episodes.store_attempt(session, attempt)
            event_id = await self._event(
                session,
                run,
                event_type="DomainStudentAttemptStored",
                payload={"attempt_id": attempt.attempt_id, "kind": "cold"},
                state_lineage_id=state.state_id,
                episode_id=attempt.episode_id,
            )
        return self._result(
            RunState.DOMAIN_COLD_ATTEMPT_STORED,
            run,
            {
                "cold_attempt_id": attempt.attempt_id,
                "exposure_ids": self._exposure_ids(run, exposure_id),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _verify_cold(self, run: ClaimedRun) -> WorkResult:
        item = self._item(run, 0)
        attempt = await self._load_attempt(str(run.payload["cold_attempt_id"]))
        grade = await self._load_grade_for_attempt(attempt.attempt_id)
        if grade is None:
            grade = await self.authority.grade_attempt(
                run_id=run.run_id,
                phase="cold",
                item=item,
                attempt=attempt,
            )
        async with self.database.transaction() as session:
            grade = await self.episodes.store_grade(session, grade)
            event_id = await self._event(
                session,
                run,
                event_type="DomainAttemptVerified",
                payload={
                    "attempt_id": attempt.attempt_id,
                    "grade_id": grade.grade_id,
                    "phase": "cold",
                    "outcome": grade.outcome.value,
                    "score": grade.score,
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.DOMAIN_COLD_VERIFIED,
            run,
            {
                "cold_grade_id": grade.grade_id,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _run_teacher(self, run: ClaimedRun) -> WorkResult:
        item = self._item(run, 0)
        attempt = await self._load_attempt(str(run.payload["cold_attempt_id"]))
        grade = await self._load_grade(str(run.payload["cold_grade_id"]))
        state = await self._load_state(str(run.payload["state_id"]))
        teacher_memory = await self._retrieve_lessons(
            state=state,
            item=item,
            request_id=f"{run.run_id}:teacher:context",
            error_class=grade.error_class,
        )
        mode = TeacherMode(
            str(run.payload.get("teacher_mode", TeacherMode.DIAGNOSTIC_CRITIQUE.value))
        )
        teacher = TeacherService(
            client=_BoundGenerationClient(
                executor=self.teacher_calls,
                run_id=run.run_id,
                purpose="domain_teacher_intervention",
                provider=self.teacher_provider,
            ),
            artifacts=self.artifacts,
        )
        forbidden = tuple(
            answer
            for answer in (
                self.authority.forbidden_transfer_answer(self._item(run, 1)),
                self.authority.forbidden_transfer_answer(self._item(run, 2)),
            )
            if answer is not None
        )
        context = TeacherContext(
            episode_id=str(run.payload["episode_id"]),
            task=self.authority.teacher_task(item),
            attempt=attempt,
            grade=grade,
            student_state=state,
            mode=mode,
            guidance_budget_tokens=int(run.payload.get("guidance_budget_tokens", 1200)),
            expected_answer=self.authority.expected_answer(item),
            prior_lessons=tuple(teacher_memory["selected"]),
            private_reasoning=None,
            private_reasoning_policy_allows=False,
            forbidden_transfer_answers=forbidden,
        )
        try:
            teacher_result = await teacher.request_with_validation(
                context,
                max_attempts=int(run.payload.get("teacher_retry_attempts", 3)),
                request_id_prefix=f"{run.run_id}:teacher",
            )
        except TeacherExhaustedError as exc:
            async with self.database.transaction() as session:
                for index, failure in enumerate(exc.failures):
                    await self.catalog.reference(
                        session,
                        failure.raw_response_ref,
                        owner_type="teacher_failure",
                        owner_id=f"{run.run_id}:{index}",
                    )
                await self._release_item_leases(session, run)
                event_id = await self._event(
                    session,
                    run,
                    event_type="DomainTeacherResponsesRejected",
                    payload={
                        "failure_count": len(exc.failures),
                        "request_ids": [failure.request_id for failure in exc.failures],
                    },
                    episode_id=str(run.payload["episode_id"]),
                )
                await self._commit_incomplete_episode(
                    session,
                    run,
                    status="review_required",
                    final_event_id=event_id,
                    teaching_outcome=TeachingOutcome.UNSUPPORTED_CRITIQUE,
                    system_outcomes=(),
                )
            return self._result(
                RunState.REVIEW_REQUIRED,
                run,
                {"provenance_event_ids": self._events(run, event_id)},
                {"teacher_failure_count": len(exc.failures)},
            )
        async with self.database.transaction() as session:
            for index, failure in enumerate(teacher_result.failed_attempts):
                await self.catalog.reference(
                    session,
                    failure.raw_response_ref,
                    owner_type="teacher_failure",
                    owner_id=f"{run.run_id}:{index}",
                )
            intervention = await self.episodes.store_intervention(
                session, teacher_result.intervention
            )
            event_id = await self._event(
                session,
                run,
                event_type="DomainTeacherInterventionValidated",
                payload={
                    "intervention_id": intervention.intervention_id,
                    "mode": intervention.mode.value,
                    "failed_teacher_attempts": len(teacher_result.failed_attempts),
                },
                episode_id=intervention.episode_id,
            )
        return self._result(
            RunState.DOMAIN_TEACHER_STORED,
            run,
            {
                "intervention_id": intervention.intervention_id,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _run_revision(self, run: ClaimedRun) -> WorkResult:
        item = self._item(run, 0)
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        state = await self._load_state(str(run.payload["treatment_state_id"]))
        request_id = f"{run.run_id}:student:revision"
        retrieval = await self._retrieve_lessons(
            state=state,
            item=item,
            request_id=request_id,
            error_class=intervention.error_class,
        )
        request = self.authority.build_student_request(
            request_id=request_id,
            item=item,
            state=state,
            intervention={"lesson": intervention.lesson, "repair": intervention.repair},
            retrieved_lessons=retrieval,
        )
        exposure_id = await self._persist_exposure(
            run,
            exposure_id=f"exposure-{run.run_id}-cold-feedback",
            lease=self._lease(run, item.item_id),
            state_id=state.state_id,
            exposure_type=ExposureType.CRITIQUE,
            prompt_exposed=True,
            critique_exposed=True,
            repair_exposed=True,
        )
        generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="domain_revision_attempt",
            provider="student_runtime",
            request=request,
        )
        attempt = await self._attempt_from_generation(
            generation,
            episode_id=str(run.payload["episode_id"]),
            item=item,
            state_id=state.state_id,
            request=request,
            label="revision",
            influence_refs=(intervention.intervention_id,),
        )
        grade = await self._load_grade_for_attempt(attempt.attempt_id)
        if grade is None:
            grade = await self.authority.grade_attempt(
                run_id=run.run_id,
                phase="revision",
                item=item,
                attempt=attempt,
            )
        async with self.database.transaction() as session:
            attempt = await self.episodes.store_attempt(session, attempt)
            grade = await self.episodes.store_grade(session, grade)
            revision = RevisionRecord(
                revision_id=f"revision-{run.run_id}",
                original_attempt_id=str(run.payload["cold_attempt_id"]),
                intervention_id=intervention.intervention_id,
                revised_attempt_id=attempt.attempt_id,
                revised_grade_id=grade.grade_id,
                created_at=datetime.now(UTC),
            )
            await self.episodes.store_revision(session, revision)
            event_id = await self._event(
                session,
                run,
                event_type="DomainRevisionVerified",
                payload={
                    "attempt_id": attempt.attempt_id,
                    "grade_id": grade.grade_id,
                    "revision_id": revision.revision_id,
                    "outcome": grade.outcome.value,
                },
                state_lineage_id=state.state_id,
                episode_id=attempt.episode_id,
            )
        return self._result(
            RunState.DOMAIN_REVISION_VERIFIED,
            run,
            {
                "revision_attempt_id": attempt.attempt_id,
                "revision_grade_id": grade.grade_id,
                "revision_id": revision.revision_id,
                "exposure_ids": self._exposure_ids(run, exposure_id),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _run_transfer(self, run: ClaimedRun) -> WorkResult:
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        treatment_item = self._item_by_id(run, str(run.payload["treatment_item_id"]))
        control_item = self._item_by_id(run, str(run.payload["control_item_id"]))
        treatment_state = await self._load_state(str(run.payload["treatment_state_id"]))
        control_state = await self._load_state(str(run.payload["control_state_id"]))
        treatment_request_id = f"{run.run_id}:student:transfer:treatment"
        control_request_id = f"{run.run_id}:student:transfer:control"
        treatment_retrieval = await self._retrieve_lessons(
            state=treatment_state,
            item=treatment_item,
            request_id=treatment_request_id,
            error_class=intervention.error_class,
        )
        control_retrieval = await self._retrieve_lessons(
            state=control_state,
            item=control_item,
            request_id=control_request_id,
            error_class=intervention.error_class,
        )
        treatment_request = self.authority.build_student_request(
            request_id=treatment_request_id,
            item=treatment_item,
            state=treatment_state,
            intervention={"lesson": intervention.lesson, "repair": intervention.repair},
            retrieved_lessons=treatment_retrieval,
        )
        control_condition = str(run.payload.get("control_condition", "no_intervention"))
        control_intervention = (
            {"generic": "Check your work carefully before answering."}
            if control_condition == "generic_check_work"
            else None
        )
        control_request = self.authority.build_student_request(
            request_id=control_request_id,
            item=control_item,
            state=control_state,
            intervention=control_intervention,
            retrieved_lessons=control_retrieval,
        )
        treatment_exposure = await self._persist_exposure(
            run,
            exposure_id=f"exposure-{run.run_id}-{treatment_item.item_id}",
            lease=self._lease(run, treatment_item.item_id),
            state_id=treatment_state.state_id,
            exposure_type=ExposureType.PROMPT,
            prompt_exposed=True,
        )
        control_exposure = await self._persist_exposure(
            run,
            exposure_id=f"exposure-{run.run_id}-{control_item.item_id}",
            lease=self._lease(run, control_item.item_id),
            state_id=control_state.state_id,
            exposure_type=ExposureType.PROMPT,
            prompt_exposed=True,
        )
        treatment_generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="domain_transfer_treatment",
            provider="student_runtime",
            request=treatment_request,
        )
        control_generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="domain_transfer_control",
            provider="student_runtime",
            request=control_request,
        )
        treatment_attempt = await self._attempt_from_generation(
            treatment_generation,
            episode_id=str(run.payload["episode_id"]),
            item=treatment_item,
            state_id=treatment_state.state_id,
            request=treatment_request,
            label="transfer-treatment",
            influence_refs=(intervention.intervention_id,),
        )
        control_attempt = await self._attempt_from_generation(
            control_generation,
            episode_id=str(run.payload["episode_id"]),
            item=control_item,
            state_id=control_state.state_id,
            request=control_request,
            label="transfer-control",
        )
        treatment_grade = await self._load_grade_for_attempt(treatment_attempt.attempt_id)
        if treatment_grade is None:
            treatment_grade = await self.authority.grade_attempt(
                run_id=run.run_id,
                phase="transfer-treatment",
                item=treatment_item,
                attempt=treatment_attempt,
            )
        control_grade = await self._load_grade_for_attempt(control_attempt.attempt_id)
        if control_grade is None:
            control_grade = await self.authority.grade_attempt(
                run_id=run.run_id,
                phase="transfer-control",
                item=control_item,
                attempt=control_attempt,
            )
        infrastructure_failures = tuple(
            label
            for label, grade in (
                ("treatment", treatment_grade),
                ("control", control_grade),
            )
            if grade.infrastructure_failure
        )
        contamination_checks = {
            "different_item_ids": treatment_item.item_id != control_item.item_id,
            "same_instance_group": (
                treatment_item.instance_group_id == control_item.instance_group_id
            ),
            "fresh_from_cold_item": (
                treatment_item.item_id != self._item(run, 0).item_id
                and control_item.item_id != self._item(run, 0).item_id
            ),
            "separate_branches": (
                run.payload["treatment_branch_id"] != run.payload["control_branch_id"]
            ),
        }
        async with self.database.transaction() as session:
            treatment_attempt = await self.episodes.store_attempt(session, treatment_attempt)
            control_attempt = await self.episodes.store_attempt(session, control_attempt)
            treatment_grade = await self.episodes.store_grade(session, treatment_grade)
            control_grade = await self.episodes.store_grade(session, control_grade)
            transfer = TransferTrialRecord(
                transfer_trial_id=f"transfer-{run.run_id}",
                source_intervention_id=intervention.intervention_id,
                inherited_state_id=str(run.payload["state_id"]),
                item_id=f"{treatment_item.item_id}|{control_item.item_id}",
                treatment_condition=str(
                    run.payload.get("treatment_condition", "frontier_teacher_critique")
                ),
                control_condition=control_condition,
                treatment_attempt_id=treatment_attempt.attempt_id,
                control_attempt_id=control_attempt.attempt_id,
                treatment_grade_id=treatment_grade.grade_id,
                control_grade_id=control_grade.grade_id,
                delayed_retest_at=None,
                contamination_checks=contamination_checks,
                created_at=datetime.now(UTC),
            )
            await self.episodes.store_transfer(
                session,
                episode_id=str(run.payload["episode_id"]),
                transfer=transfer,
                experiment_id=str(run.payload["experiment_id"]),
            )
            await self.experiments.record_block(
                session,
                block_id=str(run.payload["experiment_block_id"]),
                treatment_success=(
                    None
                    if treatment_grade.infrastructure_failure
                    else treatment_grade.outcome == GradeOutcome.CORRECT
                ),
                control_success=(
                    None
                    if control_grade.infrastructure_failure
                    else control_grade.outcome == GradeOutcome.CORRECT
                ),
                treatment_score=(
                    None if treatment_grade.infrastructure_failure else treatment_grade.score
                ),
                control_score=(
                    None if control_grade.infrastructure_failure else control_grade.score
                ),
                contamination_checks=contamination_checks,
                infrastructure_failures=infrastructure_failures,
            )
            event_id = await self._event(
                session,
                run,
                event_type="DomainTransferVerified",
                payload={
                    "transfer_trial_id": transfer.transfer_trial_id,
                    "treatment_grade_id": treatment_grade.grade_id,
                    "control_grade_id": control_grade.grade_id,
                    "treatment_score": treatment_grade.score,
                    "control_score": control_grade.score,
                    "infrastructure_failures": list(infrastructure_failures),
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.DOMAIN_TRANSFER_VERIFIED,
            run,
            {
                "transfer_trial_id": transfer.transfer_trial_id,
                "treatment_attempt_id": treatment_attempt.attempt_id,
                "control_attempt_id": control_attempt.attempt_id,
                "treatment_grade_id": treatment_grade.grade_id,
                "control_grade_id": control_grade.grade_id,
                "exposure_ids": self._exposure_ids(run, treatment_exposure, control_exposure),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _decide_memory(self, run: ClaimedRun) -> WorkResult:
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        treatment_grade = await self._load_grade(str(run.payload["treatment_grade_id"]))
        control_grade = await self._load_grade(str(run.payload["control_grade_id"]))
        treatment_success = treatment_grade.outcome == GradeOutcome.CORRECT
        control_success = control_grade.outcome == GradeOutcome.CORRECT
        item = self._item(run, 0)
        async with self.database.transaction() as session:
            if self.student_role == ResearchRole.BASELINE:
                decision = ConsolidationDecision(
                    proposal_id=f"baseline-no-consolidation-{run.run_id}",
                    accepted=False,
                    reason="baseline roles cannot consolidate target memory",
                    before_snapshot_id=None,
                    after_snapshot_id=None,
                    lesson_id=None,
                )
            else:
                decision = await self.memory_backend.propose(
                    session,
                    student_id=str(run.payload["student_id"]),
                    state_lineage_id=str(run.payload["student_id"]),
                    branch_id=str(run.payload["treatment_branch_id"]),
                    competency_id=item.competency_id,
                    error_class=intervention.error_class or "domain_teacher_diagnosed",
                    general_rule=intervention.lesson,
                    applicability=intervention.expected_transfer_scope,
                    exclusions=(),
                    evidence_ids=tuple(
                        evidence.evidence_id for evidence in treatment_grade.evidence
                    ),
                    source_episode_ids=(str(run.payload["episode_id"]),),
                    successful_transfer_count=int(treatment_success and not control_success),
                    failed_transfer_count=int(not treatment_success),
                    confidence=intervention.confidence,
                    teacher_id=intervention.model_id,
                )
            event_id = await self._event(
                session,
                run,
                event_type="DomainMemoryConsolidationDecided",
                payload={
                    "proposal_id": decision.proposal_id,
                    "accepted": decision.accepted,
                    "reason": decision.reason,
                    "lesson_id": decision.lesson_id,
                },
                state_lineage_id=str(run.payload["state_id"]),
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.DOMAIN_MEMORY_DECIDED,
            run,
            {
                "memory_decision": decision.__dict__,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _commit_episode(self, run: ClaimedRun) -> WorkResult:
        cold_grade = await self._load_grade(str(run.payload["cold_grade_id"]))
        revision_grade = await self._load_grade(str(run.payload["revision_grade_id"]))
        treatment_grade = await self._load_grade(str(run.payload["treatment_grade_id"]))
        control_grade = await self._load_grade(str(run.payload["control_grade_id"]))
        treatment_wins = treatment_grade.score > control_grade.score
        chosen_state_id = str(
            run.payload["treatment_state_id"] if treatment_wins else run.payload["control_state_id"]
        )
        memory_decision = dict(run.payload["memory_decision"])
        async with self.database.transaction() as session:
            retired_ids = await self._close_exposures(session, run)
            chosen = await self.states.get(session, state_id=chosen_state_id)
            working_state = dict(chosen.compacted_working_state)
            working_state["last_domain_episode"] = {
                "domain_id": self.authority.domain_id,
                "episode_id": run.payload["episode_id"],
                "cold_score": cold_grade.score,
                "revision_score": revision_grade.score,
                "treatment_transfer_score": treatment_grade.score,
                "control_transfer_score": control_grade.score,
            }
            lesson_refs = tuple(chosen.lesson_memory_refs)
            if (
                treatment_wins
                and memory_decision.get("accepted")
                and memory_decision.get("lesson_id")
            ):
                lesson_refs = (*lesson_refs, str(memory_decision["lesson_id"]))
            state_after = await self.states.append_state(
                session,
                parent_state_id=chosen_state_id,
                compacted_working_state=working_state,
                lesson_memory_refs=lesson_refs,
                creation_reason=(
                    f"completed {self.authority.domain_id} episode {run.payload['episode_id']}"
                ),
                lifecycle_status=LifecycleStatus.CANONICAL,
            )
            await self.states.promote_canonical(session, state_id=state_after.state_id)
            teaching_outcome = _teaching_outcome(
                cold_grade, revision_grade, treatment_grade, control_grade
            )
            student_outcome = _student_outcome(treatment_grade)
            event_id = await self._event(
                session,
                run,
                event_type="DomainDevelopmentalEpisodeCommitted",
                payload={
                    "domain_id": self.authority.domain_id,
                    "episode_id": run.payload["episode_id"],
                    "state_after_id": state_after.state_id,
                    "canonical_branch": chosen.branch_id,
                    "teaching_outcome": teaching_outcome.value,
                    "retired_item_ids": list(retired_ids),
                },
                state_lineage_id=str(run.payload["state_id"]),
                episode_id=str(run.payload["episode_id"]),
            )
            provenance_ids = self._events(run, event_id)
            memory_write = MemoryWriteRecord(
                lesson_id=str(memory_decision.get("lesson_id") or "none"),
                action="write" if memory_decision.get("accepted") else "none",
                reason=str(memory_decision["reason"]),
            )
            episode_row = await session.get(EpisodeRow, str(run.payload["episode_id"]))
            if episode_row is None:
                raise RuntimeError("episode row disappeared")
            domain_evidence = tuple(
                dict.fromkeys(
                    evidence.evidence_id
                    for grade in (
                        cold_grade,
                        revision_grade,
                        treatment_grade,
                        control_grade,
                    )
                    for evidence in grade.evidence
                )
            )
            episode = DevelopmentalEpisode(
                episode_id=str(run.payload["episode_id"]),
                student_state_before_id=str(run.payload["state_id"]),
                task_item_id=self._item(run, 0).item_id,
                research_role=self.student_role,
                initial_attempt_id=str(run.payload["cold_attempt_id"]),
                grade_id=cold_grade.grade_id,
                diagnosis_ids=tuple(evidence.evidence_id for evidence in cold_grade.evidence),
                intervention_id=str(run.payload["intervention_id"]),
                revision_id=str(run.payload["revision_id"]),
                transfer_trial_ids=(str(run.payload["transfer_trial_id"]),),
                student_state_after_id=state_after.state_id,
                memory_writes=(memory_write,),
                exposure_ids=tuple(str(value) for value in run.payload["exposure_ids"]),
                retirement_ids=retired_ids,
                student_outcome=student_outcome,
                teaching_outcome=teaching_outcome,
                system_outcomes=(),
                pedagogical_metrics={
                    "revision_gain": revision_grade.score - cold_grade.score,
                    "immediate_transfer_gain": treatment_grade.score - control_grade.score,
                    "treatment_transfer_score": treatment_grade.score,
                    "control_transfer_score": control_grade.score,
                },
                provenance_event_ids=tuple(provenance_ids),
                domain_evidence_refs=domain_evidence,
                consolidation_proposal_ids=(str(memory_decision["proposal_id"]),),
                status="complete",
                created_at=episode_row.created_at,
                completed_at=datetime.now(UTC),
            )
            await self.episodes.commit(session, episode)
        return self._result(
            RunState.DOMAIN_EPISODE_COMMITTED,
            run,
            {
                "state_after_id": state_after.state_id,
                "retired_item_ids": list(retired_ids),
                "student_outcome": student_outcome.value,
                "teaching_outcome": teaching_outcome.value,
                "provenance_event_ids": provenance_ids,
            },
        )

    async def _complete(self, run: ClaimedRun) -> WorkResult:
        return self._result(
            RunState.COMPLETE,
            run,
            {},
            {
                "domain_id": self.authority.domain_id,
                "episode_id": run.payload["episode_id"],
                "state_after_id": run.payload["state_after_id"],
            },
        )

    async def record_failure(
        self, run: ClaimedRun, error: Exception, terminal_state: RunState
    ) -> None:
        episode_id = run.payload.get("episode_id")
        if not episode_id or terminal_state != RunState.FAILED_TERMINAL:
            return
        error_name = type(error).__name__
        system_outcome = (
            SystemOutcome.PROVIDER_FAILURE
            if error_name == "ModelProviderError"
            else SystemOutcome.RUNTIME_FAILURE
        )
        async with self.database.transaction() as session:
            episode_row = await session.get(EpisodeRow, str(episode_id))
            if episode_row is None or episode_row.status != "active":
                return
            await self._release_item_leases(session, run)
            event_id = await self._event(
                session,
                run,
                event_type="DomainDevelopmentalEpisodeFailed",
                payload={
                    "domain_id": self.authority.domain_id,
                    "episode_id": episode_id,
                    "failed_from_state": run.state.value,
                    "error_class": error_name,
                    "message": redact_secrets(str(error)),
                    "system_outcome": system_outcome.value,
                },
                state_lineage_id=str(run.payload.get("state_id") or "") or None,
                episode_id=str(episode_id),
            )
            await self._commit_incomplete_episode(
                session,
                run,
                status="failed",
                final_event_id=event_id,
                teaching_outcome=None,
                system_outcomes=(system_outcome,),
            )

    async def _commit_incomplete_episode(
        self,
        session: Any,
        run: ClaimedRun,
        *,
        status: Literal["failed", "review_required"],
        final_event_id: str,
        teaching_outcome: TeachingOutcome | None,
        system_outcomes: tuple[SystemOutcome, ...],
    ) -> None:
        episode_id = str(run.payload["episode_id"])
        episode_row = await session.get(EpisodeRow, episode_id)
        if episode_row is None:
            raise RuntimeError("incomplete episode row disappeared")
        cold_grade: GradeRecord | None = None
        if run.payload.get("cold_grade_id"):
            grade_row = await session.get(GradeRow, str(run.payload["cold_grade_id"]))
            if grade_row is not None:
                cold_grade = GradeRecord.model_validate(grade_row.record_json, strict=False)
        stored_exposure_ids = tuple(
            (
                await session.scalars(
                    select(ExposureRow.exposure_id).where(ExposureRow.episode_id == episode_id)
                )
            ).all()
        )
        episode = DevelopmentalEpisode(
            episode_id=episode_id,
            student_state_before_id=str(run.payload["state_id"]),
            task_item_id=self._item(run, 0).item_id,
            research_role=self.student_role,
            initial_attempt_id=(
                str(run.payload["cold_attempt_id"]) if run.payload.get("cold_attempt_id") else None
            ),
            grade_id=(
                str(run.payload["cold_grade_id"]) if run.payload.get("cold_grade_id") else None
            ),
            intervention_id=(
                str(run.payload["intervention_id"]) if run.payload.get("intervention_id") else None
            ),
            revision_id=(
                str(run.payload["revision_id"]) if run.payload.get("revision_id") else None
            ),
            transfer_trial_ids=(
                (str(run.payload["transfer_trial_id"]),)
                if run.payload.get("transfer_trial_id")
                else ()
            ),
            student_state_after_id=None,
            memory_writes=(),
            exposure_ids=tuple(
                dict.fromkeys(
                    [
                        *(str(value) for value in run.payload.get("exposure_ids", [])),
                        *stored_exposure_ids,
                    ]
                )
            ),
            retirement_ids=tuple(str(value) for value in run.payload.get("retired_item_ids", [])),
            student_outcome=_student_outcome(cold_grade) if cold_grade is not None else None,
            teaching_outcome=teaching_outcome,
            system_outcomes=system_outcomes,
            pedagogical_metrics={"cold_score": cold_grade.score} if cold_grade else {},
            provenance_event_ids=tuple(self._events(run, final_event_id)),
            domain_evidence_refs=(
                tuple(evidence.evidence_id for evidence in cold_grade.evidence)
                if cold_grade
                else ()
            ),
            consolidation_proposal_ids=(),
            status=status,
            created_at=episode_row.created_at,
            completed_at=datetime.now(UTC),
        )
        await self.episodes.commit(session, episode)

    async def _attempt_from_generation(
        self,
        generation: GenerationResult,
        *,
        episode_id: str,
        item: CorpusItemRecord,
        state_id: str,
        request: GenerationRequest,
        label: str,
        influence_refs: tuple[str, ...] = (),
    ) -> AttemptRecord:
        async with self.database.transaction() as session:
            existing = await session.scalar(
                select(AttemptRow).where(AttemptRow.request_id == generation.request_id)
            )
            if existing is not None:
                return AttemptRecord.model_validate(existing.record_json, strict=False)
        raw_response = await artifact_put_bytes(
            self.artifacts,
            generation.raw_response,
            media_type="application/json",
            restricted=True,
            raw_data=True,
        )
        raw_request = await artifact_put_bytes(
            self.artifacts,
            generation.raw_request,
            media_type="application/json",
            restricted=True,
            raw_data=True,
        )
        private_ref = (
            await artifact_put_text(
                self.artifacts,
                generation.private_reasoning,
                media_type="text/plain; charset=utf-8",
                restricted=True,
                raw_data=True,
            )
            if generation.private_reasoning is not None
            else None
        )
        timestamp = datetime.now(UTC)
        try:
            content = self.authority.decode_student_output(
                output_text=generation.output_text,
                item=item,
                research_role=self.student_role,
                created_at=timestamp,
            )
            public = content.public_derivation
            final = content.final_answer
        except Exception:
            public = None
            final = generation.output_text
        output_rights = (
            internally_generated_target_output_rights(reviewed_at=timestamp)
            if self.student_role == ResearchRole.TARGET
            else unreviewed_provider_output_rights(provider=generation.provider)
        )
        return AttemptRecord(
            attempt_id=f"attempt-{label}-{generation.request_id}",
            episode_id=episode_id,
            item_id=item.item_id,
            state_before_id=state_id,
            state_after_id=None,
            rendered_messages=rendered_messages(request),
            rendered_prompt=rendered_prompt(request),
            input_token_ids=None,
            raw_generation_ref=raw_response,
            output_token_ids=generation.token_ids,
            token_logprobs=generation.token_logprobs,
            channel_spans=(),
            private_reasoning_ref=private_ref,
            public_derivation=public,
            final_answer=final,
            tool_calls=(),
            observations=(),
            timing_ms={"provider_total": generation.latency_ms},
            gpu_telemetry=generation.telemetry,
            stop_reason=generation.finish_reason or "unknown",
            request_id=generation.request_id,
            response_id=generation.response_id,
            model_id=generation.model_id,
            checkpoint_id=self.student_checkpoint_id,
            runtime_id=self.student_runtime_id,
            runtime_version=self.student_runtime_version,
            research_role=self.student_role,
            influence_refs=tuple(sorted(set(influence_refs))),
            output_rights=output_rights,
            sampling=request.sampling,
            capabilities=generation.capabilities,
            artifacts=(raw_request,),
            created_at=timestamp,
        )

    async def _retrieve_lessons(
        self,
        *,
        state: StudentStateRecord,
        item: CorpusItemRecord,
        request_id: str,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as session:
            result = await self.memory.retrieve(
                session,
                state_id=state.state_id,
                state_lineage_id=state.student_id,
                branch_id=state.branch_id,
                query_text=item.prompt,
                competency_id=item.competency_id,
                error_class=error_class,
                inherited_lesson_ids=state.lesson_memory_refs,
                retrieval_id=f"retrieval-{sha256_digest(request_id)[7:31]}",
            )
        return {
            "retrieval_id": result.retrieval_id,
            "selected": [
                {
                    "lesson_id": candidate.lesson.lesson_id,
                    "general_rule": candidate.lesson.general_rule,
                    "applicability": candidate.lesson.applicability,
                    "exclusions": list(candidate.lesson.exclusions),
                    "confidence": candidate.lesson.confidence,
                    "evidence_ids": list(candidate.lesson.evidence_ids),
                    "relevance": candidate.relevance,
                    "trust": candidate.trust,
                }
                for candidate in result.selected
            ],
        }

    async def _persist_exposure(
        self,
        run: ClaimedRun,
        *,
        exposure_id: str,
        lease: dict[str, Any],
        state_id: str,
        exposure_type: ExposureType,
        prompt_exposed: bool = False,
        answer_exposed: bool = False,
        critique_exposed: bool = False,
        repair_exposed: bool = False,
    ) -> str:
        item = CorpusItemRecord.model_validate(lease["item"], strict=False)
        exposure = ExposureRecord(
            exposure_id=exposure_id,
            student_id=str(run.payload["student_id"]),
            checkpoint_id=self.student_checkpoint_id,
            state_id=state_id,
            item_id=item.item_id,
            template_family_id=item.template_family_id,
            instance_group_id=item.instance_group_id,
            exposure_type=exposure_type,
            prompt_exposed=prompt_exposed,
            answer_exposed=answer_exposed,
            critique_exposed=critique_exposed,
            repair_exposed=repair_exposed,
            metadata_exposed=False,
            episode_id=str(run.payload["episode_id"]),
            created_at=datetime.now(UTC),
        )
        async with self.database.transaction() as session:
            await self.registry.record_exposure(
                session,
                exposure=exposure,
                lease_token=str(lease["token"]),
                lease_owner=run.run_id,
            )
        return exposure_id

    async def _close_exposures(self, session: Any, run: ClaimedRun) -> tuple[str, ...]:
        treatment_item = self._item_by_id(run, str(run.payload["treatment_item_id"]))
        control_item = self._item_by_id(run, str(run.payload["control_item_id"]))
        for item, state_id in (
            (treatment_item, str(run.payload["treatment_state_id"])),
            (control_item, str(run.payload["control_state_id"])),
        ):
            exposure = ExposureRecord(
                exposure_id=f"exposure-{run.run_id}-{item.item_id}",
                student_id=str(run.payload["student_id"]),
                checkpoint_id=self.student_checkpoint_id,
                state_id=state_id,
                item_id=item.item_id,
                template_family_id=item.template_family_id,
                instance_group_id=item.instance_group_id,
                exposure_type=ExposureType.PROMPT,
                prompt_exposed=True,
                answer_exposed=False,
                critique_exposed=False,
                repair_exposed=False,
                metadata_exposed=False,
                episode_id=str(run.payload["episode_id"]),
                created_at=datetime.now(UTC),
            )
            lease = self._lease(run, item.item_id)
            await self.registry.record_exposure_and_retire(
                session,
                exposure=exposure,
                lease_token=str(lease["token"]),
                lease_owner=run.run_id,
                retirement_reason="domain transfer prompt exposure",
            )
        cold = self._item(run, 0)
        cold_exposure = ExposureRecord(
            exposure_id=f"exposure-{run.run_id}-cold-feedback",
            student_id=str(run.payload["student_id"]),
            checkpoint_id=self.student_checkpoint_id,
            state_id=str(run.payload["treatment_state_id"]),
            item_id=cold.item_id,
            template_family_id=cold.template_family_id,
            instance_group_id=cold.instance_group_id,
            exposure_type=ExposureType.CRITIQUE,
            prompt_exposed=True,
            answer_exposed=False,
            critique_exposed=True,
            repair_exposed=True,
            metadata_exposed=False,
            episode_id=str(run.payload["episode_id"]),
            created_at=datetime.now(UTC),
        )
        cold_lease = self._lease(run, cold.item_id)
        _, retired_ids = await self.registry.record_exposure_and_retire(
            session,
            exposure=cold_exposure,
            lease_token=str(cold_lease["token"]),
            lease_owner=run.run_id,
            retirement_reason="answer-bearing domain feedback closed matched instance group",
        )
        return tuple(retired_ids)

    async def _release_item_leases(self, session: Any, run: ClaimedRun) -> None:
        released_tokens: set[str] = set()
        for lease in run.payload.get("leases", []):
            token = str(lease["token"])
            if token in released_tokens:
                continue
            await self.registry.release_lease(session, token=token, owner=run.run_id)
            released_tokens.add(token)

    async def _load_attempt(self, attempt_id: str) -> AttemptRecord:
        async with self.database.transaction() as session:
            row = await session.get(AttemptRow, attempt_id)
            if row is None:
                raise KeyError(attempt_id)
            return AttemptRecord.model_validate(row.record_json, strict=False)

    async def _load_grade(self, grade_id: str) -> GradeRecord:
        async with self.database.transaction() as session:
            row = await session.get(GradeRow, grade_id)
            if row is None:
                raise KeyError(grade_id)
            return GradeRecord.model_validate(row.record_json, strict=False)

    async def _load_grade_for_attempt(self, attempt_id: str) -> GradeRecord | None:
        async with self.database.transaction() as session:
            row = await session.scalar(select(GradeRow).where(GradeRow.attempt_id == attempt_id))
            if row is None:
                return None
            return GradeRecord.model_validate(row.record_json, strict=False)

    async def _load_intervention(self, intervention_id: str) -> TeacherInterventionRecord:
        async with self.database.transaction() as session:
            row = await session.get(TeacherInterventionRow, intervention_id)
            if row is None:
                raise KeyError(intervention_id)
            return TeacherInterventionRecord.model_validate(row.record_json, strict=False)

    async def _load_state(self, state_id: str) -> StudentStateRecord:
        async with self.database.transaction() as session:
            return await self.states.get(session, state_id=state_id)

    async def _event(
        self,
        session: Any,
        run: ClaimedRun,
        *,
        event_type: str,
        payload: dict[str, Any],
        state_lineage_id: str | None = None,
        episode_id: str | None = None,
    ) -> str:
        prior = tuple(str(value) for value in run.payload.get("provenance_event_ids", []))
        identity = sha256_digest(
            {
                "run_id": run.run_id,
                "run_state": run.state.value,
                "event_type": event_type,
                "domain_id": self.authority.domain_id,
            }
        )
        row = await self.provenance.append(
            session,
            event_type=event_type,
            actor="padawan.domain_developmental_workflow",
            payload=payload,
            parent_event_ids=prior[-1:] if prior else (),
            state_lineage_id=state_lineage_id,
            episode_id=episode_id,
            event_id=f"evt-domain-{identity[7:31]}",
        )
        return row.event_id

    @staticmethod
    def _events(run: ClaimedRun, event_id: str) -> list[str]:
        return list(dict.fromkeys([*run.payload.get("provenance_event_ids", []), event_id]))

    @staticmethod
    def _result(
        state: RunState,
        run: ClaimedRun,
        updates: dict[str, Any],
        details: dict[str, Any] | None = None,
    ) -> WorkResult:
        return WorkResult(to_state=state, payload_updates=updates, details=details or {})

    @staticmethod
    def _exposure_ids(run: ClaimedRun, *new_ids: str) -> list[str]:
        return list(dict.fromkeys([*run.payload.get("exposure_ids", []), *new_ids]))

    @staticmethod
    def _item(run: ClaimedRun, index: int) -> CorpusItemRecord:
        return CorpusItemRecord.model_validate(run.payload["leases"][index]["item"], strict=False)

    @staticmethod
    def _item_by_id(run: ClaimedRun, item_id: str) -> CorpusItemRecord:
        for lease in run.payload["leases"]:
            if lease["item"]["item_id"] == item_id:
                return CorpusItemRecord.model_validate(lease["item"], strict=False)
        raise KeyError(item_id)

    @staticmethod
    def _lease(run: ClaimedRun, item_id: str) -> dict[str, Any]:
        for lease in run.payload["leases"]:
            if lease["item"]["item_id"] == item_id:
                return dict(lease)
        raise KeyError(item_id)


def _teaching_outcome(
    cold: GradeRecord,
    revision: GradeRecord,
    treatment: GradeRecord,
    control: GradeRecord,
) -> TeachingOutcome:
    if treatment.score < control.score:
        return TeachingOutcome.HARMFUL
    if treatment.outcome == GradeOutcome.CORRECT and control.outcome != GradeOutcome.CORRECT:
        return TeachingOutcome.SUCCESSFUL_TRANSFER
    if revision.score > cold.score:
        return TeachingOutcome.SUCCESSFUL_REPAIR
    if revision.score == cold.score and treatment.score == control.score:
        return TeachingOutcome.NO_EFFECT
    return TeachingOutcome.SUCCESSFUL_DIAGNOSIS


def _student_outcome(grade: GradeRecord) -> StudentOutcome:
    if grade.outcome == GradeOutcome.CORRECT:
        return StudentOutcome.ROBUST_SUCCESS
    if grade.outcome == GradeOutcome.INVALID_PROCESS:
        return StudentOutcome.CORRECT_INVALID_PROCESS
    if grade.outcome == GradeOutcome.MALFORMED:
        return StudentOutcome.MALFORMED_OUTPUT
    if grade.outcome == GradeOutcome.PARTIAL:
        return StudentOutcome.PARTIAL_PROGRESS
    if grade.outcome == GradeOutcome.INFRASTRUCTURE_FAILURE:
        return StudentOutcome.EXECUTION_ERROR
    return StudentOutcome.REASONING_ERROR
