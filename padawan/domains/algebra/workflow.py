from __future__ import annotations

import json
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
    artifact_read_bytes,
    redact_secrets,
)
from padawan.corpus.registry import CorpusRegistry
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.grading.algebra import AlgebraGrader
from padawan.grading.derivation import PublicDerivation
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import (
    AttemptRecord,
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
    SamplingConfiguration,
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
from padawan.teaching.service import (
    TeacherContext,
    TeacherExhaustedError,
    TeacherService,
)
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


class AlgebraWorkflowHandler:
    """One durable action per invocation for a real state-forked algebra episode."""

    def __init__(
        self,
        *,
        database: Database,
        artifacts: ArtifactBackend,
        registry: CorpusRegistry,
        states: StateStore,
        episodes: EpisodeStore,
        experiments: ExperimentEngine,
        grader: AlgebraGrader,
        provenance: ProvenanceLedger,
        student_calls: IdempotentGenerationExecutor,
        teacher_calls: IdempotentGenerationExecutor,
        teacher_provider: str,
        memory: LessonMemory,
        memory_backend: ConsolidationBackend,
        student_runtime_id: str,
        student_runtime_version: str,
        student_checkpoint_id: str,
        student_role: ResearchRole = ResearchRole.TARGET,
        lease_for: timedelta = timedelta(hours=2),
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.catalog = ArtifactCatalog(artifacts)
        self.registry = registry
        self.states = states
        self.episodes = episodes
        self.experiments = experiments
        self.grader = grader
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

    async def handle(self, run: ClaimedRun) -> WorkResult:
        run_role = ResearchRole(str(run.payload.get("research_role", ResearchRole.TARGET.value)))
        if run_role != run.research_role or run_role != self.student_role:
            raise RuntimeError("persisted, payload, and runtime research roles must match")
        handlers = {
            RunState.CREATED: self._lease_items,
            RunState.ITEMS_LEASED: self._snapshot_state,
            RunState.BASE_STATE_SNAPSHOTTED: self._create_branches,
            RunState.BRANCHES_CREATED: self._begin_cold,
            RunState.COLD_ATTEMPT_RUNNING: self._run_cold,
            RunState.COLD_ATTEMPT_STORED: self._grade_cold,
            RunState.COLD_GRADED: self._request_teacher_marker,
            RunState.TEACHER_REQUESTED: self._run_teacher,
            RunState.TEACHER_RESPONSE_STORED: self._validate_marker,
            RunState.COMMENT_VALIDATED: self._begin_revision,
            RunState.REVISION_RUNNING: self._run_revision,
            RunState.REVISION_STORED: self._grade_revision,
            RunState.REVISION_GRADED: self._begin_transfer,
            RunState.TRANSFER_RUNNING: self._run_transfer,
            RunState.TRANSFER_STORED: self._grade_transfer,
            RunState.TRANSFER_GRADED: self._decide_memory,
            RunState.MEMORY_DECIDED: self._record_exposures_and_retire,
            RunState.EXPOSURES_RECORDED: self._retirement_marker,
            RunState.ITEMS_RETIRED: self._commit_episode,
            RunState.EPISODE_COMMITTED: self._complete,
        }
        handler = handlers.get(run.state)
        if handler is None:
            raise RuntimeError(f"no algebra workflow handler for {run.state.value}")
        return await handler(run)

    async def _lease_items(self, run: ClaimedRun) -> WorkResult:
        from padawan.models.contracts import CorpusPool

        pool = CorpusPool(str(run.payload.get("pool", CorpusPool.CURRICULUM.value)))
        async with self.database.transaction() as session:
            leases = await self.registry.lease_matched_group(
                session,
                owner=run.run_id,
                pool=pool,
                count=3,
                lease_for=self.lease_for,
                student_id=str(run.payload["student_id"]),
                competency_id=(
                    str(run.payload["competency_id"]) if run.payload.get("competency_id") else None
                ),
            )
            if len(leases) != 3:
                raise InventoryExhaustedError("no active three-sibling algebra group is available")
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
                event_type="CorpusItemsLeased",
                payload={
                    "episode_id": episode_id,
                    "item_ids": [lease.item.item_id for lease in leases],
                    "instance_group_id": leases[0].item.instance_group_id,
                    "pool": pool.value,
                },
                episode_id=episode_id,
            )
        return self._result(
            RunState.ITEMS_LEASED,
            run,
            {
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

    async def _snapshot_state(self, run: ClaimedRun) -> WorkResult:
        async with self.database.transaction() as session:
            state = await self.states.get(session, state_id=str(run.payload["state_id"]))
            event_id = await self._event(
                session,
                run,
                event_type="BaseStateSnapshotted",
                payload={"state_id": state.state_id, "state_hash": state.state_hash},
                state_lineage_id=state.state_id,
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.BASE_STATE_SNAPSHOTTED,
            run,
            {
                "base_state_hash": state.state_hash,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _create_branches(self, run: ClaimedRun) -> WorkResult:
        leases = run.payload["leases"]
        transfer_a = leases[1]["item"]
        transfer_b = leases[2]["item"]
        experiment_id = f"experiment-{run.run_id}"
        async with self.database.transaction() as session:
            _, assignments = await self.experiments.create(
                session,
                parent_state_id=str(run.payload["state_id"]),
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
                research_execution_digest=run.research_execution_digest,
            )
            block = await session.get(ExperimentBlockRow, assignments[0].block_id)
            if block is None:
                raise RuntimeError("experiment block was not persisted")
            event_id = await self._event(
                session,
                run,
                event_type="StateForkCreated",
                payload={
                    "experiment_id": experiment_id,
                    "block_id": block.block_id,
                    "assignment": block.assignment,
                },
                state_lineage_id=str(run.payload["state_id"]),
                episode_id=str(run.payload["episode_id"]),
            )
            assignment = dict(block.assignment)
        return self._result(
            RunState.BRANCHES_CREATED,
            run,
            {
                "experiment_id": experiment_id,
                "experiment_block_id": assignments[0].block_id,
                **assignment,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _begin_cold(self, run: ClaimedRun) -> WorkResult:
        return self._result(
            RunState.COLD_ATTEMPT_RUNNING,
            run,
            {},
            {"request_id": f"{run.run_id}:student:cold"},
        )

    async def _run_cold(self, run: ClaimedRun) -> WorkResult:
        item = run.payload["leases"][0]["item"]
        state = await self._load_state(str(run.payload["state_id"]))
        retrieval = await self._retrieve_lessons(
            state=state, item=item, request_id=f"{run.run_id}:student:cold"
        )
        request = self._student_request(
            request_id=f"{run.run_id}:student:cold",
            item=item,
            state=state,
            intervention=None,
            retrieval=retrieval,
        )
        exposure_ids = await self._persist_student_exposures(
            run,
            (
                {
                    "exposure_id": f"exposure-{run.run_id}-cold-prompt",
                    "lease": run.payload["leases"][0],
                    "state_id": state.state_id,
                    "exposure_type": ExposureType.PROMPT,
                    "prompt_exposed": True,
                },
            ),
        )
        generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="cold_attempt",
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
                event_type="StudentAttemptStored",
                payload={"attempt_id": attempt.attempt_id, "kind": "cold"},
                state_lineage_id=state.state_id,
                episode_id=attempt.episode_id,
            )
        return self._result(
            RunState.COLD_ATTEMPT_STORED,
            run,
            {
                "cold_attempt_id": attempt.attempt_id,
                "exposure_ids": self._exposure_ids(run, *exposure_ids),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _grade_cold(self, run: ClaimedRun) -> WorkResult:
        item = run.payload["leases"][0]["item"]
        attempt = await self._load_attempt(str(run.payload["cold_attempt_id"]))
        grade = self.grader.grade(
            attempt_id=attempt.attempt_id,
            response=await artifact_read_bytes(
                self.artifacts, attempt.raw_generation_ref, allow_restricted=True
            )
            if attempt.public_derivation is None
            else attempt.public_derivation,
            expected_answer=dict(item["expected_answer"]),
        )
        # Raw provider bytes are not necessarily just model text; grade the extracted
        # text on malformed output.
        if attempt.public_derivation is None:
            raw_text = str(attempt.final_answer or "")
            grade = self.grader.grade(
                attempt_id=attempt.attempt_id,
                response=raw_text,
                expected_answer=dict(item["expected_answer"]),
            )
        async with self.database.transaction() as session:
            await self.episodes.store_grade(session, grade)
            event_id = await self._event(
                session,
                run,
                event_type="DeterministicGradeStored",
                payload={
                    "grade_id": grade.grade_id,
                    "attempt_id": grade.attempt_id,
                    "outcome": grade.outcome.value,
                    "score": grade.score,
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.COLD_GRADED,
            run,
            {
                "cold_grade_id": grade.grade_id,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _request_teacher_marker(self, run: ClaimedRun) -> WorkResult:
        return self._result(
            RunState.TEACHER_REQUESTED,
            run,
            {},
            {"request_prefix": f"{run.run_id}:teacher"},
        )

    async def _run_teacher(self, run: ClaimedRun) -> WorkResult:
        item = run.payload["leases"][0]["item"]
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
                purpose="teacher_intervention",
                provider=self.teacher_provider,
            ),
            artifacts=self.artifacts,
        )
        context = TeacherContext(
            episode_id=str(run.payload["episode_id"]),
            task={
                "item_id": item["item_id"],
                "prompt": item["prompt"],
                "competency_id": item["competency_id"],
            },
            attempt=attempt,
            grade=grade,
            student_state=state,
            mode=mode,
            guidance_budget_tokens=int(run.payload.get("guidance_budget_tokens", 800)),
            expected_answer=dict(item["expected_answer"]),
            prior_lessons=tuple(teacher_memory["selected"]),
            private_reasoning=None,
            private_reasoning_policy_allows=False,
            forbidden_transfer_answers=tuple(
                json.dumps(lease["item"]["expected_answer"], sort_keys=True)
                for lease in run.payload["leases"][1:]
            ),
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
                    event_type="TeacherResponsesRejected",
                    payload={
                        "failures": [
                            {
                                "request_id": failure.request_id,
                                "provider": failure.provider,
                                "artifact_id": failure.raw_response_ref.artifact_id,
                                "validation_errors": list(failure.validation_errors),
                            }
                            for failure in exc.failures
                        ]
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
                {
                    "teacher_failures": [
                        {
                            "request_id": failure.request_id,
                            "artifact_id": failure.raw_response_ref.artifact_id,
                            "errors": list(failure.validation_errors),
                        }
                        for failure in exc.failures
                    ],
                    "provenance_event_ids": self._events(run, event_id),
                },
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
                event_type="TeacherInterventionValidated",
                payload={
                    "intervention_id": intervention.intervention_id,
                    "failed_teacher_attempts": len(teacher_result.failed_attempts),
                    "mode": intervention.mode.value,
                },
                episode_id=intervention.episode_id,
            )
        return self._result(
            RunState.TEACHER_RESPONSE_STORED,
            run,
            {
                "intervention_id": intervention.intervention_id,
                "teacher_failure_count": len(teacher_result.failed_attempts),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _validate_marker(self, run: ClaimedRun) -> WorkResult:
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        if intervention.validation_status.value != "accepted":
            return self._result(
                RunState.REVIEW_REQUIRED,
                run,
                {},
                {"validation_errors": list(intervention.validation_errors)},
            )
        return self._result(RunState.COMMENT_VALIDATED, run, {})

    async def _begin_revision(self, run: ClaimedRun) -> WorkResult:
        return self._result(
            RunState.REVISION_RUNNING,
            run,
            {},
            {"request_id": f"{run.run_id}:student:revision"},
        )

    async def _run_revision(self, run: ClaimedRun) -> WorkResult:
        item = run.payload["leases"][0]["item"]
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        state = await self._load_state(str(run.payload["treatment_state_id"]))
        retrieval = await self._retrieve_lessons(
            state=state,
            item=item,
            request_id=f"{run.run_id}:student:revision",
            error_class=intervention.error_class,
        )
        request = self._student_request(
            request_id=f"{run.run_id}:student:revision",
            item=item,
            state=state,
            intervention={"lesson": intervention.lesson, "repair": intervention.repair},
            retrieval=retrieval,
        )
        exposure_ids = await self._persist_student_exposures(
            run,
            (
                {
                    "exposure_id": f"exposure-{run.run_id}-cold",
                    "lease": run.payload["leases"][0],
                    "state_id": state.state_id,
                    "exposure_type": ExposureType.CRITIQUE,
                    "prompt_exposed": True,
                    "critique_exposed": True,
                    "repair_exposed": True,
                },
            ),
        )
        generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="revision_attempt",
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
        async with self.database.transaction() as session:
            await self.episodes.store_attempt(session, attempt)
            event_id = await self._event(
                session,
                run,
                event_type="StudentRevisionStored",
                payload={"attempt_id": attempt.attempt_id},
                state_lineage_id=state.state_id,
                episode_id=attempt.episode_id,
            )
        return self._result(
            RunState.REVISION_STORED,
            run,
            {
                "revision_attempt_id": attempt.attempt_id,
                "exposure_ids": self._exposure_ids(run, *exposure_ids),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _grade_revision(self, run: ClaimedRun) -> WorkResult:
        item = run.payload["leases"][0]["item"]
        attempt = await self._load_attempt(str(run.payload["revision_attempt_id"]))
        grade = self.grader.grade(
            attempt_id=attempt.attempt_id,
            response=attempt.public_derivation or str(attempt.final_answer or ""),
            expected_answer=dict(item["expected_answer"]),
        )
        revision = RevisionRecord(
            revision_id=f"revision-{run.run_id}",
            original_attempt_id=str(run.payload["cold_attempt_id"]),
            intervention_id=str(run.payload["intervention_id"]),
            revised_attempt_id=attempt.attempt_id,
            revised_grade_id=grade.grade_id,
            created_at=datetime.now(UTC),
        )
        async with self.database.transaction() as session:
            await self.episodes.store_grade(session, grade)
            await self.episodes.store_revision(session, revision)
            event_id = await self._event(
                session,
                run,
                event_type="RevisionGraded",
                payload={
                    "revision_id": revision.revision_id,
                    "grade_id": grade.grade_id,
                    "outcome": grade.outcome.value,
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.REVISION_GRADED,
            run,
            {
                "revision_id": revision.revision_id,
                "revision_grade_id": grade.grade_id,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _begin_transfer(self, run: ClaimedRun) -> WorkResult:
        return self._result(
            RunState.TRANSFER_RUNNING,
            run,
            {},
            {
                "request_ids": [
                    f"{run.run_id}:student:transfer:treatment",
                    f"{run.run_id}:student:transfer:control",
                ]
            },
        )

    async def _run_transfer(self, run: ClaimedRun) -> WorkResult:
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        treatment_item = self._item_payload(run, str(run.payload["treatment_item_id"]))
        control_item = self._item_payload(run, str(run.payload["control_item_id"]))
        treatment_state = await self._load_state(str(run.payload["treatment_state_id"]))
        control_state = await self._load_state(str(run.payload["control_state_id"]))
        treatment_retrieval = await self._retrieve_lessons(
            state=treatment_state,
            item=treatment_item,
            request_id=f"{run.run_id}:student:transfer:treatment",
            error_class=intervention.error_class,
        )
        control_retrieval = await self._retrieve_lessons(
            state=control_state,
            item=control_item,
            request_id=f"{run.run_id}:student:transfer:control",
            error_class=intervention.error_class,
        )
        treatment_request = self._student_request(
            request_id=f"{run.run_id}:student:transfer:treatment",
            item=treatment_item,
            state=treatment_state,
            intervention={"lesson": intervention.lesson, "repair": intervention.repair},
            retrieval=treatment_retrieval,
        )
        control_condition = str(run.payload.get("control_condition", "no_intervention"))
        control_intervention = (
            {"generic": "Check your work carefully before answering."}
            if control_condition == "generic_check_work"
            else None
        )
        control_request = self._student_request(
            request_id=f"{run.run_id}:student:transfer:control",
            item=control_item,
            state=control_state,
            intervention=control_intervention,
            retrieval=control_retrieval,
        )
        exposure_ids = await self._persist_student_exposures(
            run,
            (
                {
                    "exposure_id": f"exposure-{run.run_id}-{treatment_item['item_id']}",
                    "lease": self._lease_payload(run, str(treatment_item["item_id"])),
                    "state_id": treatment_state.state_id,
                    "exposure_type": ExposureType.PROMPT,
                    "prompt_exposed": True,
                },
                {
                    "exposure_id": f"exposure-{run.run_id}-{control_item['item_id']}",
                    "lease": self._lease_payload(run, str(control_item["item_id"])),
                    "state_id": control_state.state_id,
                    "exposure_type": ExposureType.PROMPT,
                    "prompt_exposed": True,
                },
            ),
        )
        treatment_generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="transfer_treatment",
            provider="student_runtime",
            request=treatment_request,
        )
        control_generation = await self.student_calls.execute(
            run_id=run.run_id,
            purpose="transfer_control",
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
        async with self.database.transaction() as session:
            await self.episodes.store_attempt(session, treatment_attempt)
            await self.episodes.store_attempt(session, control_attempt)
            event_id = await self._event(
                session,
                run,
                event_type="TransferAttemptsStored",
                payload={
                    "treatment_attempt_id": treatment_attempt.attempt_id,
                    "control_attempt_id": control_attempt.attempt_id,
                    "treatment_item_id": treatment_item["item_id"],
                    "control_item_id": control_item["item_id"],
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.TRANSFER_STORED,
            run,
            {
                "treatment_attempt_id": treatment_attempt.attempt_id,
                "control_attempt_id": control_attempt.attempt_id,
                "exposure_ids": self._exposure_ids(run, *exposure_ids),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _grade_transfer(self, run: ClaimedRun) -> WorkResult:
        treatment_item = self._item_payload(run, str(run.payload["treatment_item_id"]))
        control_item = self._item_payload(run, str(run.payload["control_item_id"]))
        treatment_attempt = await self._load_attempt(str(run.payload["treatment_attempt_id"]))
        control_attempt = await self._load_attempt(str(run.payload["control_attempt_id"]))
        treatment_grade = self.grader.grade(
            attempt_id=treatment_attempt.attempt_id,
            response=treatment_attempt.public_derivation
            or str(treatment_attempt.final_answer or ""),
            expected_answer=dict(treatment_item["expected_answer"]),
        )
        control_grade = self.grader.grade(
            attempt_id=control_attempt.attempt_id,
            response=control_attempt.public_derivation or str(control_attempt.final_answer or ""),
            expected_answer=dict(control_item["expected_answer"]),
        )
        transfer = TransferTrialRecord(
            transfer_trial_id=f"transfer-{run.run_id}",
            source_intervention_id=str(run.payload["intervention_id"]),
            inherited_state_id=str(run.payload["state_id"]),
            item_id=f"{treatment_item['item_id']}|{control_item['item_id']}",
            treatment_condition=str(
                run.payload.get("treatment_condition", "frontier_teacher_critique")
            ),
            control_condition=str(run.payload.get("control_condition", "no_intervention")),
            treatment_attempt_id=treatment_attempt.attempt_id,
            control_attempt_id=control_attempt.attempt_id,
            treatment_grade_id=treatment_grade.grade_id,
            control_grade_id=control_grade.grade_id,
            delayed_retest_at=None,
            contamination_checks={
                "different_item_ids": treatment_item["item_id"] != control_item["item_id"],
                "same_instance_group": treatment_item["instance_group_id"]
                == control_item["instance_group_id"],
                "fresh_from_cold_item": treatment_item["item_id"]
                != run.payload["leases"][0]["item"]["item_id"]
                and control_item["item_id"] != run.payload["leases"][0]["item"]["item_id"],
                "separate_branches": run.payload["treatment_branch_id"]
                != run.payload["control_branch_id"],
            },
            created_at=datetime.now(UTC),
        )
        async with self.database.transaction() as session:
            await self.episodes.store_grade(session, treatment_grade)
            await self.episodes.store_grade(session, control_grade)
            await self.episodes.store_transfer(
                session,
                episode_id=str(run.payload["episode_id"]),
                transfer=transfer,
                experiment_id=str(run.payload["experiment_id"]),
            )
            await self.experiments.record_block(
                session,
                block_id=str(run.payload["experiment_block_id"]),
                treatment_success=treatment_grade.outcome == GradeOutcome.CORRECT,
                control_success=control_grade.outcome == GradeOutcome.CORRECT,
                treatment_score=treatment_grade.score,
                control_score=control_grade.score,
                contamination_checks=transfer.contamination_checks,
            )
            event_id = await self._event(
                session,
                run,
                event_type="TransferTrialGraded",
                payload={
                    "transfer_trial_id": transfer.transfer_trial_id,
                    "treatment_score": treatment_grade.score,
                    "control_score": control_grade.score,
                    "contamination_checks": transfer.contamination_checks,
                },
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.TRANSFER_GRADED,
            run,
            {
                "transfer_trial_id": transfer.transfer_trial_id,
                "treatment_grade_id": treatment_grade.grade_id,
                "control_grade_id": control_grade.grade_id,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _decide_memory(self, run: ClaimedRun) -> WorkResult:
        intervention = await self._load_intervention(str(run.payload["intervention_id"]))
        treatment_grade = await self._load_grade(str(run.payload["treatment_grade_id"]))
        control_grade = await self._load_grade(str(run.payload["control_grade_id"]))
        treatment_success = treatment_grade.outcome == GradeOutcome.CORRECT
        control_success = control_grade.outcome == GradeOutcome.CORRECT
        item = run.payload["leases"][0]["item"]
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
                    competency_id=str(item["competency_id"]),
                    error_class=intervention.error_class or "teacher_diagnosed",
                    general_rule=intervention.lesson,
                    applicability=intervention.expected_transfer_scope,
                    exclusions=(),
                    evidence_ids=tuple(
                        evidence.evidence_id for evidence in (*treatment_grade.evidence,)
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
                event_type="MemoryConsolidationDecided",
                payload={
                    "proposal_id": decision.proposal_id,
                    "accepted": decision.accepted,
                    "reason": decision.reason,
                    "lesson_id": decision.lesson_id,
                    "before_snapshot_id": decision.before_snapshot_id,
                    "after_snapshot_id": decision.after_snapshot_id,
                },
                state_lineage_id=str(run.payload["state_id"]),
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.MEMORY_DECIDED,
            run,
            {
                "memory_decision": decision.__dict__,
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _record_exposures_and_retire(self, run: ClaimedRun) -> WorkResult:
        leases = run.payload["leases"]
        lease_by_item = {lease["item"]["item_id"]: lease for lease in leases}
        treatment_item_id = str(run.payload["treatment_item_id"])
        control_item_id = str(run.payload["control_item_id"])
        timestamp = datetime.now(UTC)
        async with self.database.transaction() as session:
            exposure_ids = list(dict.fromkeys(run.payload.get("exposure_ids", [])))
            for item_id, state_id in (
                (treatment_item_id, str(run.payload["treatment_state_id"])),
                (control_item_id, str(run.payload["control_state_id"])),
            ):
                lease = lease_by_item[item_id]
                item = lease["item"]
                exposure = ExposureRecord(
                    exposure_id=f"exposure-{run.run_id}-{item_id}",
                    student_id=str(run.payload["student_id"]),
                    checkpoint_id=self.student_checkpoint_id,
                    state_id=state_id,
                    item_id=item_id,
                    template_family_id=str(item["template_family_id"]),
                    instance_group_id=str(item["instance_group_id"]),
                    exposure_type=ExposureType.PROMPT,
                    prompt_exposed=True,
                    answer_exposed=False,
                    critique_exposed=False,
                    repair_exposed=False,
                    metadata_exposed=False,
                    episode_id=str(run.payload["episode_id"]),
                    created_at=timestamp,
                )
                await self.registry.record_exposure_and_retire(
                    session,
                    exposure=exposure,
                    lease_token=str(lease["token"]),
                    lease_owner=run.run_id,
                    retirement_reason="transfer prompt exposure",
                )
                if exposure.exposure_id not in exposure_ids:
                    exposure_ids.append(exposure.exposure_id)
            cold_lease = leases[0]
            cold_item = cold_lease["item"]
            cold_exposure = ExposureRecord(
                exposure_id=f"exposure-{run.run_id}-cold",
                student_id=str(run.payload["student_id"]),
                checkpoint_id=self.student_checkpoint_id,
                state_id=str(run.payload["treatment_state_id"]),
                item_id=str(cold_item["item_id"]),
                template_family_id=str(cold_item["template_family_id"]),
                instance_group_id=str(cold_item["instance_group_id"]),
                exposure_type=ExposureType.CRITIQUE,
                prompt_exposed=True,
                answer_exposed=False,
                critique_exposed=True,
                repair_exposed=True,
                metadata_exposed=False,
                episode_id=str(run.payload["episode_id"]),
                created_at=timestamp,
            )
            _, retired_ids = await self.registry.record_exposure_and_retire(
                session,
                exposure=cold_exposure,
                lease_token=str(cold_lease["token"]),
                lease_owner=run.run_id,
                retirement_reason="answer-bearing teacher feedback closed matched instance group",
            )
            if cold_exposure.exposure_id not in exposure_ids:
                exposure_ids.append(cold_exposure.exposure_id)
            event_id = await self._event(
                session,
                run,
                event_type="ExposuresRecordedAndItemsRetired",
                payload={"exposure_ids": exposure_ids, "retired_item_ids": list(retired_ids)},
                episode_id=str(run.payload["episode_id"]),
            )
        return self._result(
            RunState.EXPOSURES_RECORDED,
            run,
            {
                "exposure_ids": exposure_ids,
                "retired_item_ids": list(retired_ids),
                "provenance_event_ids": self._events(run, event_id),
            },
        )

    async def _retirement_marker(self, run: ClaimedRun) -> WorkResult:
        if not run.payload.get("retired_item_ids"):
            raise RuntimeError("retirement state lacks retired inventory evidence")
        return self._result(
            RunState.ITEMS_RETIRED,
            run,
            {},
            {"retired_item_count": len(run.payload["retired_item_ids"])},
        )

    async def _commit_episode(self, run: ClaimedRun) -> WorkResult:
        cold_grade = await self._load_grade(str(run.payload["cold_grade_id"]))
        revision_grade = await self._load_grade(str(run.payload["revision_grade_id"]))
        treatment_grade = await self._load_grade(str(run.payload["treatment_grade_id"]))
        control_grade = await self._load_grade(str(run.payload["control_grade_id"]))
        # The intervention branch must demonstrate a strict transfer advantage;
        # ties retain the lower-intervention control branch.
        treatment_wins = treatment_grade.score > control_grade.score
        chosen_state_id = str(
            run.payload["treatment_state_id"] if treatment_wins else run.payload["control_state_id"]
        )
        memory_decision = dict(run.payload["memory_decision"])
        async with self.database.transaction() as session:
            chosen = await self.states.get(session, state_id=chosen_state_id)
            working_state = dict(chosen.compacted_working_state)
            working_state["last_episode"] = {
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
                creation_reason=f"completed developmental episode {run.payload['episode_id']}",
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
                event_type="DevelopmentalEpisodeCommitted",
                payload={
                    "episode_id": run.payload["episode_id"],
                    "state_after_id": state_after.state_id,
                    "canonical_branch": chosen.branch_id,
                    "teaching_outcome": teaching_outcome.value,
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
            episode = DevelopmentalEpisode(
                episode_id=str(run.payload["episode_id"]),
                student_state_before_id=str(run.payload["state_id"]),
                task_item_id=str(run.payload["leases"][0]["item"]["item_id"]),
                research_role=self.student_role,
                research_execution_digest=run.research_execution_digest,
                initial_attempt_id=str(run.payload["cold_attempt_id"]),
                grade_id=str(run.payload["cold_grade_id"]),
                diagnosis_ids=(),
                intervention_id=str(run.payload["intervention_id"]),
                revision_id=str(run.payload["revision_id"]),
                transfer_trial_ids=(str(run.payload["transfer_trial_id"]),),
                student_state_after_id=state_after.state_id,
                memory_writes=(memory_write,),
                exposure_ids=tuple(str(value) for value in run.payload["exposure_ids"]),
                retirement_ids=tuple(str(value) for value in run.payload["retired_item_ids"]),
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
                consolidation_proposal_ids=(str(memory_decision["proposal_id"]),),
                status="complete",
                created_at=episode_row.created_at,
                completed_at=datetime.now(UTC),
            )
            await self.episodes.commit(session, episode)
        return self._result(
            RunState.EPISODE_COMMITTED,
            run,
            {
                "state_after_id": state_after.state_id,
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
                event_type="DevelopmentalEpisodeFailed",
                payload={
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
            raise RuntimeError("failed episode row disappeared")
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
            task_item_id=str(run.payload["leases"][0]["item"]["item_id"]),
            research_role=self.student_role,
            research_execution_digest=run.research_execution_digest,
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
            pedagogical_metrics=(
                {"cold_score": cold_grade.score} if cold_grade is not None else {}
            ),
            provenance_event_ids=tuple(self._events(run, final_event_id)),
            consolidation_proposal_ids=(),
            status=status,
            created_at=episode_row.created_at,
            completed_at=datetime.now(UTC),
        )
        await self.episodes.commit(session, episode)

    def _student_request(
        self,
        *,
        request_id: str,
        item: dict[str, Any],
        state: Any,
        intervention: dict[str, Any] | None,
        retrieval: dict[str, Any],
    ) -> GenerationRequest:
        context = {
            "task": item["prompt"],
            "persistent_student_state": {
                "compacted_working_state": state.compacted_working_state,
                "lesson_memory_refs": list(state.lesson_memory_refs),
                "unresolved_hypotheses": [
                    hypothesis.model_dump(mode="json") for hypothesis in state.unresolved_hypotheses
                ],
            },
            "retrieved_lesson_memory": retrieval,
            "current_intervention": intervention,
        }
        return GenerationRequest(
            request_id=request_id,
            instructions=(
                "Act as the persistent student agent. Solve the unseen item using "
                "legitimate accumulated state. Do not invent tool results. Return "
                "only the required public-derivation JSON."
            ),
            input=json.dumps(context, sort_keys=True),
            sampling=SamplingConfiguration(
                temperature=0.0,
                top_p=None,
                max_output_tokens=1600,
                seed=int(item["generation_seed"]),
                top_logprobs=5,
            ),
            schema_name="padawan_public_derivation",
            json_schema=PublicDerivation.model_json_schema(),
            metadata={"item_id": str(item["item_id"]), "state_id": state.state_id},
            store=False,
        )

    async def _retrieve_lessons(
        self,
        *,
        state: StudentStateRecord,
        item: dict[str, Any],
        request_id: str,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as session:
            result = await self.memory.retrieve(
                session,
                state_id=state.state_id,
                state_lineage_id=state.student_id,
                branch_id=state.branch_id,
                query_text=str(item["prompt"]),
                competency_id=str(item["competency_id"]),
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

    async def _persist_student_exposures(
        self,
        run: ClaimedRun,
        entries: tuple[dict[str, Any], ...],
    ) -> tuple[str, ...]:
        timestamp = datetime.now(UTC)
        identifiers: list[str] = []
        async with self.database.transaction() as session:
            for entry in entries:
                lease = dict(entry["lease"])
                item = dict(lease["item"])
                exposure = ExposureRecord(
                    exposure_id=str(entry["exposure_id"]),
                    student_id=str(run.payload["student_id"]),
                    checkpoint_id=self.student_checkpoint_id,
                    state_id=str(entry["state_id"]),
                    item_id=str(item["item_id"]),
                    template_family_id=str(item["template_family_id"]),
                    instance_group_id=str(item["instance_group_id"]),
                    exposure_type=ExposureType(entry["exposure_type"]),
                    prompt_exposed=bool(entry.get("prompt_exposed", False)),
                    answer_exposed=bool(entry.get("answer_exposed", False)),
                    critique_exposed=bool(entry.get("critique_exposed", False)),
                    repair_exposed=bool(entry.get("repair_exposed", False)),
                    metadata_exposed=bool(entry.get("metadata_exposed", False)),
                    episode_id=str(run.payload["episode_id"]),
                    created_at=timestamp,
                )
                await self.registry.record_exposure(
                    session,
                    exposure=exposure,
                    lease_token=str(lease["token"]),
                    lease_owner=run.run_id,
                )
                identifiers.append(exposure.exposure_id)
        return tuple(identifiers)

    async def _release_item_leases(self, session: Any, run: ClaimedRun) -> None:
        released_tokens: set[str] = set()
        for lease in run.payload.get("leases", []):
            token = str(lease["token"])
            if token in released_tokens:
                continue
            await self.registry.release_lease(session, token=token, owner=run.run_id)
            released_tokens.add(token)

    async def _attempt_from_generation(
        self,
        generation: GenerationResult,
        *,
        episode_id: str,
        item: dict[str, Any],
        state_id: str,
        request: GenerationRequest,
        label: str,
        influence_refs: tuple[str, ...] = (),
    ) -> AttemptRecord:
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
        public: dict[str, Any] | None
        final: dict[str, Any] | str | None
        try:
            parsed = PublicDerivation.model_validate_json(generation.output_text)
            public = parsed.model_dump(mode="json")
            final = parsed.final_answer.model_dump(mode="json")
        except Exception:
            public = None
            final = generation.output_text
        timestamp = datetime.now(UTC)
        output_rights = (
            internally_generated_target_output_rights(reviewed_at=timestamp)
            if self.student_role == ResearchRole.TARGET
            else unreviewed_provider_output_rights(provider=generation.provider)
        )
        return AttemptRecord(
            attempt_id=f"attempt-{label}-{generation.request_id}",
            episode_id=episode_id,
            item_id=str(item["item_id"]),
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
        row = await self.provenance.append(
            session,
            event_type=event_type,
            actor="padawan.algebra_workflow",
            payload={
                **payload,
                **(
                    {"research_execution_digest": run.research_execution_digest}
                    if run.research_execution_digest is not None
                    else {}
                ),
            },
            parent_event_ids=prior[-1:] if prior else (),
            state_lineage_id=state_lineage_id,
            episode_id=episode_id,
        )
        return row.event_id

    @staticmethod
    def _events(run: ClaimedRun, event_id: str) -> list[str]:
        return [*run.payload.get("provenance_event_ids", []), event_id]

    @staticmethod
    def _result(
        state: RunState,
        run: ClaimedRun,
        updates: dict[str, Any],
        details: dict[str, Any] | None = None,
    ) -> WorkResult:
        return WorkResult(to_state=state, payload_updates=updates, details=details or {})

    @staticmethod
    def _item_payload(run: ClaimedRun, item_id: str) -> dict[str, Any]:
        for lease in run.payload["leases"]:
            if lease["item"]["item_id"] == item_id:
                return dict(lease["item"])
        raise KeyError(item_id)

    @staticmethod
    def _lease_payload(run: ClaimedRun, item_id: str) -> dict[str, Any]:
        for lease in run.payload["leases"]:
            if lease["item"]["item_id"] == item_id:
                return dict(lease)
        raise KeyError(item_id)

    @staticmethod
    def _exposure_ids(run: ClaimedRun, *new_ids: str) -> list[str]:
        return list(dict.fromkeys([*run.payload.get("exposure_ids", []), *new_ids]))


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
    return StudentOutcome.REASONING_ERROR
