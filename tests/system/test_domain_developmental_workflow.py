from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.developmental.workflow import DomainDevelopmentalWorkflowHandler
from padawan.domains.lean_math import LeanMathCorpusGenerator, LeanMathFamily
from padawan.domains.lean_math.developmental import LeanDevelopmentalAuthority
from padawan.domains.legal.appellate import (
    AppellateComplianceDraft,
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    AppellateScenarioManifest,
    AppellateSemanticAssessmentDraft,
    AppellateSubmissionDraft,
)
from padawan.domains.legal.appellate.adjudication import AppellateAdjudicationService
from padawan.domains.legal.appellate.developmental import AppellateDevelopmentalAuthority
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import CorpusPool, ResearchRole, RunState
from padawan.models.tables import EpisodeRow, ExternalCallRow, RunRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.updates.backends import MemoryConsolidationBackend
from tests.appellate_helpers import build_semantic_assessment, build_submission
from tests.helpers import CallbackGenerationClient, teacher_response


class _KernelDouble:
    verifier_id = "lean4.kernel"
    verifier_version = "test-kernel"

    def verify(self, task) -> VerifierResult:
        accepted = task.proof == "by\n  omega"
        return VerifierResult(
            result_id=f"lean-test-{task.task_id}-{int(accepted)}",
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            scope=task.task_id,
            disposition=(
                VerifierDisposition.VERIFIED if accepted else VerifierDisposition.REJECTED
            ),
            deterministic=True,
            summary=("kernel accepted proof" if accepted else "kernel rejected proof"),
            evidence={"kernel_executed": True, "accepted": accepted},
            created_at=datetime.now(UTC),
        )


async def test_lean_runs_the_shared_matched_developmental_episode(database, tmp_path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalog = ArtifactCatalog(artifacts)
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    memory = LessonMemory()
    runs = RunStore()
    generator = LeanMathCorpusGenerator()
    created_at = datetime(2026, 8, 8, tzinfo=UTC)
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=83,
        groups_per_family=1,
        siblings_per_group=3,
        families=(LeanMathFamily.INT_LINEAR_EQUATION,),
        created_at=created_at,
    )
    other_generator = AppellateCorpusGenerator()
    other_items = other_generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=84,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AppellateScenarioFamily.AMBIGUOUS_VIDEO,),
        created_at=created_at,
    )

    def student_response(request) -> str:
        if ":revision" in request.request_id or ":transfer:treatment" in request.request_id:
            return "by\n  omega"
        return "by\n  trivial"

    student = CallbackGenerationClient(student_response, "student-test")
    teacher = CallbackGenerationClient(teacher_response, "teacher-test")
    async with database.transaction() as session:
        for competency in other_generator.competencies(created_at=created_at):
            await registry.register_competency(session, competency)
        for competency in generator.competencies(created_at=created_at):
            await registry.register_competency(session, competency)
        await registry.register_items(session, other_items)
        await registry.register_items(session, items)
        state = await states.create_student(
            session,
            student_id="lean-student",
            checkpoint_id="checkpoint-test",
            runtime_id="runtime-test",
            research_role=ResearchRole.TARGET,
            initial_working_state={"prior": "persistent student cognition"},
        )
        run_id = await runs.create(
            session,
            run_id="run-lean-developmental",
            payload={
                "student_id": "lean-student",
                "state_id": state.state_id,
                "domain_id": "math.lean",
                "pool": CorpusPool.CURRICULUM.value,
                "experiment_seed": 29,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )

    handler = DomainDevelopmentalWorkflowHandler(
        authority=LeanDevelopmentalAuthority(_KernelDouble()),  # type: ignore[arg-type]
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(catalog),
        experiments=experiments,
        provenance=ProvenanceLedger(code_revision="test", environment="test"),
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher
        ),
        teacher_provider="teacher-test",
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id="runtime-test",
        student_runtime_version="1",
        student_checkpoint_id="checkpoint-test",
        student_role=ResearchRole.TARGET,
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id="worker-test",
        idle_poll_seconds=0,
    )

    actions = await supervisor.run(budget=64)

    assert actions == 10
    assert len(student.calls) == 4
    assert len(teacher.calls) == 1
    assert all(call.store is False for call in (*student.calls, *teacher.calls))
    async with database.transaction() as session:
        run = await session.get(RunRow, run_id)
        assert run is not None
        assert run.state == RunState.COMPLETE.value
        assert run.sequence == 10
        episode = await session.get(EpisodeRow, f"episode-{run_id}")
        assert episode is not None
        assert episode.status == "complete"
        assert episode.record_json["task_item_id"].startswith("item-lean-")
        assert episode.record_json["teaching_outcome"] == "successful_transfer"
        assert episode.record_json["domain_evidence_refs"]
        assert await session.scalar(select(func.count()).select_from(ExternalCallRow)) == 5


async def test_appellate_runs_hard_gates_then_evidence_bound_adjudication(
    database, tmp_path
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "appellate-artifacts")
    catalog = ArtifactCatalog(artifacts)
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    memory = LessonMemory()
    runs = RunStore()
    generator = AppellateCorpusGenerator()
    created_at = datetime(2026, 8, 8, tzinfo=UTC)
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=97,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AppellateScenarioFamily.AMBIGUOUS_VIDEO,),
        created_at=created_at,
    )
    scenarios = {
        scenario.scenario_id: scenario
        for item in items
        for scenario in (
            AppellateScenarioManifest.model_validate(
                item.verifier_spec.parameters["scenario"], strict=False
            ),
        )
    }

    def student_response(request) -> str:
        payload = json.loads(str(request.input))
        scenario = AppellateScenarioManifest.model_validate(
            payload["closed_task"]["scenario"], strict=False
        )
        submission = build_submission(scenario)
        valid = ":revision" in request.request_id or ":transfer:treatment" in request.request_id
        if not valid:
            submission = submission.model_copy(
                update={
                    "citations": tuple(
                        citation.model_copy(
                            update={"quoted_text": "a court may weigh the evidence"}
                        )
                        if citation.citation_id == "cite-tolan"
                        else citation
                        for citation in submission.citations
                    )
                }
            )
        draft = AppellateSubmissionDraft(
            sections=submission.sections,
            claims=submission.claims,
            citations=submission.citations,
            compliance=AppellateComplianceDraft(
                counted_words=submission.compliance_certificate.counted_words,
                typeface_points=submission.compliance_certificate.typeface_points,
                uses_word_count_limit=(submission.compliance_certificate.uses_word_count_limit),
                service_certified=submission.compliance_certificate.service_certified,
            ),
        )
        return draft.model_dump_json()

    def adjudicator_response(request) -> str:
        payload = json.loads(str(request.input))
        scenario = scenarios[payload["scope"]["scenario_id"]]
        submission = build_submission(scenario)
        assessment = build_semantic_assessment(scenario, submission)
        return AppellateSemanticAssessmentDraft(
            claim_assessments=assessment.claim_assessments,
            adverse_authority_assessments=assessment.adverse_authority_assessments,
            issue_coverage=assessment.issue_coverage,
            preservation_coverage=assessment.preservation_coverage,
            remedy_coverage=assessment.remedy_coverage,
        ).model_dump_json()

    student = CallbackGenerationClient(student_response, "student-test")
    teacher = CallbackGenerationClient(teacher_response, "teacher-test")
    adjudicator = CallbackGenerationClient(adjudicator_response, "adjudicator-test")
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=created_at):
            await registry.register_competency(session, competency)
        await registry.register_items(session, items)
        state = await states.create_student(
            session,
            student_id="appellate-student",
            checkpoint_id="checkpoint-test",
            runtime_id="runtime-test",
            research_role=ResearchRole.TARGET,
            initial_working_state={"prior": "persistent student cognition"},
        )
        run_id = await runs.create(
            session,
            run_id="run-appellate-developmental",
            payload={
                "student_id": "appellate-student",
                "state_id": state.state_id,
                "domain_id": "legal.appellate.fourth_circuit",
                "pool": CorpusPool.CURRICULUM.value,
                "experiment_seed": 37,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )

    adjudicator_executor = IdempotentGenerationExecutor(
        database=database, artifacts=artifacts, client=adjudicator
    )
    handler = DomainDevelopmentalWorkflowHandler(
        authority=AppellateDevelopmentalAuthority(
            adjudicator=AppellateAdjudicationService(
                executor=adjudicator_executor,
                provider="adjudicator-test",
            )
        ),
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(catalog),
        experiments=experiments,
        provenance=ProvenanceLedger(code_revision="test", environment="test"),
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher
        ),
        teacher_provider="teacher-test",
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id="runtime-test",
        student_runtime_version="1",
        student_checkpoint_id="checkpoint-test",
        student_role=ResearchRole.TARGET,
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id="worker-test",
        idle_poll_seconds=0,
    )

    actions = await supervisor.run(budget=64)

    assert actions == 10
    assert len(student.calls) == 4
    assert len(teacher.calls) == 1
    assert len(adjudicator.calls) == 2
    assert all(call.store is False for call in (*student.calls, *teacher.calls, *adjudicator.calls))
    async with database.transaction() as session:
        run = await session.get(RunRow, run_id)
        assert run is not None
        assert run.state == RunState.COMPLETE.value
        episode = await session.get(EpisodeRow, f"episode-{run_id}")
        assert episode is not None
        assert episode.status == "complete"
        assert episode.record_json["teaching_outcome"] == "successful_transfer"
        assert await session.scalar(select(func.count()).select_from(ExternalCallRow)) == 7
