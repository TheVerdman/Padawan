# Bound local container execution

Status: implemented stage-2 local CPU tool boundary, with offline and opt-in Docker fixture evidence
recorded in the execution ledger. The preceding resource-accounting checkpoint is `7f64f9b`.
No live model, research worker, cloud campaign or parameter update is authorized by this interface.

## Audit and boundary

`ProcessCoordinator.run` passes admitted actions to an injected `ProcessActionExecutor` in the
broker's Python process. `PPRLApplication.coordinator` accepts that executor without a separate
runtime boundary. `AmberEnvironmentBoundary` describes a sandbox, and observation/generation
receipts bind declared identities and exact inputs, but none creates a contained execution.
The worker ID remains a caller declaration; assignments remain declarative. The general program
still requires authenticated workers, independent capture and metering, executable recovery,
governed communication, institutional Atlas measurements and the full learning protocol.

This slice adds one explicit local Linux-container tool path, bound to an existing observation,
Amber tool admission, resource reservation and reviewed execution profile. The profile pins the
local Docker client, explicit Unix socket, observed daemon identity, cached image content identity,
trusted supervisor, exact command and limits. It never selects a remote context, pulls an image,
mounts host data or credentials, enables networking, launches a model or installs a live scheduler.
Unsupported environment declarations fail closed instead of being silently narrowed.

The trusted computing base is the operator/broker, its SQL and artifact storage, Docker client and
daemon, Linux kernel/VM and the small container supervisor. Tool code and stdin may be adversarial.
The worker process cannot access the Docker socket, broker database, artifact service, host files,
credentials, other containers or an external network through an interface installed by this runner.
Kernel escapes, malicious administrators, shared-hardware timing channels and an independent remote
attestation authority are outside this checkpoint. Observed Docker identity/configuration and
adversarial container tests must be labeled as such; they are not cryptographic attestation or
evidence that a Metal/CUDA model loaded particular weights.

## Execution invariants

- Before launch, reconstruct the exact current observation/decision, role/model/tool identity,
  environment fingerprint, sandbox profile and funding. Retain immutable workload intent and its
  independently owned input before any container creation. One decision cannot launch twice.
- The worker receives only canonical public observation bytes on stdin and the exact reviewed
  command. No lease, decision, invocation, researcher record, raw trace, credential or private
  artifact identifier is delivered in its environment, hostname or stdin.
- Use a fixed local Unix socket, an empty Docker configuration and a minimal CLI environment.
  Reject client/daemon/image drift. Do not accept caller-supplied Docker flags, environment additions,
  mounts, devices, ports, network modes, privileged mode or Docker context selection. Override
  Docker's inherited resolver with loopback-only DNS, no search domain and fixed options; a
  network-disabled container otherwise still receives host resolver configuration.
- Each action gets a fresh container with a read-only root, bounded private tmpfs, disabled network
  and IPC, private PID/cgroup namespaces, no host mounts/devices, bounded CPU/memory/PIDs, no core
  dumps, no daemon log storage, no restart policy and no-new-privileges. Reject implicit image
  volumes and inspect the created container before starting it.
- A pinned supervisor is PID 1. It retains only the container-local set-UID/set-GID capabilities
  needed to start tool code as UID/GID 65534 with no supplementary groups. The tool cannot signal
  or change the supervisor. The supervisor's deadline kills the container even if the broker dies;
  this must be tested with an actual disposable container, not inferred from a timer in Python.
- Mark resource dispatch before container start. Recheck current authority before start and poll
  it while running. Pause, revocation, lease/state drift, resource stop, timeout or output overflow
  triggers termination. A failed control/evidence/cleanup operation leaves uncertainty and held
  resources; it does not permit another launch or an automatic refund.
- Stdout, stderr, configuration, lifecycle and failure evidence are privileged raw records. Capture
  is bounded; truncation or missing bytes stays explicit. The runner returns a privileged receipt
  to the broker and does not automatically turn a tool's stdout or environment trace into worker
  observations, process state or training data. Domain result admission remains a separate boundary.
- A terminal result requires inspected process exit and verified removal of the exact owned
  container. Retain source ownership and terminal evidence transactionally before allowing
  conservative full-reservation accounting; no physical-time or storage refund is inferred.

## Acceptance and retained evidence

Implemented paths:

| Substrate | Store, API and read/write boundary |
| --- | --- |
| Reviewed execution declaration | `ProcessContainerProfile` in `container_contracts.py`; its full digest must equal Amber's pinned sandbox identity. Fixed policy adds disabled network, fixed resolver, namespaces, privilege/resource limits and exact supervisor. |
| Persistent private intent | `ProcessContainerExecutor._execute` checks `ProcessObservationStore` and resource scope, writes `process_container_workloads` plus owned raw canonical input, then commits `process_container_heads: prepared` before Docker I/O. |
| Dispatch and enforcement | `_expected`, `authorize` and `before_start` recheck current scope; `ProcessResourceStore.start` and head `starting` commit before `DockerContainerDriver.run` starts the inspected container. `container_supervisor.py` enforces the tool deadline inside that container. |
| Forensic capture | `ArtifactCatalog`, `artifact_information` and `artifact_references` retain raw `runtime`, `created`, `terminal`, `cleanup` captures, bounded output/failures and `ProcessContainerReceipt`. Original workload, capture, result, receipt and resource-journal owners are independent. No worker projection consumes them. |
| Historical reconstruction | `ProcessContainerStore.workload/inspect` verifies SQL/source joins, physical bytes and ownership, reconstructs wire input, validates retained supervisor/configuration and terminal/removal captures. |
| Accounting and event commit | `ProcessContainerStore.reconcile` rechecks complete terminal evidence; `container_evidence` charges the full reservation plus retained-byte overage. `ProcessStore.append_event` repeats evidence validation; generic events cannot settle unknown effects. |
| Explicit composition | `PPRLApplication.container_executor(profile)` constructs the broker-only adapter without launching it. No automatic CLI dispatch, planner exposure or generic result admission is added. |

Migration `c4a93d8e127b` follows `f6a8c2d4e913`, adds three tables without backfill, and preserves
immutable workload/receipt rows separately from mutable dispatch heads. Four generated schemas
cover runtime identity, profile, workload and receipt. These contracts are privileged even though
their type definitions are public source code.

Offline tests must reject changed command/input/profile/model/tool/lease/authority, duplicate
delivery, forged or drifted runtime observations, unexpected mounts/devices/env/network/capabilities,
corrupt/missing original evidence, and output-reference leakage. Exercise failure and cancellation
at every launch/capture/cleanup boundary and transactional rollback. New PPRL composition must
remain inert unless the runner is explicitly supplied.

An opt-in Docker fixture must establish exact stdin/command execution; worker UID and zero effective
capabilities; inability to become root or signal the supervisor; denied root writes, host files,
Docker socket, network and peer-container access; bounded output; deadline termination including
broker interruption; and removal of owned fixtures. Use cached image IDs, small CPU/memory/time
ceilings and no image pull. Record actual Docker/client/image identities, effective inspect data,
input/output digests, exit/termination reason, completeness/truncation, resource accounting,
transactional ownership, exact tests and cleanup evidence. Failed probes are retained, not relabeled
as successful isolation.

## Failure, rollback and non-goals

After durable intent, an uncertain launch is never automatically retried. On interruption, stop the
owned container when its exact identity can be established, retain all available evidence and
preserve the reservation when final state or cleanup is unknown. A broker restart must inspect
retained intent before future reconciliation; this slice does not implement a recovery scheduler.
Rollback disables this runner and preserves intent, receipts, source pins and accounting holds.
Never restore unrestricted in-process execution as a fallback or downgrade a populated database.

Non-goals are a general network-enabled workspace, a model-serving sandbox, GPU validation, loaded-
model attestation, authenticated model-worker registration, role assignment/handoff, automatic
result admission, live communication, complete independent forensic capture, physical billing
measurement, cloud activation, training and scientific continuity/improvement claims. These remain
dependencies of the full program; this slice does not reduce that scope.

SQL authority transitions and Docker start are not one atomic operation. A lifecycle change can race
the final pre-start check; responsive broker polling attempts termination every 100 ms while attached,
and control operations have their own ten-second client bounds. The container-local timer provides
a separately enforced workload deadline after supervisor startup. These are bounded stop mechanisms,
not an instantaneous revocation guarantee or an end-to-end stop-time SLA under host/daemon failure.
