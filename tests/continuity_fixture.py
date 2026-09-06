"""Privileged finite continuity fixture composition. Never a production launcher."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ProcessResourceGrantRow,
    ProcessRolloutRow,
    ProcessTaskPlanRow,
    ProcessWorkerRegistrationRow,
    ProcessWorkerRequestRow,
)
from padawan.pprl.abandonment import ProcessAbandonmentStore
from padawan.pprl.abandonment_contracts import ProcessAbandonmentRequest
from padawan.pprl.container_contracts import ProcessContainerProfile
from padawan.pprl.containers import ProcessContainerExecutor, ProcessContainerStore
from padawan.pprl.contracts import (
    HypothesisStatus,
    ProcessEventKind,
    ProjectHypothesis,
    ProjectSplit,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.distributions import GeneratedProject, ProcessDistributionRegistry
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.recovery_contracts import ProcessRecoveryRequest
from padawan.pprl.store import ProcessStore
from padawan.pprl.tasks import ProcessTaskStore
from padawan.pprl.worker_broker import ProcessWorkerBroker
from padawan.pprl.worker_contracts import ProcessWorkerScope
from padawan.training.pprl import compile_pprl_snapshot
from tests.container_helpers import SyntheticContainerDriver, container_profile
from tests.pprl_helpers import (
    component,
    distribution,
    envelope,
    environment_parameters,
    execution,
    fund_resources,
    program,
    worker_model,
)
from tests.pprl_task_helpers import task_plan

CANDIDATES = (2, 3, 5, 7, 11, 13)
TARGET = 221
CANARY = "FIXTURE_FORENSIC_DO_NOT_HYDRATE_74819"
AUDIENCE = "scripted-continuity-broker"
WORKER_SOURCE = Path(__file__).with_name("continuity_worker.py")
FIXTURE_SOURCES = (
    WORKER_SOURCE,
    Path(__file__),
    Path(__file__).with_name("continuity_broker.py"),
    Path(__file__).with_name("continuity_supervisor.py"),
    Path(__file__).parent / "integration/test_process_continuity.py",
)


class FactorGenerator:
    generator_id = "scripted.factor-fixture"
    generator_version = "1.0.0"
    generator_digest = sha256_digest({"target": TARGET, "candidates": CANDIDATES})

    def generate(self, *, manifest, split, seed):
        return GeneratedProject(
            task={"target": TARGET, "ordered_candidates": list(CANDIDATES)},
            difficulty=0.0,
            difficulty_stratum="standard",
            environment_fingerprint=sha256_digest(environment_parameters()),
            metadata={"evidence_kind": "deterministic_engineering_fixture"},
        )


def initial_state():
    return ProjectStatePayload(
        objective="Find a proper factor of 221 using the declared ordered candidates",
        plan=(
            "Test the next open candidate; retain rejections; finish after a supported candidate",
        ),
        hypotheses=tuple(
            ProjectHypothesis(
                hypothesis_id=f"candidate-{value:02}",
                statement=str(value),
                status=HypothesisStatus.OPEN,
            )
            for value in CANDIDATES
        ),
    )


class Institution:
    def __init__(self, database, root):
        self.database, self.root = database, root
        self.catalog = ArtifactCatalog(LocalArtifactStore(root / "artifacts"))
        self.amber = AmberStore()
        self.containers = ProcessContainerStore(self.catalog, self.amber.resources)
        self.process = ProcessStore(self.amber, container_evidence=self.containers)
        self.broker = ProcessWorkerBroker(
            database=database, store=self.process, broker_audience=AUDIENCE
        )
        self.recovery = ProcessRecoveryStore(self.process, self.catalog)
        self.access = {}  # only this native broker incarnation; never serialized
        self.manifest = None

    async def initialize(self):
        now = datetime.now(UTC)
        registry = ProcessDistributionRegistry()
        generator = FactorGenerator()
        parameters = {"fixture": "no-inference", "python_version": sys.version}
        model = worker_model().model_copy(
            update={
                "model_id": "scripted-continuity-no-model",
                "checkpoint": component("scripted-worker", digest_source=WORKER_SOURCE.read_text()),
                "quantization": component("scripted-not-applicable"),
                "runtime": component("python-fixture", digest_source=sys.version),
                "serving_artifact": component("scripted-no-serving-endpoint"),
                "protocol": "padawan-scripted-worker-v1",
                "runtime_parameters": parameters,
                "runtime_parameters_digest": sha256_digest(parameters),
            }
        )
        profile = container_profile(now, worker_model_digest=sha256_digest(model))
        async with self.database.transaction() as session:
            dm = distribution().model_copy(
                update={
                    "distribution_id": "scripted.continuity-fixture",
                    "title": "Deterministic engineering fixture, not a research distribution",
                    "generator": component(generator.generator_id).model_copy(
                        update={"digest": generator.generator_digest}
                    ),
                }
            )
            dd = await registry.register_distribution(session, dm)
            pp = program(dd)
            pp = pp.model_copy(
                update={
                    "worker_roles": (
                        pp.worker_roles[0].model_copy(update={"maximum_instances": 2}),
                    ),
                    "maximum_concurrent_workers": 2,
                }
            )
            pd = await registry.register_program(session, pp)
            instance = await registry.sample(
                session,
                distribution_digest=dd,
                split=ProjectSplit.TRAIN,
                seed=0,
                generator=generator,
            )
            authority = envelope(program_digest=pd, distribution_digest=dd, model=model)
            authority = authority.model_copy(
                update={
                    "created_at": now - timedelta(minutes=9),
                    "expires_at": now + timedelta(hours=1),
                    "checkpoint_policy": authority.checkpoint_policy.model_copy(
                        update={"training_permitted": False}
                    ),
                    "budgets": authority.budgets.model_copy(
                        update={
                            "actions": 12,
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "cost": 0.0,
                            "concurrent_workers": 2,
                        }
                    ),
                    "environment": authority.environment.model_copy(
                        update={
                            "sandbox_id": profile.profile_id,
                            "sandbox_version": profile.version,
                            "sandbox_digest": profile.digest,
                            "filesystem_scopes": ("/work",),
                        }
                    ),
                }
            )
            await self.amber.prepare(session, envelope=authority, actor_id="preparer")
            for status, minutes in ((AmberStatus.AUTHORIZED, 8), (AmberStatus.ACTIVE, 7)):
                await self.amber.transition(
                    session,
                    authorization_digest=authority.digest,
                    to_status=status,
                    actor_id="reviewer-a",
                    reason="bounded deterministic engineering fixture",
                    evidence_refs=("review:scripted-continuity",),
                    occurred_at=now - timedelta(minutes=minutes),
                )
            grant = await fund_resources(session, self.amber, authority)
            manifest = execution(
                program_digest=pd,
                distribution_digest=dd,
                instance=instance,
                authorization_digest=authority.digest,
                model=model,
                seed=0,
            )
            await self.process.register_execution(session, manifest)
            await self.process.workers.enroll(
                session,
                ProcessWorkerScope(
                    execution_digest=sha256_digest(manifest),
                    authorization_digest=authority.digest,
                    broker_audience=AUDIENCE,
                    maximum_credential_seconds=1800,
                    maximum_registered_workers=2,
                    maximum_request_records=16,
                    maximum_request_history_bytes=131_072,
                    reviewed_by="reviewer-a",
                    review_evidence="two trusted native scripted workers; no OS isolation claim",
                    created_at=now,
                ),
            )
            plan = await task_plan(session, self.amber, manifest, initial_state(), now)
            await ProcessTaskStore().enroll(session, plan, now=now)
            rollout = await self.process.create_rollout(
                session,
                execution_digest=sha256_digest(manifest),
                replication_index=0,
                initial_state=initial_state(),
                rollout_id=plan.tasks[0].rollout_id,
                created_at=now,
            )
            canary = self.catalog.backend.put_text(CANARY, restricted=True, raw_data=True)
            await ArtifactInformationStore(self.catalog).classify(
                session,
                artifact=canary,
                information_class=InformationClass.FORENSIC,
                classified_by="reviewer-a",
                reason="interface non-disclosure canary, not a secret",
                classified_at=now,
            )
            await self.catalog.reference(
                session, canary, owner_type="scripted_fixture", owner_id="canary"
            )
        self.manifest = {
            "fixture": "scripted-continuity-v1",
            "rollout_id": rollout.rollout_id,
            "execution_digest": sha256_digest(manifest),
            "authorization_digest": authority.digest,
            "task_plan_digest": plan.digest,
            "grant_digest": grant.digest,
            "worker_model_digest": sha256_digest(model),
            "worker_script_digest": sha256_digest(WORKER_SOURCE.read_bytes()),
            "fixture_source_digests": {
                path.name: sha256_digest(path.read_bytes()) for path in FIXTURE_SOURCES
            },
            "python_version": sys.version,
            "python_executable_digest": sha256_digest(Path(sys.executable).read_bytes()),
            "profile": profile.model_dump(mode="json"),
            "canary": canary.model_dump(mode="json"),
        }
        (self.root / "fixture.json").write_bytes(canonical_json_bytes(self.manifest))

    def reopen(self):
        self.manifest = json.loads((self.root / "fixture.json").read_bytes())
        if (
            self.manifest["fixture"] != "scripted-continuity-v1"
            or self.manifest["worker_script_digest"] != sha256_digest(WORKER_SOURCE.read_bytes())
            or self.manifest["python_executable_digest"]
            != sha256_digest(Path(sys.executable).read_bytes())
            or self.manifest["fixture_source_digests"]
            != {path.name: sha256_digest(path.read_bytes()) for path in FIXTURE_SOURCES}
        ):
            raise ValueError("fixture runtime identity changed")

    async def issue_roster(self):
        now = datetime.now(UTC)
        result = []
        async with self.database.transaction() as session:
            for _ in range(2):
                worker, access = await self.process.workers.issue(
                    session,
                    execution_digest=self.manifest["execution_digest"],
                    role_id="researcher",
                    worker_model_digest=self.manifest["worker_model_digest"],
                    declared_capabilities=("reasoning",),
                    issued_by="reviewer-a",
                    evidence="new trusted native scripted process; no inherited local state",
                    expires_at=now + timedelta(minutes=20),
                    now=now,
                )
                self.access[worker.worker_id] = access
                result.append(
                    {
                        "worker_id": worker.worker_id,
                        "broker_audience": AUDIENCE,
                        "credential": access.credential.get_secret_value(),
                    }
                )
        return result

    async def snapshot(self):
        async with self.database.transaction() as session:
            rollout = await self.process.get_rollout(
                session, rollout_id=self.manifest["rollout_id"]
            )
            state = await self.process.get_state(session, state_id=rollout.current_state_id)
            initial, events = await self.process.replay(session, rollout_id=rollout.rollout_id)
            states = [
                await self.process.get_state(session, state_id=event.resulting_state_id)
                for event in events
            ]
            row = await session.get(ProcessRolloutRow, rollout.rollout_id)
            plan = await ProcessTaskStore().check_rollout(session, row)
            account = await self.amber.resources.inspect(
                session, self.manifest["authorization_digest"]
            )
            return {
                "rollout": rollout.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "initial": initial.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
                "states": [item.model_dump(mode="json") for item in states],
                "account": account.model_dump(mode="json"),
                "account_digest": account.digest,
                "task_plan_digest": plan.digest,
            }

    async def action(self, session, worker_id, request_id):
        receipt, _ = await self.broker.inspect_request(
            session, worker_id=worker_id, request_id=request_id
        )
        assignment = await self.process.workers.assignment(session, receipt.assignment_id)
        row = await session.get(ProcessRolloutRow, assignment.rollout_id)
        state = await self.process.get_state(session, state_id=assignment.state_id)
        decision = await session.get(AmberAdmissionDecisionRow, receipt.decision_id)
        action = AmberActionRequest.model_validate(decision.request_json, strict=False)
        access = self.access[worker_id].assigned(assignment.assignment_id)
        return row, state, action, access, receipt

    async def commit(self, command, checkpoint):
        async with self.database.transaction() as session:
            row, state, action, access, receipt = await self.action(
                session, command["worker_id"], command["request_id"]
            )
            payload = state.payload
            supported = any(h.status == HypothesisStatus.SUPPORTED for h in payload.hypotheses)
            expected = (
                ProcessEventKind.PROJECT_COMPLETED
                if supported
                else ProcessEventKind.HYPOTHESIS_UPDATED
            )
            if action.event_kind != expected or action.tool_id is not None:
                raise ValueError("scripted action mask violation")
            changes = {"budget_usage": action.projected_usage}
            event_payload = {"summary": "fixed public mathematical fixture completed"}
            if not supported:
                candidate = next(h for h in payload.hypotheses if h.status == HypothesisStatus.OPEN)
                value = int(candidate.statement)
                updated = candidate.model_copy(
                    update={
                        "status": HypothesisStatus.REJECTED
                        if TARGET % value
                        else HypothesisStatus.SUPPORTED
                    }
                )
                changes["hypotheses"] = tuple(
                    updated if h.hypothesis_id == candidate.hypothesis_id else h
                    for h in payload.hypotheses
                )
                event_payload = {"summary": f"tested candidate: {value}"}
            await self.process.append_event(
                session,
                rollout_id=row.rollout_id,
                lease_token=row.lease_token,
                amber_decision_id=receipt.decision_id,
                kind=expected,
                actor_id=access.worker_id,
                payload=event_payload,
                resulting_state=payload.model_copy(update=changes),
                worker_access=access,
                to_status=RolloutStatus.COMPLETE if supported else RolloutStatus.ACTIVE,
            )
            if command.get("crash") == "before_commit":
                await checkpoint("before_commit")
        if command.get("crash") == "after_commit":
            await checkpoint("after_commit")
        return await self.snapshot()

    async def recover_roster(self):
        now = datetime.now(UTC)
        async with self.database.transaction() as session:
            workers = list(await session.scalars(select(ProcessWorkerRegistrationRow)))
            for row in workers:
                _, head = await self.process.workers.registration(session, row.worker_id)
                if head.status == "active":
                    await self.process.workers.revoke(
                        session,
                        worker_id=row.worker_id,
                        revoked_by="reviewer-a",
                        evidence="controller reaped all owned processes before replacement",
                        now=now,
                    )
            row = await session.get(ProcessRolloutRow, self.manifest["rollout_id"])
            if row.status in {"complete", "cancelled"}:
                return None
            state = await self.process.get_state(session, state_id=row.current_state_id)
            receipt = await self.recovery.recover(
                session,
                ProcessRecoveryRequest(
                    recovery_id="process-recovery-" + uuid4().hex,
                    rollout_id=row.rollout_id,
                    expected_state_digest=state.state_digest,
                    expected_lease_token_digest=sha256_digest(row.lease_token)
                    if row.lease_token
                    else None,
                    reviewer_id="reviewer-a",
                    reason="owned broker and entire roster exited; reconstruct institution",
                    resume=True,
                    reviewed_at=now,
                ),
                now=now,
            )
            return receipt.model_dump(mode="json")

    async def pause(self, paused):
        async with self.database.transaction() as session:
            event = await self.amber.transition(
                session,
                authorization_digest=self.manifest["authorization_digest"],
                to_status=AmberStatus.PAUSED if paused else AmberStatus.ACTIVE,
                actor_id="reviewer-a",
                reason="explicit finite fixture pause/resume",
                evidence_refs=("review:scripted-continuity",),
                occurred_at=datetime.now(UTC),
            )
            return event.model_dump(mode="json")

    async def effect(self, command):
        # These probes deliberately leave the task stopped. No tool bytes enter public state.
        async with self.database.transaction() as session:
            row, _, _, access, receipt = await self.action(
                session, command["worker_id"], command["request_id"]
            )
            kwargs = dict(
                decision_id=receipt.decision_id,
                rollout_id=row.rollout_id,
                lease_token=row.lease_token,
                worker_id=access.worker_id,
                worker_access=access,
            )
            if command["kind"] == "unknown":
                await self.amber.resources.start(
                    session,
                    decision_id=receipt.decision_id,
                    now=datetime.now(UTC),
                    worker_access=access,
                )
                return {"kind": "unknown"}
        profile = ProcessContainerProfile.model_validate(self.manifest["profile"], strict=False)
        service = ProcessContainerExecutor(
            database=self.database,
            observations=self.broker.observations,
            store=self.containers,
            driver=SyntheticContainerDriver(profile, lambda: datetime.now(UTC)),
        )
        result = await service.execute(**kwargs)
        return {"kind": "synthetic_completed", "result_digest": result.digest}

    async def abandon(self, recovery):
        now = datetime.now(UTC)
        # The receipt is re-read from the native store; control input supplies only its ID.
        async with self.database.transaction() as session:
            recovered = await self.recovery.read(session, recovery_id=recovery)
            account = await self.amber.resources.inspect(session, recovered.authorization_digest)
            history = await self.amber.history(
                session, authorization_digest=recovered.authorization_digest
            )
            receipt = await ProcessAbandonmentStore(self.recovery).abandon(
                session,
                ProcessAbandonmentRequest(
                    abandonment_id="process-abandonment-" + uuid4().hex,
                    rollout_id=recovered.request.rollout_id,
                    recovery_id=recovered.request.recovery_id,
                    recovery_digest=recovered.digest,
                    expected_state_digest=recovered.state_digest,
                    expected_authorization_sequence=history[-1].sequence,
                    expected_account_digest=account.digest,
                    reviewer_id="reviewer-a",
                    reason="terminal fixture interruption; no replacement task or domain outcome",
                    exclusion="unresolved_external_effect"
                    if any(e.disposition == "unknown" for e in recovered.effects)
                    else "infrastructure_interruption",
                    reviewed_at=now,
                    expires_at=now + timedelta(minutes=1),
                ),
                now=now,
            )
            return receipt.model_dump(mode="json")

    async def compile(self):
        async with self.database.transaction() as session:
            compiled = await compile_pprl_snapshot(session, as_of=datetime.now(UTC))
            return {
                "source_snapshot_digest": compiled.source_snapshot_digest,
                "rollout_ids": compiled.rollout_ids,
                "products": {name: len(rows) for name, rows in compiled.products.items()},
                "exclusions": [item.model_dump(mode="json") for item in compiled.exclusions],
            }

    async def audit(self):
        async with self.database.transaction() as session:
            requests = []
            for row in await session.scalars(select(ProcessWorkerRequestRow)):
                receipt, reply = await self.broker.inspect_request(
                    session, worker_id=row.worker_id, request_id=row.request_id
                )
                requests.append(
                    {
                        "worker_id": row.worker_id,
                        "request_id": row.request_id,
                        "receipt_digest": receipt.digest,
                        "reply_bytes": canonical_json_bytes(reply).decode(),
                    }
                )
            workers = {}
            for row in await session.scalars(select(ProcessWorkerRegistrationRow)):
                _, head = await self.process.workers.registration(session, row.worker_id)
                workers[row.worker_id] = head.status
            counts = {
                table.__tablename__: await session.scalar(select(func.count()).select_from(table))
                for table in (ProcessRolloutRow, ProcessTaskPlanRow, ProcessResourceGrantRow)
            }
            return {"requests": requests, "workers": workers, "counts": counts}
