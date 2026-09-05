from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
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
from padawan.atlas.campaigns import (
    build_first_inkling_campaign_bundle,
    prepare_first_inkling_campaign_stage,
)
from padawan.atlas.reporting import (
    build_first_inkling_machine_report,
    render_first_inkling_report,
)
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
from padawan.experiments.controls import ResearchControlRegistry, research_corpus_digest
from padawan.experiments.defaults import build_standardized_developmental_control
from padawan.experiments.engine import ExperimentEngine
from padawan.governance.amber import AmberAuthorizationEnvelope, AmberStatus
from padawan.governance.manifests import CommandManifest, ManifestWriter, now
from padawan.governance.policy import ExportPolicy
from padawan.models.contracts import CorpusPool, SourceRights, TeacherMode
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    CorpusItemRow,
    InstanceGroupRow,
    LessonVersionRow,
    ProcessOutcomeRow,
    ProcessRolloutRow,
    ProcessTrainingEligibilityRow,
    StudentRow,
    TemplateFamilyRow,
)
from padawan.orchestration.state_machine import RunStore
from padawan.pprl.composition import build_pprl_application
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessExecutionManifest,
    ProcessOutcomeAssessment,
    ProcessProgram,
    ProcessTrainingEligibilityDecision,
    ProjectStatePayload,
)
from padawan.pprl.resource_contracts import ProcessResourceGrant
from padawan.pprl.resource_generation import ProcessGenerationResources
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
atlas_app = typer.Typer(help="Capability Atlas behavioral evaluation and boundary mapping.")
atlas_campaign_app = typer.Typer(help="Predeclared Capability Atlas campaigns.")
interaction_app = typer.Typer(help="Private Padawan Interaction Lab.")
pprl_app = typer.Typer(help="Governed persistent-process reinforcement learning.")
pprl_distribution_app = typer.Typer(help="Versioned project distributions.")
pprl_program_app = typer.Typer(help="Persistent-process program definitions.")
pprl_amber_app = typer.Typer(help="Amber authorization lifecycle and audit history.")
pprl_resource_app = typer.Typer(help="Privileged conserved funding and accounting; no dispatch.")
pprl_execution_app = typer.Typer(help="Exact process execution manifests.")
pprl_rollout_app = typer.Typer(help="Durable project rollout state and replay.")
pprl_outcome_app = typer.Typer(help="Outcome assessments with declared authority.")
pprl_eligibility_app = typer.Typer(help="Separate process-training eligibility decisions.")

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
app.add_typer(atlas_app, name="atlas")
atlas_app.add_typer(atlas_campaign_app, name="campaign")
app.add_typer(interaction_app, name="interaction")
app.add_typer(pprl_app, name="pprl")
pprl_app.add_typer(pprl_distribution_app, name="distribution")
pprl_app.add_typer(pprl_program_app, name="program")
pprl_app.add_typer(pprl_amber_app, name="amber")
pprl_app.add_typer(pprl_resource_app, name="resource")
pprl_app.add_typer(pprl_execution_app, name="execution")
pprl_app.add_typer(pprl_rollout_app, name="rollout")
pprl_app.add_typer(pprl_outcome_app, name="outcome")
pprl_app.add_typer(pprl_eligibility_app, name="eligibility")


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


@pprl_distribution_app.command("register")
def pprl_distribution_register(
    ctx: typer.Context,
    manifest_file: Path = typer.Option(
        ..., "--manifest-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            manifest = ProcessDistributionManifest.model_validate_json(
                manifest_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.distributions.register_distribution(session, manifest)
            return {
                "distribution_id": manifest.distribution_id,
                "version": manifest.version,
                "distribution_digest": digest,
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl distribution register", operation)


@pprl_program_app.command("register")
def pprl_program_register(
    ctx: typer.Context,
    program_file: Path = typer.Option(
        ..., "--program-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            program = ProcessProgram.model_validate_json(
                program_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.distributions.register_program(session, program)
            return {
                "program_id": program.program_id,
                "version": program.version,
                "program_digest": digest,
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl program register", operation)


@pprl_amber_app.command("prepare")
def pprl_amber_prepare(
    ctx: typer.Context,
    envelope_file: Path = typer.Option(
        ..., "--envelope-file", exists=True, file_okay=True, dir_okay=False
    ),
    actor_id: str = typer.Option(..., "--actor-id"),
    evidence_ref: list[str] | None = typer.Option(None, "--evidence-ref"),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            envelope = AmberAuthorizationEnvelope.model_validate_json(
                envelope_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.amber.prepare(
                    session,
                    envelope=envelope,
                    actor_id=actor_id,
                    evidence_refs=tuple(sorted(set(evidence_ref or ()))),
                )
            return {
                "authorization_id": envelope.authorization_id,
                "authorization_digest": digest,
                "status": AmberStatus.PREPARED,
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl amber prepare", operation)


@pprl_amber_app.command("transition")
def pprl_amber_transition(
    ctx: typer.Context,
    authorization_digest: str,
    to_status: AmberStatus = typer.Option(..., "--to-status"),
    actor_id: str = typer.Option(..., "--actor-id"),
    reason: str = typer.Option(..., "--reason"),
    evidence_ref: list[str] | None = typer.Option(None, "--evidence-ref"),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                event = await process_app.amber.transition(
                    session,
                    authorization_digest=authorization_digest,
                    to_status=to_status,
                    actor_id=actor_id,
                    reason=reason,
                    evidence_refs=tuple(sorted(set(evidence_ref or ()))),
                )
            return event.model_dump(mode="json")
        finally:
            await process_app.close()

    _run_command(ctx, "pprl amber transition", operation)


@pprl_amber_app.command("inspect")
def pprl_amber_inspect(ctx: typer.Context, authorization_digest: str) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                envelope = await process_app.amber.get(
                    session, authorization_digest=authorization_digest
                )
                status = await process_app.amber.status(
                    session, authorization_digest=authorization_digest
                )
                history = await process_app.amber.history(
                    session, authorization_digest=authorization_digest
                )
            return {
                "authorization_digest": authorization_digest,
                "status": status,
                "envelope": envelope.model_dump(mode="json"),
                "history": [event.model_dump(mode="json") for event in history],
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl amber inspect", operation)


@pprl_resource_app.command("fund")
def pprl_resource_fund(
    ctx: typer.Context,
    grant_file: Path = typer.Option(
        ..., "--grant-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            grant = ProcessResourceGrant.model_validate_json(grant_file.read_bytes())
            async with process_app.database.transaction() as session:
                await process_app.amber.resources.fund(session, grant)
            return {
                "grant_digest": grant.digest,
                "authorization_digest": grant.authorization_digest,
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl resource fund", operation)


@pprl_resource_app.command("inspect")
def pprl_resource_inspect(ctx: typer.Context, authorization_digest: str) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                grant = await process_app.amber.resources.grant(session, authorization_digest)
                journal = await process_app.amber.resources.replay(session, authorization_digest)
            return {
                "grant": grant.model_dump(mode="json"),
                "journal": [event.model_dump(mode="json") for event in journal],
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl resource inspect", operation)


@pprl_resource_app.command("release-unstarted")
def pprl_resource_release(
    ctx: typer.Context,
    decision_id: str,
    reviewer_id: str = typer.Option(..., "--reviewer-id"),
    evidence: str = typer.Option(..., "--evidence"),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                event = await process_app.amber.resources.release_unstarted(
                    session,
                    decision_id=decision_id,
                    reviewer_id=reviewer_id,
                    evidence=evidence,
                    now=datetime.now(UTC),
                )
            return event.model_dump(mode="json")
        finally:
            await process_app.close()

    _run_command(ctx, "pprl resource release-unstarted", operation)


@pprl_resource_app.command("reconcile-model")
def pprl_resource_reconcile(ctx: typer.Context, invocation_id: str) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            broker = ProcessGenerationResources(
                store=process_app.amber.resources, catalog=process_app.catalog
            )
            async with process_app.database.transaction() as session:
                event = await broker.reconcile(
                    session, invocation_id=invocation_id, now=datetime.now(UTC)
                )
            return event.model_dump(mode="json")
        finally:
            await process_app.close()

    _run_command(ctx, "pprl resource reconcile-model", operation)


@pprl_execution_app.command("register")
def pprl_execution_register(
    ctx: typer.Context,
    execution_file: Path = typer.Option(
        ..., "--execution-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            execution = ProcessExecutionManifest.model_validate_json(
                execution_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.processes.register_execution(session, execution)
            return {"execution_id": execution.execution_id, "execution_digest": digest}
        finally:
            await process_app.close()

    _run_command(ctx, "pprl execution register", operation)


@pprl_rollout_app.command("create")
def pprl_rollout_create(
    ctx: typer.Context,
    execution_digest: str = typer.Option(..., "--execution-digest"),
    initial_state_file: Path = typer.Option(
        ..., "--initial-state-file", exists=True, file_okay=True, dir_okay=False
    ),
    replication_index: int = typer.Option(..., "--replication-index", min=0),
    rollout_id: str | None = typer.Option(None, "--rollout-id"),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            initial_state = ProjectStatePayload.model_validate_json(
                initial_state_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                rollout = await process_app.processes.create_rollout(
                    session,
                    execution_digest=execution_digest,
                    replication_index=replication_index,
                    initial_state=initial_state,
                    rollout_id=rollout_id,
                )
            return rollout.model_dump(mode="json")
        finally:
            await process_app.close()

    _run_command(ctx, "pprl rollout create", operation)


@pprl_rollout_app.command("inspect")
def pprl_rollout_inspect(ctx: typer.Context, rollout_id: str) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                rollout = await process_app.processes.get_rollout(session, rollout_id=rollout_id)
                state = await process_app.processes.get_state(
                    session, state_id=rollout.current_state_id
                )
                row = await session.get(ProcessRolloutRow, rollout_id)
                if row is None:
                    raise KeyError(rollout_id)
                amber_status = await process_app.amber.status(
                    session, authorization_digest=row.authorization_digest
                )
                outcomes = (
                    await session.scalars(
                        select(ProcessOutcomeRow)
                        .where(ProcessOutcomeRow.rollout_id == rollout_id)
                        .order_by(ProcessOutcomeRow.created_at, ProcessOutcomeRow.assessment_id)
                    )
                ).all()
                eligibility = (
                    await session.scalars(
                        select(ProcessTrainingEligibilityRow)
                        .where(ProcessTrainingEligibilityRow.rollout_id == rollout_id)
                        .order_by(
                            ProcessTrainingEligibilityRow.created_at,
                            ProcessTrainingEligibilityRow.decision_id,
                        )
                    )
                ).all()
            return {
                "rollout": rollout.model_dump(mode="json"),
                "current_state": state.model_dump(mode="json"),
                "amber_status": amber_status,
                "outcomes": [item.record_json for item in outcomes],
                "training_eligibility": [item.record_json for item in eligibility],
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl rollout inspect", operation)


@pprl_rollout_app.command("replay")
def pprl_rollout_replay(ctx: typer.Context, rollout_id: str) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            async with process_app.database.transaction() as session:
                initial_state, events = await process_app.processes.replay(
                    session, rollout_id=rollout_id
                )
                steps = []
                for event in events:
                    state = await process_app.processes.get_state(
                        session, state_id=event.resulting_state_id
                    )
                    steps.append(
                        {
                            "event": event.model_dump(mode="json"),
                            "resulting_state": state.model_dump(mode="json"),
                        }
                    )
            return {
                "rollout_id": rollout_id,
                "initial_state": initial_state.model_dump(mode="json"),
                "steps": steps,
                "event_count": len(events),
            }
        finally:
            await process_app.close()

    _run_command(ctx, "pprl rollout replay", operation)


@pprl_outcome_app.command("record")
def pprl_outcome_record(
    ctx: typer.Context,
    assessment_file: Path = typer.Option(
        ..., "--assessment-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            assessment = ProcessOutcomeAssessment.model_validate_json(
                assessment_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.processes.record_outcome(session, assessment)
            return {"assessment_id": assessment.assessment_id, "assessment_digest": digest}
        finally:
            await process_app.close()

    _run_command(ctx, "pprl outcome record", operation)


@pprl_eligibility_app.command("record")
def pprl_eligibility_record(
    ctx: typer.Context,
    decision_file: Path = typer.Option(
        ..., "--decision-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    async def operation() -> dict[str, Any]:
        process_app = build_pprl_application(_settings(ctx))
        try:
            decision = ProcessTrainingEligibilityDecision.model_validate_json(
                decision_file.read_text(encoding="utf-8"), strict=False
            )
            async with process_app.database.transaction() as session:
                digest = await process_app.processes.record_training_eligibility(session, decision)
            return {"decision_id": decision.decision_id, "decision_digest": digest}
        finally:
            await process_app.close()

    _run_command(ctx, "pprl eligibility record", operation)


@atlas_app.command("plan")
def atlas_plan(ctx: typer.Context) -> None:
    """Render the first campaign without registering or executing external work."""

    def operation() -> dict[str, Any]:
        report = build_first_inkling_machine_report()
        live = cast(dict[str, Any], report["live_campaign_plan"])
        return {
            "campaign": report["campaign"],
            "offline_verification": report["offline_verification"],
            "evidence_lanes": report["evidence_lanes"],
            "live_campaign_plan": live,
            "report_id": report["report_id"],
            "report_digest": report["report_digest"],
        }

    _run_command(ctx, "atlas plan", operation)


@atlas_app.command("verify-offline")
def atlas_verify_offline(ctx: typer.Context) -> None:
    """Regenerate all deterministic campaign assets and fail on any local check."""

    def operation() -> dict[str, Any]:
        bundle = build_first_inkling_campaign_bundle()
        failed = tuple(name for name, passed in bundle.verification.checks.items() if not passed)
        if failed:
            raise ValueError("Capability Atlas offline checks failed: " + ", ".join(failed))
        return {
            **bundle.verification.model_dump(mode="json"),
            "source_claim_count": len(bundle.claims),
            "suite_count": len(bundle.suites),
            "condition_count": len(bundle.campaign.conditions),
            "locally_reproduced_observations": 0,
            "promotion_eligible": False,
        }

    _run_command(ctx, "atlas verify-offline", operation)


@atlas_app.command("report")
def atlas_report(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None,
        "--output",
        dir_okay=False,
        writable=True,
        resolve_path=True,
        help="Optional Markdown destination; omitted output is returned in the command result.",
    ),
) -> None:
    """Render the human campaign report from the same immutable machine bundle."""

    def operation() -> dict[str, Any]:
        markdown = render_first_inkling_report()
        report = build_first_inkling_machine_report()
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        return {
            "report_id": report["report_id"],
            "report_digest": report["report_digest"],
            "output": str(output) if output is not None else None,
            "markdown": markdown if output is None else None,
        }

    _run_command(ctx, "atlas report", operation)


@atlas_campaign_app.command("prepare")
def atlas_campaign_prepare(
    ctx: typer.Context,
    campaign_digest: str = typer.Option(..., "--campaign-digest"),
    allocation_set: str = typer.Option(..., "--allocation-set"),
    authorization_ref: str = typer.Option(..., "--authorization-ref"),
    max_requests: int = typer.Option(..., "--max-requests", min=1),
    max_input_tokens: int = typer.Option(..., "--max-input-tokens", min=1),
    max_output_tokens: int = typer.Option(..., "--max-output-tokens", min=1),
    max_actions: int = typer.Option(..., "--max-actions", min=1),
    max_cost_usd: float = typer.Option(..., "--max-cost-usd", min=0.0),
    max_runtime_minutes: int = typer.Option(..., "--max-runtime-minutes", min=1),
    preparation_only: bool = typer.Option(
        False,
        "--preparation-only",
        help="Acknowledge that this command emits a plan and cannot execute model work.",
    ),
) -> None:
    """Validate a frozen stage and emit a non-executable activation envelope."""

    def operation() -> object:
        return prepare_first_inkling_campaign_stage(
            campaign_digest=campaign_digest,
            allocation_set=allocation_set,
            authorization_ref=authorization_ref,
            max_requests=max_requests,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_actions=max_actions,
            max_cost_usd=max_cost_usd,
            max_runtime_minutes=max_runtime_minutes,
            preparation_only=preparation_only,
        )

    _run_preparation_only_command(ctx, operation)


@atlas_campaign_app.command("run", hidden=True)
def atlas_campaign_run(ctx: typer.Context) -> None:
    """Fail closed: no governed Atlas execution gateway exists yet."""

    _emit(
        ctx,
        {
            "error": {
                "type": "AtlasExecutionGatewayUnavailable",
                "message": (
                    "Atlas campaign execution is unavailable and fails closed. "
                    "Use 'atlas campaign prepare' to validate a preparation-only envelope."
                ),
            },
            "execution_permitted": False,
            "external_requests_made": 0,
            "external_cost_usd": 0.0,
            "gpu_actions": 0,
        },
        error=True,
    )
    raise typer.Exit(code=1)


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


@interaction_app.command("serve")
def interaction_serve(ctx: typer.Context) -> None:
    """Serve the loopback-only Interaction Lab without starting model infrastructure."""

    settings = _settings(ctx)
    if settings.interaction_access_token is None:
        raise typer.BadParameter(
            "PADAWAN_INTERACTION_ACCESS_TOKEN is required",
            param_hint="PADAWAN_INTERACTION_ACCESS_TOKEN",
        )
    from padawan.interaction.composition import build_interaction_application
    from padawan.interaction.web import create_interaction_web_app

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Interaction Lab requires the installed web dependencies") from exc
    application = build_interaction_application(settings)
    web = create_interaction_web_app(
        application,
        access_token=settings.interaction_access_token.get_secret_value(),
    )
    typer.echo(
        f"Padawan Interaction Lab: http://{settings.interaction_host}:{settings.interaction_port}"
    )
    typer.echo("No endpoint deployment or GPU wake action is performed by this command.")
    uvicorn.run(
        web,
        host=settings.interaction_host,
        port=settings.interaction_port,
        access_log=True,
    )


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
        result_failure=_terminal_run_failure,
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
    allow_legacy_student_fallback: bool = typer.Option(False, "--allow-legacy-student-fallback"),
) -> None:
    async def operation() -> dict[str, Any]:
        application = await build_live_application(
            _settings(ctx),
            student_provider=student_provider,
            teacher_provider=teacher_provider,
            student_model=student_model,
            teacher_model=teacher_model,
            compatible_base_url=compatible_base_url,
            allow_legacy_student_fallback=allow_legacy_student_fallback,
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
        result_failure=_terminal_run_failure,
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
        selected_teacher_model = teacher_model or (
            settings.openai_model if teacher_provider == "openai" else settings.anthropic_model
        )
        if selected_teacher_model is None:
            raise ValueError("teacher model cannot be resolved")
        competencies = application.domain.competencies()
        competency_ids = tuple(item.competency_id for item in competencies)
        async with application.database.transaction() as session:
            student = await session.get(StudentRow, student_id)
            if student is None:
                await application.states.create_student(
                    session,
                    student_id=student_id,
                    checkpoint_id=application.student_checkpoint_id,
                    runtime_id=application.student_runtime_id,
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
            elif student.canonical_state_id is None:
                raise ValueError(f"research identity {student_id} has no canonical state")
            else:
                state = await application.states.get(
                    session,
                    state_id=student.canonical_state_id,
                )
                if (
                    state.checkpoint_id != application.student_checkpoint_id
                    or state.runtime_id != application.student_runtime_id
                ):
                    raise ValueError(
                        f"research identity {student_id} is bound to a different runtime or "
                        "checkpoint; use a new student identity"
                    )
            if bootstrap:
                for competency in competencies:
                    await application.registry.register_competency(session, competency)
                items = application.domain.generate_curriculum(
                    pool=CorpusPool.CURRICULUM,
                    seed=experiment_seed,
                    groups_per_family=max(1, episode_budget),
                    siblings_per_group=3,
                )
                await application.registry.register_items(session, list(items))
            inventory = (
                await session.scalars(
                    select(CorpusItemRow)
                    .where(
                        CorpusItemRow.pool == CorpusPool.CURRICULUM.value,
                        CorpusItemRow.competency_id.in_(competency_ids),
                    )
                    .order_by(CorpusItemRow.item_id)
                )
            ).all()
            corpus_digest = research_corpus_digest(list(inventory))
        research_controls = ResearchControlRegistry()
        control_configuration = build_standardized_developmental_control(
            settings=settings,
            domain=application.domain.spec,
            student_provider=student_provider,
            student_model_id=selected_model,
            student_runtime_id=application.student_runtime_id,
            student_runtime_version=application.student_runtime_version,
            student_checkpoint_id=application.student_checkpoint_id,
            student_role=application.student_role,
            student_base_url=(
                settings.inkling_base_url
                if student_provider == "inkling"
                else settings.openai_base_url
                if student_provider == "openai"
                else compatible_base_url or settings.compatible_base_url
            ),
            teacher_provider=teacher_provider,
            teacher_model_id=selected_teacher_model,
            corpus_digest=corpus_digest,
            allow_legacy_student_fallback=allow_legacy_student_fallback,
            worker_configuration=application.research_worker,
        )
        loop = AutonomousResearchLoop(
            database=application.database,
            runs=application.runs,
            supervisor=application.supervisor,
            student_id=student_id,
            research_role=application.student_role,
            domain_id=application.domain.spec.domain_id,
            retry_budget=settings.run_retry_budget,
            research_controls=research_controls,
            control_configuration=control_configuration,
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
    *,
    result_failure: Callable[[dict[str, Any]], dict[str, str] | None] | None = None,
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
        semantic_error = result_failure(result_data) if result_failure is not None else None
        manifest = CommandManifest(
            command=command_name,
            status="failed" if semantic_error is not None else "complete",
            started_at=started,
            completed_at=now(),
            configuration=_manifest_configuration(ctx, settings),
            result=result_data,
            error=semantic_error,
        )
        reference = writer.write(manifest, known_secrets=_known_secrets(settings))
        payload = {**result_data, "manifest": reference.model_dump(mode="json")}
        if semantic_error is not None:
            _emit(ctx, {**payload, "error": semantic_error}, error=True)
            raise typer.Exit(code=1)
        _emit(ctx, payload)
    except typer.Exit:
        raise
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


def _run_preparation_only_command(
    ctx: typer.Context,
    operation: Callable[[], object],
) -> None:
    """Run a synchronous pure builder without DB, artifact backend, network, or provider I/O."""

    try:
        result = operation()
        _emit(ctx, cast(dict[str, Any], _jsonable(result)))
    except typer.Exit:
        raise
    except Exception as exc:
        _emit(
            ctx,
            {"error": {"type": type(exc).__name__, "message": str(exc)}},
            error=True,
        )
        raise typer.Exit(code=1) from exc


def _terminal_run_failure(result: dict[str, Any]) -> dict[str, str] | None:
    runs = result.get("runs")
    if not isinstance(runs, list):
        return None
    terminal = [
        run for run in runs if isinstance(run, dict) and run.get("state") == "FAILED_TERMINAL"
    ]
    if not terminal:
        return None
    run_ids = ", ".join(
        str(run.get("run_id", "unknown")) for run in terminal if isinstance(run, dict)
    )
    return {
        "type": "TerminalRunFailure",
        "message": (f"{len(terminal)} research run(s) ended in FAILED_TERMINAL: {run_ids}"),
    }


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
    for parameter in tuple(invocation):
        if parameter.endswith("_file") or parameter == "rights_manifest":
            invocation[parameter] = {"selected": invocation[parameter] is not None}
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
