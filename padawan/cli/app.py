from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from padawan.adapters.heirloom.exporter import HeirloomAuditExporter
from padawan.agent.loop import AutonomousResearchLoop
from padawan.artifacts.factory import build_artifact_backend
from padawan.artifacts.store import ArtifactCatalog, artifact_put_bytes
from padawan.config.composition import (
    StudentProvider,
    TeacherProvider,
    build_live_application,
)
from padawan.config.logging import configure_logging
from padawan.config.settings import Settings
from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.contracts import HardGateResult, VerifierDisposition
from padawan.domains.lean_math import (
    LeanMathCorpusGenerator,
    LeanMathFamily,
    LeanProofTask,
    LeanVerifier,
)
from padawan.domains.legal.appellate import (
    AppellateBriefVerifier,
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSubmission,
    build_fourth_circuit_pack,
)
from padawan.domains.magellan_improvement import (
    MagellanEnvironmentAssessment,
    MagellanEnvironmentInspector,
    MagellanScenarioFamily,
    MagellanScenarioGenerator,
)
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine
from padawan.governance.manifests import CommandManifest, ManifestWriter, now
from padawan.governance.policy import ExportPolicy
from padawan.models.contracts import CorpusPool, SourceRights, TeacherMode
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    CorpusItemRow,
    InstanceGroupRow,
    LessonVersionRow,
    StudentRow,
    TemplateFamilyRow,
)
from padawan.orchestration.state_machine import RunStore
from padawan.provenance.ledger import ProvenanceLedger
from padawan.reporting.service import ReportingService
from padawan.state.store import StateStore
from padawan.training.compiler import TrainingCompiler
from padawan.training.contracts import (
    TrainingSourceDecision,
    TrainingSourceDocument,
    TrainingSourceStatus,
)
from padawan.training.sources import TrainingSourceRegistry

app = typer.Typer(
    name="padawan",
    help="Evidence-governed persistent student research system.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Database lifecycle.")
corpus_app = typer.Typer(help="Governed corpus operations.")
corpus_generate_app = typer.Typer(help="Generate deterministic corpus inventory.")
supervisor_app = typer.Typer(help="Autonomous research supervisor.")
worker_app = typer.Typer(help="Durable worker actions.")
experiment_app = typer.Typer(help="State-forked experiments.")
episode_app = typer.Typer(help="Developmental episode inspection.")
state_app = typer.Typer(help="Persistent student-state operations.")
memory_app = typer.Typer(help="Versioned lesson memory.")
report_app = typer.Typer(help="Research reports.")
provenance_app = typer.Typer(help="Cryptographic provenance.")
export_app = typer.Typer(help="Policy-governed exports.")
verify_app = typer.Typer(help="Deterministic domain verifier operations.")
training_app = typer.Typer(help="Rights-aware internal training-product compilation.")
training_source_app = typer.Typer(help="Governed continued-pretraining source admission.")

app.add_typer(db_app, name="db")
app.add_typer(corpus_app, name="corpus")
corpus_app.add_typer(corpus_generate_app, name="generate")
app.add_typer(supervisor_app, name="supervisor")
app.add_typer(worker_app, name="worker")
app.add_typer(experiment_app, name="experiment")
app.add_typer(episode_app, name="episode")
app.add_typer(state_app, name="state")
app.add_typer(memory_app, name="memory")
app.add_typer(report_app, name="report")
app.add_typer(provenance_app, name="provenance")
app.add_typer(export_app, name="export")
app.add_typer(verify_app, name="verify")
app.add_typer(training_app, name="training")
training_app.add_typer(training_source_app, name="source")


@app.callback()
def root(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    settings = Settings.load()
    configure_logging(settings.log_level)
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["settings"] = settings


@db_app.command("migrate")
def db_migrate(ctx: typer.Context, revision: str = typer.Option("head", "--revision")) -> None:
    settings = _settings(ctx)

    def operation() -> dict[str, Any]:
        configuration = Config()
        configuration.set_main_option("script_location", str(_migration_root()))
        configuration.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(configuration, revision)
        return {"database_url": _redact_database_url(settings.database_url), "revision": revision}

    _run_command(ctx, "db migrate", operation)


@corpus_generate_app.command("algebra")
def corpus_generate_algebra(
    ctx: typer.Context,
    pool: CorpusPool = typer.Option(CorpusPool.CURRICULUM, "--pool"),
    seed: int = typer.Option(20260801, "--seed"),
    groups_per_family: int = typer.Option(2, "--groups-per-family", min=1),
    siblings_per_group: int = typer.Option(3, "--siblings-per-group", min=2),
    family: list[AlgebraFamily] | None = typer.Option(None, "--family"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        generator = AlgebraCorpusGenerator()
        registry = CorpusRegistry()
        selected = tuple(family) if family else None
        try:
            records = generator.generate(
                pool=pool,
                seed=seed,
                groups_per_family=groups_per_family,
                siblings_per_group=siblings_per_group,
                families=selected,
            )
            async with database.transaction() as session:
                for competency in generator.competencies():
                    await registry.register_competency(session, competency)
                stored = await registry.register_items(session, records)
            return {
                "generated": len(records),
                "registered": len(stored),
                "pool": pool.value,
                "families": sorted({record.template_family_id for record in records}),
                "seed": seed,
            }
        finally:
            await database.close()

    _run_command(ctx, "corpus generate algebra", operation)


@corpus_generate_app.command("lean-math")
def corpus_generate_lean_math(
    ctx: typer.Context,
    pool: CorpusPool = typer.Option(CorpusPool.CURRICULUM, "--pool"),
    seed: int = typer.Option(20260801, "--seed"),
    groups_per_family: int = typer.Option(2, "--groups-per-family", min=1),
    siblings_per_group: int = typer.Option(2, "--siblings-per-group", min=2),
    family: list[LeanMathFamily] | None = typer.Option(None, "--family"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        generator = LeanMathCorpusGenerator()
        registry = CorpusRegistry()
        selected = tuple(family) if family else None
        try:
            records = generator.generate(
                pool=pool,
                seed=seed,
                groups_per_family=groups_per_family,
                siblings_per_group=siblings_per_group,
                families=selected,
            )
            async with database.transaction() as session:
                for competency in generator.competencies():
                    await registry.register_competency(session, competency)
                stored = await registry.register_items(session, records)
            return {
                "domain_id": "math.lean",
                "generated": len(records),
                "registered": len(stored),
                "pool": pool.value,
                "families": sorted({record.template_family_id for record in records}),
                "seed": seed,
            }
        finally:
            await database.close()

    _run_command(ctx, "corpus generate lean-math", operation)


@corpus_generate_app.command("magellan")
def corpus_generate_magellan(
    ctx: typer.Context,
    pool: CorpusPool = typer.Option(CorpusPool.CURRICULUM, "--pool"),
    seed: int = typer.Option(20260801, "--seed"),
    groups_per_family: int = typer.Option(1, "--groups-per-family", min=1),
    siblings_per_group: int = typer.Option(2, "--siblings-per-group", min=2),
    family: list[MagellanScenarioFamily] | None = typer.Option(None, "--family"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        assessment = _magellan_environment_assessment(settings)
        if not assessment.ready or assessment.handshake is None:
            raise ValueError(
                "Magellan corpus generation requires a ready environment handshake: "
                + "; ".join(assessment.blockers)
            )
        database = Database(settings.database_url)
        generator = MagellanScenarioGenerator(
            environment_fingerprint=assessment.handshake.fingerprint
        )
        registry = CorpusRegistry()
        selected = tuple(family) if family else None
        try:
            records = generator.generate(
                pool=pool,
                seed=seed,
                groups_per_family=groups_per_family,
                siblings_per_group=siblings_per_group,
                families=selected,
            )
            async with database.transaction() as session:
                for competency in generator.competencies():
                    await registry.register_competency(session, competency)
                stored = await registry.register_items(session, records)
            return {
                "domain_id": "agent.magellan_improvement",
                "environment_id": assessment.handshake.environment_id,
                "environment_fingerprint": assessment.handshake.fingerprint,
                "repository_snapshot_id": assessment.repository.snapshot_id,
                "generated": len(records),
                "registered": len(stored),
                "pool": pool.value,
                "families": sorted({record.template_family_id for record in records}),
                "seed": seed,
            }
        finally:
            await database.close()

    _run_command(ctx, "corpus generate magellan", operation)


@corpus_generate_app.command("appellate")
def corpus_generate_appellate(
    ctx: typer.Context,
    pool: CorpusPool = typer.Option(CorpusPool.CURRICULUM, "--pool"),
    seed: int = typer.Option(20260802, "--seed"),
    groups_per_family: int = typer.Option(1, "--groups-per-family", min=1),
    siblings_per_group: int = typer.Option(2, "--siblings-per-group", min=2),
    family: list[AppellateScenarioFamily] | None = typer.Option(None, "--family"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        generator = AppellateCorpusGenerator()
        registry = CorpusRegistry()
        selected = tuple(family) if family else None
        try:
            records = generator.generate(
                pool=pool,
                seed=seed,
                groups_per_family=groups_per_family,
                siblings_per_group=siblings_per_group,
                families=selected,
            )
            async with database.transaction() as session:
                for competency in generator.competencies():
                    await registry.register_competency(session, competency)
                stored = await registry.register_items(session, records)
            pack = build_fourth_circuit_pack()
            return {
                "domain_id": "legal.appellate.fourth_circuit",
                "court_pack_id": pack.pack_id,
                "court_pack_digest": pack.pack_digest,
                "currentness_capability": "unknown_without_citator",
                "generated": len(records),
                "registered": len(stored),
                "pool": pool.value,
                "families": sorted({record.template_family_id for record in records}),
                "seed": seed,
            }
        finally:
            await database.close()

    _run_command(ctx, "corpus generate appellate", operation)


@verify_app.command("lean")
def verify_lean(
    ctx: typer.Context,
    statement: str = typer.Option(..., "--statement"),
    proof_file: Path = typer.Option(
        ...,
        "--proof-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    task_id: str | None = typer.Option(None, "--task-id"),
) -> None:
    def operation() -> dict[str, Any]:
        proof = proof_file.read_text(encoding="utf-8")
        selected_task_id = task_id or f"lean-task-{sha256_digest(statement)[7:23]}"
        result = _lean_verifier(_settings(ctx)).verify(
            LeanProofTask(task_id=selected_task_id, statement=statement, proof=proof)
        )
        gate = HardGateResult(
            gate_id=f"lean-kernel:{selected_task_id}",
            passed=result.disposition == VerifierDisposition.VERIFIED,
            disposition=result.disposition,
            evidence_refs=(result.result_id,),
            reason=result.summary,
        )
        return {
            "verification": result.model_dump(mode="json"),
            "hard_gate": gate.model_dump(mode="json"),
        }

    _run_command(ctx, "verify lean", operation)


@verify_app.command("magellan-environment")
def verify_magellan_environment(ctx: typer.Context) -> None:
    def operation() -> dict[str, Any]:
        assessment = _magellan_environment_assessment(_settings(ctx))
        return assessment.model_dump(mode="json")

    _run_command(ctx, "verify magellan-environment", operation)


@verify_app.command("appellate")
def verify_appellate(
    ctx: typer.Context,
    scenario_file: Path = typer.Option(
        ...,
        "--scenario-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    submission_file: Path = typer.Option(
        ...,
        "--submission-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    semantic_assessment_file: Path | None = typer.Option(
        None,
        "--semantic-assessment-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
) -> None:
    def operation() -> dict[str, Any]:
        scenario = AppellateScenarioManifest.model_validate_json(
            scenario_file.read_text(encoding="utf-8"), strict=False
        )
        submission = AppellateSubmission.model_validate_json(
            submission_file.read_text(encoding="utf-8"), strict=False
        )
        assessment = (
            AppellateSemanticAssessment.model_validate_json(
                semantic_assessment_file.read_text(encoding="utf-8"), strict=False
            )
            if semantic_assessment_file is not None
            else None
        )
        bundle = AppellateBriefVerifier().verify(
            pack=build_fourth_circuit_pack(),
            scenario=scenario,
            submission=submission,
            semantic_assessment=assessment,
        )
        return bundle.model_dump(mode="json")

    _run_command(ctx, "verify appellate", operation)


@corpus_app.command("validate")
def corpus_validate(ctx: typer.Context) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                errors = await CorpusRegistry().validate_inventory(session)
            if errors:
                raise ValueError("; ".join(errors))
            return {"valid": True, "errors": []}
        finally:
            await database.close()

    _run_command(ctx, "corpus validate", operation)


@corpus_app.command("inspect")
def corpus_inspect(ctx: typer.Context, limit: int = typer.Option(20, "--limit", min=1)) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                counts = dict(
                    (
                        await session.execute(
                            select(CorpusItemRow.status, func.count()).group_by(
                                CorpusItemRow.status
                            )
                        )
                    )
                    .tuples()
                    .all()
                )
                pool_counts = dict(
                    (
                        await session.execute(
                            select(CorpusItemRow.pool, func.count()).group_by(CorpusItemRow.pool)
                        )
                    )
                    .tuples()
                    .all()
                )
                items = (
                    await session.scalars(
                        select(CorpusItemRow)
                        .order_by(CorpusItemRow.created_at, CorpusItemRow.item_id)
                        .limit(limit)
                    )
                ).all()
                family_count = await session.scalar(
                    select(func.count()).select_from(TemplateFamilyRow)
                )
                group_count = await session.scalar(
                    select(func.count()).select_from(InstanceGroupRow)
                )
            return {
                "items_by_status": counts,
                "items_by_pool": pool_counts,
                "template_families": int(family_count or 0),
                "instance_groups": int(group_count or 0),
                "items": [
                    {
                        "item_id": row.item_id,
                        "pool": row.pool,
                        "status": row.status,
                        "instance_group_id": row.instance_group_id,
                        "difficulty": row.difficulty,
                    }
                    for row in items
                ],
            }
        finally:
            await database.close()

    _run_command(ctx, "corpus inspect", operation)


@supervisor_app.command("run")
def supervisor_run(
    ctx: typer.Context,
    student_id: str = typer.Option("inkling-small-research", "--student-id"),
    episode_budget: int = typer.Option(1, "--episode-budget", min=1),
    experiment_seed: int = typer.Option(20260801, "--seed"),
    student_provider: StudentProvider = typer.Option("inkling", "--student-provider"),
    teacher_provider: TeacherProvider = typer.Option("openai", "--teacher-provider"),
    student_model: str | None = typer.Option(None, "--student-model"),
    teacher_model: str | None = typer.Option(None, "--teacher-model"),
    compatible_base_url: str | None = typer.Option(None, "--compatible-base-url"),
    allow_legacy_student_fallback: bool = typer.Option(False, "--allow-legacy-student-fallback"),
    bootstrap: bool = typer.Option(False, "--bootstrap"),
) -> None:
    _run_command(
        ctx,
        "supervisor run",
        lambda: _live_research_run(
            settings=_settings(ctx),
            student_id=student_id,
            episode_budget=episode_budget,
            experiment_seed=experiment_seed,
            student_provider=student_provider,
            teacher_provider=teacher_provider,
            student_model=student_model,
            teacher_model=teacher_model,
            compatible_base_url=compatible_base_url,
            allow_legacy_student_fallback=allow_legacy_student_fallback,
            bootstrap=bootstrap,
        ),
    )


@worker_app.command("run")
def worker_run(
    ctx: typer.Context,
    action_budget: int = typer.Option(1, "--action-budget", min=1),
    student_provider: StudentProvider = typer.Option("inkling", "--student-provider"),
    teacher_provider: TeacherProvider = typer.Option("openai", "--teacher-provider"),
    student_model: str | None = typer.Option(None, "--student-model"),
    teacher_model: str | None = typer.Option(None, "--teacher-model"),
    compatible_base_url: str | None = typer.Option(None, "--compatible-base-url"),
) -> None:
    async def operation() -> dict[str, Any]:
        application = await build_live_application(
            _settings(ctx),
            student_provider=student_provider,
            teacher_provider=teacher_provider,
            student_model=student_model,
            teacher_model=teacher_model,
            compatible_base_url=compatible_base_url,
        )
        try:
            completed = await application.supervisor.run(budget=action_budget)
            return {"durable_actions_completed": completed, "action_budget": action_budget}
        finally:
            await application.close()

    _run_command(ctx, "worker run", operation)


@supervisor_app.command("pause")
def supervisor_pause(ctx: typer.Context, run_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                await RunStore().pause(session, run_id=run_id)
            return {"run_id": run_id, "paused": True}
        finally:
            await database.close()

    _run_command(ctx, "supervisor pause", operation)


@supervisor_app.command("resume")
def supervisor_resume(ctx: typer.Context, run_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                await RunStore().resume(session, run_id=run_id)
            return {"run_id": run_id, "paused": False}
        finally:
            await database.close()

    _run_command(ctx, "supervisor resume", operation)


@experiment_app.command("run")
def experiment_run(
    ctx: typer.Context,
    student_id: str = typer.Option("inkling-small-research", "--student-id"),
    blocks: int = typer.Option(1, "--blocks", min=1),
    seed: int = typer.Option(20260801, "--seed"),
    student_provider: StudentProvider = typer.Option("inkling", "--student-provider"),
    teacher_provider: TeacherProvider = typer.Option("openai", "--teacher-provider"),
    student_model: str | None = typer.Option(None, "--student-model"),
    teacher_model: str | None = typer.Option(None, "--teacher-model"),
    compatible_base_url: str | None = typer.Option(None, "--compatible-base-url"),
    bootstrap: bool = typer.Option(False, "--bootstrap"),
) -> None:
    _run_command(
        ctx,
        "experiment run",
        lambda: _live_research_run(
            settings=_settings(ctx),
            student_id=student_id,
            episode_budget=blocks,
            experiment_seed=seed,
            student_provider=student_provider,
            teacher_provider=teacher_provider,
            student_model=student_model,
            teacher_model=teacher_model,
            compatible_base_url=compatible_base_url,
            allow_legacy_student_fallback=False,
            bootstrap=bootstrap,
        ),
    )


@episode_app.command("inspect")
def episode_inspect(ctx: typer.Context, episode_id: str) -> None:
    async def operation() -> dict[str, object]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        artifacts = build_artifact_backend(settings)
        try:
            store = EpisodeStore(ArtifactCatalog(artifacts))
            async with database.transaction() as session:
                return await store.inspect(session, episode_id=episode_id)
        finally:
            await _close_resource(artifacts)
            await database.close()

    _run_command(ctx, "episode inspect", operation)


@state_app.command("inspect")
def state_inspect(
    ctx: typer.Context,
    state_id: str | None = typer.Option(None, "--state-id"),
    student_id: str | None = typer.Option(None, "--student-id"),
) -> None:
    async def operation() -> dict[str, Any]:
        if not state_id and not student_id:
            raise ValueError("provide --state-id or --student-id")
        database = Database(_settings(ctx).database_url)
        states = StateStore()
        try:
            async with database.transaction() as session:
                resolved = state_id
                if resolved is None and student_id:
                    student = await session.get(StudentRow, student_id)
                    if student is None or student.canonical_state_id is None:
                        raise KeyError(student_id)
                    resolved = student.canonical_state_id
                if resolved is None:
                    raise AssertionError("state resolution failed")
                record = await states.get(session, state_id=resolved)
                lineage = await states.lineage(session, state_id=resolved)
            return {"state": record.model_dump(mode="json"), "lineage": lineage}
        finally:
            await database.close()

    _run_command(ctx, "state inspect", operation)


@state_app.command("fork")
def state_fork(
    ctx: typer.Context,
    state_id: str,
    experiment_id: str = typer.Option(..., "--experiment-id"),
    treatment: str = typer.Option("frontier_teacher_critique", "--treatment"),
    control: str = typer.Option("no_intervention", "--control"),
) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                fork = await StateStore().fork(
                    session,
                    parent_state_id=state_id,
                    experiment_id=experiment_id,
                    intervention={"treatment_condition": treatment, "control_condition": control},
                )
            return fork.model_dump(mode="json")
        finally:
            await database.close()

    _run_command(ctx, "state fork", operation)


@memory_app.command("inspect")
def memory_inspect(
    ctx: typer.Context,
    lesson_id: str | None = typer.Option(None, "--lesson-id"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            async with database.transaction() as session:
                query = select(LessonVersionRow).order_by(
                    LessonVersionRow.lesson_id, LessonVersionRow.version.desc()
                )
                if lesson_id:
                    query = query.where(LessonVersionRow.lesson_id == lesson_id)
                rows = (await session.scalars(query.limit(limit))).all()
            return {"lesson_versions": [row.record_json for row in rows]}
        finally:
            await database.close()

    _run_command(ctx, "memory inspect", operation)


@report_app.command("experiment")
def report_experiment(ctx: typer.Context, experiment_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            service = ReportingService(database, ExperimentEngine(StateStore()))
            return await service.experiment(experiment_id)
        finally:
            await database.close()

    _run_command(ctx, "report experiment", operation)


@report_app.command("study")
def report_study(ctx: typer.Context, study_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            service = ReportingService(database, ExperimentEngine(StateStore()))
            return await service.study(study_id)
        finally:
            await database.close()

    _run_command(ctx, "report study", operation)


@report_app.command("reward")
def report_reward(ctx: typer.Context, reward_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            service = ReportingService(database, ExperimentEngine(StateStore()))
            return await service.reward(reward_id)
        finally:
            await database.close()

    _run_command(ctx, "report reward", operation)


@report_app.command("checkpoint")
def report_checkpoint(ctx: typer.Context, checkpoint_id: str) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            service = ReportingService(database, ExperimentEngine(StateStore()))
            return await service.checkpoint(checkpoint_id)
        finally:
            await database.close()

    _run_command(ctx, "report checkpoint", operation)


@report_app.command("operations")
def report_operations(ctx: typer.Context) -> None:
    async def operation() -> dict[str, Any]:
        database = Database(_settings(ctx).database_url)
        try:
            return await ReportingService(database, ExperimentEngine(StateStore())).operations()
        finally:
            await database.close()

    _run_command(ctx, "report operations", operation)


@provenance_app.command("verify")
def provenance_verify(
    ctx: typer.Context, stream_id: str = typer.Option("global", "--stream-id")
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        try:
            ledger = ProvenanceLedger(
                code_revision=settings.code_revision, environment=settings.environment
            )
            async with database.transaction() as session:
                result = await ledger.verify(session, stream_id=stream_id)
            if not result.valid:
                raise ValueError("provenance verification failed: " + "; ".join(result.errors))
            return asdict(result)
        finally:
            await database.close()

    _run_command(ctx, "provenance verify", operation)


@export_app.command("heirloom")
def export_heirloom(
    ctx: typer.Context,
    episode_id: str,
    output_root: Path = typer.Option(..., "--output-root"),
    hmac_key_env: str = typer.Option("PADAWAN_EXPORT_HMAC_KEY", "--hmac-key-env"),
) -> None:
    async def operation() -> dict[str, Any]:
        secret = os.environ.get(hmac_key_env)
        if secret is None:
            raise ValueError(f"{hmac_key_env} is required for opaque audit identifiers")
        exporter = HeirloomAuditExporter(
            database=Database(_settings(ctx).database_url),
            policy=ExportPolicy(),
            audit_hmac_key=secret.encode(),
        )
        try:
            result = await exporter.export(episode_id=episode_id, output_root=output_root)
            return cast(dict[str, Any], _jsonable(result))
        finally:
            await exporter.database.close()

    _run_command(ctx, "export heirloom", operation)


@training_app.command("compile")
def training_compile(
    ctx: typer.Context,
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Timezone-aware ISO-8601 snapshot; defaults to the latest source watermark.",
    ),
    eligibility_policy_id: str | None = typer.Option(None, "--eligibility-policy-id"),
    eligibility_policy_version: str | None = typer.Option(None, "--eligibility-policy-version"),
    checkpoint_id: list[str] | None = typer.Option(None, "--checkpoint-id"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        artifacts = build_artifact_backend(settings)
        compiler = TrainingCompiler(artifacts, ArtifactCatalog(artifacts))
        try:
            async with database.transaction() as session:
                build = await compiler.compile(
                    session,
                    as_of=_parse_timestamp(as_of) if as_of is not None else None,
                    eligibility_policy_id=eligibility_policy_id,
                    eligibility_policy_version=eligibility_policy_version,
                    checkpoint_ids=tuple(sorted(set(checkpoint_id or ()))),
                )
            return {
                "bundle_id": build.manifest.bundle_id,
                "manifest_digest": build.manifest_ref.digest,
                "manifest_artifact": build.manifest_ref.model_dump(mode="json"),
                "source_snapshot_digest": build.manifest.source_snapshot_digest,
                "as_of": build.manifest.invocation.as_of,
                "internal_only": True,
                "included_counts": build.manifest.included_counts,
                "exclusion_counts": build.manifest.exclusion_counts,
            }
        finally:
            await _close_resource(artifacts)
            await database.close()

    _run_command(ctx, "training compile", operation)


@training_app.command("verify")
def training_verify(ctx: typer.Context, bundle_id: str) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        artifacts = build_artifact_backend(settings)
        try:
            async with database.transaction() as session:
                verification = await TrainingCompiler(artifacts).verify(
                    session, bundle_id=bundle_id
                )
            if not verification.valid:
                raise ValueError(
                    "training bundle verification failed: " + "; ".join(verification.errors)
                )
            return verification.model_dump(mode="json")
        finally:
            await _close_resource(artifacts)
            await database.close()

    _run_command(ctx, "training verify", operation)


@training_app.command("inspect")
def training_inspect(ctx: typer.Context, bundle_id: str) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        artifacts = build_artifact_backend(settings)
        try:
            async with database.transaction() as session:
                manifest = await TrainingCompiler(artifacts).get_manifest(
                    session, bundle_id=bundle_id
                )
            return manifest.model_dump(mode="json")
        finally:
            await _close_resource(artifacts)
            await database.close()

    _run_command(ctx, "training inspect", operation)


@training_source_app.command("admit")
def training_source_admit(
    ctx: typer.Context,
    content_file: Path = typer.Option(
        ...,
        "--content-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    rights_manifest: Path = typer.Option(
        ...,
        "--rights-manifest",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    source_id: str = typer.Option(..., "--source-id"),
    source_version: str = typer.Option(..., "--source-version"),
    title: str = typer.Option(..., "--title"),
    language: str = typer.Option("en", "--language"),
    media_type: str = typer.Option("text/plain; charset=utf-8", "--media-type"),
    status: TrainingSourceStatus = typer.Option(TrainingSourceStatus.REVIEW_REQUIRED, "--status"),
    quality_evidence: list[str] | None = typer.Option(None, "--quality-evidence"),
    contaminated: bool = typer.Option(False, "--contaminated"),
    contamination_evidence: list[str] | None = typer.Option(None, "--contamination-evidence"),
    supersedes_document_id: str | None = typer.Option(None, "--supersedes-document-id"),
    admitted_by: str = typer.Option("cli.operator", "--admitted-by"),
    reason: str = typer.Option("initial source admission", "--reason"),
) -> None:
    async def operation() -> dict[str, Any]:
        settings = _settings(ctx)
        database = Database(settings.database_url)
        artifacts = build_artifact_backend(settings)
        timestamp = now()
        try:
            rights_payload = json.loads(rights_manifest.read_text(encoding="utf-8"))
            rights = SourceRights.model_validate(rights_payload, strict=False)
            content_ref = await artifact_put_bytes(
                artifacts,
                content_file.read_bytes(),
                media_type=media_type,
                restricted=True,
                raw_data=True,
            )
            document_identity = {
                "source_id": source_id,
                "source_version": source_version,
                "content_digest": content_ref.digest,
                "rights_digest": sha256_digest(rights.model_dump(mode="json")),
            }
            document_id = f"source-{sha256_digest(document_identity)[7:39]}"
            document = TrainingSourceDocument(
                document_id=document_id,
                source_id=source_id,
                source_version=source_version,
                supersedes_document_id=supersedes_document_id,
                title=title,
                language=language,
                media_type=media_type,
                content_ref=content_ref,
                content_digest=content_ref.digest,
                rights=rights,
                rights_digest=sha256_digest(rights.model_dump(mode="json")),
                quality_evidence_refs=tuple(sorted(set(quality_evidence or ()))),
                admitted_by=admitted_by,
                created_at=timestamp,
            )
            decision_evidence = tuple(
                sorted(set(quality_evidence or ()).union(contamination_evidence or ()))
            )
            decision_identity = {
                "document_id": document_id,
                "status": status.value,
                "contaminated": contaminated,
                "evidence_refs": decision_evidence,
                "created_at": timestamp,
            }
            decision = TrainingSourceDecision(
                decision_id=f"source-decision-{sha256_digest(decision_identity)[7:39]}",
                document_id=document_id,
                status=status,
                contaminated=contaminated,
                reason=reason,
                evidence_refs=decision_evidence,
                decided_by=admitted_by,
                created_at=timestamp,
            )
            registry = TrainingSourceRegistry(ArtifactCatalog(artifacts))
            async with database.transaction() as session:
                admitted = await registry.admit(
                    session,
                    document=document,
                    initial_decision=decision,
                )
            return {
                "document": admitted.document.model_dump(mode="json"),
                "decision": admitted.decision.model_dump(mode="json"),
            }
        finally:
            await _close_resource(artifacts)
            await database.close()

    _run_command(ctx, "training source admit", operation)


async def _live_research_run(
    *,
    settings: Settings,
    student_id: str,
    episode_budget: int,
    experiment_seed: int,
    student_provider: StudentProvider,
    teacher_provider: TeacherProvider,
    student_model: str | None,
    teacher_model: str | None,
    compatible_base_url: str | None,
    allow_legacy_student_fallback: bool,
    bootstrap: bool,
) -> dict[str, Any]:
    application = await build_live_application(
        settings,
        student_provider=student_provider,
        teacher_provider=teacher_provider,
        student_model=student_model,
        teacher_model=teacher_model,
        compatible_base_url=compatible_base_url,
        allow_legacy_student_fallback=allow_legacy_student_fallback,
    )
    try:
        selected_model = (
            student_model
            or (settings.inkling_model if student_provider == "inkling" else None)
            or (settings.openai_model if student_provider == "openai" else None)
            or settings.compatible_model
        )
        if selected_model is None:
            raise ValueError("student model cannot be resolved")
        async with application.database.transaction() as session:
            student = await session.get(StudentRow, student_id)
            if student is None:
                await application.states.create_student(
                    session,
                    student_id=student_id,
                    checkpoint_id=selected_model,
                    runtime_id=student_provider,
                    research_role=application.student_role,
                    initial_working_state={
                        "institution": "padawan",
                        "policy": "clean item, continuous student",
                    },
                )
            elif student.research_role != application.student_role.value:
                raise ValueError(
                    f"research identity {student_id} is {student.research_role}, not "
                    f"{application.student_role.value}; use a separate baseline identity"
                )
            if bootstrap:
                for competency in application.domain.competencies():
                    await application.registry.register_competency(session, competency)
                items = application.domain.generate_curriculum(
                    pool=CorpusPool.CURRICULUM,
                    seed=experiment_seed,
                    groups_per_family=max(1, episode_budget),
                    siblings_per_group=3,
                )
                await application.registry.register_items(session, list(items))
        loop = AutonomousResearchLoop(
            database=application.database,
            runs=application.runs,
            supervisor=application.supervisor,
            student_id=student_id,
            research_role=application.student_role,
        )
        result = await loop.run(
            episode_budget=episode_budget,
            experiment_seed=experiment_seed,
            teacher_mode=TeacherMode.DIAGNOSTIC_CRITIQUE,
        )
        return cast(dict[str, Any], _jsonable(result))
    finally:
        await application.close()


def _run_command(
    ctx: typer.Context,
    command_name: str,
    operation: Callable[[], object],
) -> None:
    settings = _settings(ctx)
    started = now()
    manifest_backend = build_artifact_backend(settings)
    writer = ManifestWriter(manifest_backend)
    try:
        result_or_awaitable: object = operation()
        if inspect.isawaitable(result_or_awaitable):
            result: object = asyncio.run(_await_value(cast(Awaitable[object], result_or_awaitable)))
        else:
            result = result_or_awaitable
        result_data = cast(dict[str, Any], _jsonable(result))
        manifest = CommandManifest(
            command=command_name,
            status="complete",
            started_at=started,
            completed_at=now(),
            configuration=_manifest_configuration(ctx, settings),
            result=result_data,
            error=None,
        )
        reference = writer.write(manifest, known_secrets=_known_secrets(settings))
        _emit(ctx, {**result_data, "manifest": reference.model_dump(mode="json")})
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        manifest = CommandManifest(
            command=command_name,
            status="failed",
            started_at=started,
            completed_at=now(),
            configuration=_manifest_configuration(ctx, settings),
            result={},
            error=error,
        )
        reference = writer.write(manifest, known_secrets=_known_secrets(settings))
        _emit(ctx, {"error": error, "manifest": reference.model_dump(mode="json")}, error=True)
        raise typer.Exit(code=1) from exc
    finally:
        _close_resource_sync(manifest_backend)


async def _await_value(value: Awaitable[object]) -> object:
    return await value


async def _close_resource(resource: object) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _close_resource_sync(resource: object) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        asyncio.run(_await_value(result))


def _emit(ctx: typer.Context, value: dict[str, Any], *, error: bool = False) -> None:
    if bool(ctx.obj.get("json")):
        typer.echo(json.dumps(_jsonable(value), sort_keys=True), err=error)
    else:
        typer.echo(json.dumps(_jsonable(value), sort_keys=True, indent=2), err=error)


def _settings(ctx: typer.Context) -> Settings:
    return cast(Settings, ctx.obj["settings"])


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _known_secrets(settings: Settings) -> tuple[str, ...]:
    values = []
    for secret in (
        settings.openai_api_key,
        settings.anthropic_api_key,
        settings.compatible_api_key,
    ):
        if secret is not None:
            values.append(secret.get_secret_value())
    return tuple(values)


def _manifest_configuration(ctx: typer.Context, settings: Settings) -> dict[str, object]:
    invocation = cast(dict[str, object], _jsonable(dict(ctx.params)))
    for sensitive_path in ("content_file", "rights_manifest"):
        if sensitive_path in invocation:
            invocation[sensitive_path] = {"selected": invocation[sensitive_path] is not None}
    return {
        **settings.redacted_manifest(),
        "invocation": invocation,
    }


def _redact_database_url(value: str) -> str:
    if "@" not in value or "://" not in value:
        return value
    scheme, rest = value.split("://", 1)
    return f"{scheme}://[REDACTED]@{rest.split('@', 1)[1]}"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _lean_verifier(settings: Settings) -> LeanVerifier:
    repository = _repository_root()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else repository / path

    return LeanVerifier(
        project_root=resolve(settings.lean_project_root),
        lake_executable=resolve(settings.lean_lake_executable),
        elan_home=resolve(settings.lean_elan_home),
        sandbox_mode=settings.lean_sandbox_mode,
        timeout_seconds=settings.lean_timeout_seconds,
        output_limit_bytes=settings.lean_output_limit_bytes,
        memory_limit_mb=settings.lean_memory_limit_mb,
    )


def _magellan_environment_assessment(settings: Settings) -> MagellanEnvironmentAssessment:
    if settings.magellan_repository_root is None:
        raise ValueError("PADAWAN_MAGELLAN_REPOSITORY_ROOT is not configured")
    return MagellanEnvironmentInspector().assess(
        settings.magellan_repository_root,
        handshake_path=settings.magellan_handshake_path,
    )


def _migration_root() -> Path:
    package_data = Path(__file__).resolve().parents[1] / "_migrations"
    repository_data = _repository_root() / "migrations"
    for candidate in (package_data, repository_data):
        if (candidate / "env.py").is_file() and (candidate / "versions").is_dir():
            return candidate
    raise FileNotFoundError("Padawan Alembic migration resources are not installed")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
