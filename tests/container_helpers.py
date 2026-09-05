"""Synthetic contract fixtures; actual container enforcement has separate opt-in tests."""

import copy
from datetime import timedelta
from types import SimpleNamespace

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import project_authored_internal_rights
from padawan.models.hashing import sha256_digest
from padawan.pprl.container_contracts import ContainerRuntimeIdentity, ProcessContainerProfile
from padawan.pprl.container_driver import (
    ContainerRunEvidence,
    DockerContainerDriver,
    container_wire,
    supervisor_bytes,
)
from padawan.pprl.containers import ProcessContainerExecutor, ProcessContainerStore
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ProcessStore
from tests.pprl_helpers import (
    DeterministicProjectGenerator,
    component,
    distribution,
    envelope,
    execution,
    fund_resources,
    program,
    worker_model,
)


def container_profile(now, **updates):
    profile = ProcessContainerProfile(
        profile_id="test.cpu-boundary",
        version="1.0.0",
        role_id="researcher",
        worker_model_digest=sha256_digest(worker_model()),
        tool_id="calculator",
        tool_digest=component("calculator").digest,
        tool_operation="evaluate",
        docker_binary="/fixture/docker",
        docker_binary_digest=sha256_digest("fixture-client"),
        socket_uri="unix:///fixture/docker.sock",
        runtime=ContainerRuntimeIdentity(
            engine_id="test-engine",
            server_version="test-version",
            kernel_version="test-kernel",
            architecture="test-architecture",
            security_options=("name=cgroupns", "name=seccomp,profile=builtin"),
        ),
        image_id=sha256_digest("fixture-image"),
        image_architecture="arm64",
        python_executable="/usr/local/bin/python3",
        supervisor_digest=sha256_digest(supervisor_bytes()),
        argv=("/usr/local/bin/python3", "-I", "-c", "print('fixture')"),
        cpu_millicores=250,
        memory_bytes=134_217_728,
        pids_limit=32,
        tmpfs_bytes=8_388_608,
        maximum_wall_ms=5_000,
        maximum_input_bytes=65_536,
        maximum_output_bytes=65_536,
        reviewed_by="reviewer-a",
        reviewed_at=now - timedelta(minutes=10),
        command_rights=project_authored_internal_rights(reviewed_at=now - timedelta(minutes=10)),
    )
    return ProcessContainerProfile.model_validate({**profile.model_dump(), **updates})


def synthetic_inspection(profile, name):
    image = {
        "Id": profile.image_id,
        "Os": "linux",
        "Architecture": profile.image_architecture,
        "Config": {
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
            "Volumes": None,
            "Healthcheck": None,
        },
    }
    inspected = {
        "Id": sha256_digest(name)[7:],
        "Name": "/" + name,
        "Image": profile.image_id,
        "Mounts": [],
        "State": {"Status": "created", "Running": False, "ExitCode": 0},
        "Config": {
            "Image": profile.image_id,
            "User": "0:0",
            "WorkingDir": "/work",
            "Hostname": "padawan-action",
            "Tty": False,
            "OpenStdin": True,
            "StdinOnce": True,
            "Entrypoint": [profile.python_executable],
            "Cmd": [
                "-I",
                "-S",
                "-B",
                "-c",
                supervisor_bytes().decode(),
                str(profile.maximum_wire_bytes),
                str(profile.maximum_wall_ms),
            ],
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "TMPDIR=/work"],
            "Labels": {"padawan.pprl.container": name, "padawan.pprl.profile": profile.digest},
        },
        "HostConfig": {
            "NetworkMode": "none",
            "IpcMode": "none",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NanoCpus": profile.cpu_millicores * 1_000_000,
            "Memory": profile.memory_bytes,
            "MemorySwap": profile.memory_bytes,
            "PidsLimit": profile.pids_limit,
            "AutoRemove": False,
            "PublishAllPorts": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Init": False,
            "UsernsMode": "",
            "Dns": ["127.0.0.1"],
            "DnsSearch": ["."],
            "DnsOptions": ["ndots:0"],
            "Tmpfs": {"/work": f"rw,noexec,nosuid,nodev,mode=1777,size={profile.tmpfs_bytes}"},
            "CapAdd": ["CAP_SETGID", "CAP_SETUID"],
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges=true"],
            "LogConfig": {"Type": "none", "Config": {}},
            "Runtime": "runc",
            "Ulimits": [
                {"Name": "core", "Hard": 0, "Soft": 0},
                {"Name": "nofile", "Hard": 64, "Soft": 64},
            ],
        },
    }
    return image, inspected


class SyntheticContainerDriver(DockerContainerDriver):
    def __init__(self, profile, clock, *, after_start=None):
        super().__init__(profile)
        self.clock, self.after_start = clock, after_start
        self.calls = []

    async def run(self, *, name, public_input, deadline, authorize, before_start, audit):
        self.calls.append(public_input)
        await authorize()
        image, created = synthetic_inspection(self.profile, name)
        await audit(
            "runtime", {"runtime": self.profile.runtime.model_dump(mode="json"), "image": image}
        )
        await audit("created", created)
        await before_start()
        if self.after_start is not None:
            await self.after_start()
        await authorize()
        terminal = copy.deepcopy(created)
        terminal["State"]["Status"] = "exited"
        await audit("terminal", terminal)
        await audit(
            "cleanup", {"container_id": created["Id"], "removed_id": created["Id"], "remaining": ""}
        )
        return ContainerRunEvidence(
            container_name=name,
            container_id=created["Id"],
            profile_digest=self.profile.digest,
            wire_digest=sha256_digest(container_wire(self.profile, public_input, deadline)),
            started_at=self.clock(),
            finished_at=self.clock(),
            start_attempted=True,
            terminated=True,
            terminal_verified=True,
            removed=True,
            exit_code=0,
            reason="exited",
            stdout=b"raw privileged fixture result",
            snapshots=[created, terminal],
        )


async def container_context(database, tmp_path, clock, *, profile=None, driver=None):
    now = clock()
    profile = profile or container_profile(now)
    registry, amber = ProcessDistributionRegistry(), AmberStore()
    catalog = ArtifactCatalog(LocalArtifactStore(tmp_path / "container-artifacts"))
    records = ProcessContainerStore(catalog, amber.resources)
    process = ProcessStore(amber, container_evidence=records)
    observations = ProcessObservationStore(process)
    async with database.transaction() as session:
        dd = await registry.register_distribution(session, distribution())
        pp = program(dd)
        pd = await registry.register_program(session, pp)
        instance = await registry.sample(
            session,
            distribution_digest=dd,
            split=ProjectSplit.TRAIN,
            seed=12,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(program_digest=pd, distribution_digest=dd)
        authorization = authorization.model_copy(
            update={
                "created_at": now - timedelta(minutes=9),
                "expires_at": now + timedelta(days=1),
                "environment": authorization.environment.model_copy(
                    update={
                        "sandbox_id": profile.profile_id,
                        "sandbox_version": profile.version,
                        "sandbox_digest": profile.digest,
                        "filesystem_scopes": ("/work",),
                    }
                ),
            }
        )
        await amber.prepare(session, envelope=authorization, actor_id="preparer")
        for status, minutes in ((AmberStatus.AUTHORIZED, 8), (AmberStatus.ACTIVE, 7)):
            await amber.transition(
                session,
                authorization_digest=authorization.digest,
                to_status=status,
                actor_id="reviewer-a",
                reason="explicit fixture authorization",
                evidence_refs=("review:cpu-fixture",),
                occurred_at=now - timedelta(minutes=minutes),
            )
        await fund_resources(session, amber, authorization)
        manifest = execution(
            program_digest=pd,
            distribution_digest=dd,
            instance=instance,
            authorization_digest=authorization.digest,
            seed=instance.seed,
        )
        await process.register_execution(session, manifest)
        await process.create_rollout(
            session,
            execution_digest=sha256_digest(manifest),
            replication_index=0,
            initial_state=ProjectStatePayload(objective="execute a bounded scientific tool"),
            created_at=now - timedelta(minutes=5),
        )
        claim = await process.claim_next(
            session, worker_id="worker", lease_for=timedelta(minutes=5), now=clock()
        )
        observed = await observations.observe_claim(
            session,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            worker_id="worker",
            now=clock(),
        )
        action = AmberActionRequest(
            authorization_digest=authorization.digest,
            rollout_id=claim.rollout.rollout_id,
            rollout_sequence=claim.rollout.sequence,
            state_digest=claim.state.state_digest,
            lease_token_digest=sha256_digest(claim.lease_token),
            program_digest=pd,
            distribution_digest=dd,
            split=ProjectSplit.TRAIN,
            persistence_mode=pp.persistence_mode,
            event_kind=ProcessEventKind.TOOL_INVOKED,
            role_id=profile.role_id,
            worker_model_digest=profile.worker_model_digest,
            target_class="scientific_math",
            tool_id=profile.tool_id,
            tool_digest=profile.tool_digest,
            tool_operation=profile.tool_operation,
            environment_fingerprint=manifest.environment_fingerprint,
            projected_usage=ProjectBudgetUsage(
                actions=1, artifact_bytes=profile.maximum_retention_bytes, wall_time_seconds=60.0
            ),
            projected_artifact_bytes=profile.maximum_retention_bytes,
            requested_at=clock(),
        )
        decision = await amber.admit(session, request=action, active_workers=0)
        await observations.bind_decision(
            session,
            observation_id=observed.observation_id,
            decision_id=decision.decision_id,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            worker_id="worker",
            now=clock(),
        )
    driver = driver or SyntheticContainerDriver(profile, clock)
    service = ProcessContainerExecutor(
        database=database, observations=observations, store=records, driver=driver
    )
    return SimpleNamespace(
        database=database,
        amber=amber,
        process=process,
        records=records,
        observations=observations,
        profile=profile,
        authorization=authorization,
        clock=clock,
        claim=claim,
        action=action,
        decision=decision,
        observed=observed,
        driver=driver,
        service=service,
        kwargs=dict(
            decision_id=decision.decision_id,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            worker_id="worker",
        ),
    )


async def commit_container_action(ctx):
    async with ctx.database.transaction() as session:
        return await ctx.process.append_event(
            session,
            rollout_id=ctx.claim.rollout.rollout_id,
            lease_token=ctx.claim.lease_token,
            amber_decision_id=ctx.decision.decision_id,
            kind=ProcessEventKind.TOOL_INVOKED,
            actor_id="worker",
            payload={"summary": "bounded tool execution ended"},
            resulting_state=ctx.claim.state.payload.model_copy(
                update={"budget_usage": ctx.action.projected_usage}
            ),
            occurred_at=ctx.clock(),
        )
