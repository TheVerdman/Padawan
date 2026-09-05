# Padawan agent entry point

Before changing persistent-process research code or documentation, read these in order:

1. `docs/pprl-epsilon-charity-program.md` for the full research thesis, terminology, hypotheses,
   baselines, and staged program.
2. `docs/pprl-four-fabric-architecture.md` for the current implementation audit, missing layers,
   information-flow boundary, and dependency-ordered build sequence.
3. `docs/persistent-process-rl.md`, `docs/private-reasoning-policy.md`,
   `docs/adr/0013-persistent-process-reinforcement-learning.md`, and
   `docs/adr/0014-amber-protocol.md` for implemented contracts and decisions.

Preserve the distinction among:

- the persistent process fabric: what the institution remembers;
- the communication and coordination fabric: how live workers collaborate;
- the forensic and interpretability fabric: what privileged researchers can reconstruct; and
- the recovery and scheduling runtime: how workers are created, replaced, hydrated, reassigned,
  and recovered.

## Non-negotiable boundaries

- Provider-exposed private reasoning, raw model traffic, security telemetry, environment traces,
  and mechanistic-interpretability data are researcher-only forensic records. Never automatically
  place them, their content, or discoverable references to them in worker prompts, hydration,
  `ProjectStatePayload`, `memory_refs`, `artifact_refs`, or shared process memory.
- If a forensic finding must affect the institution, admit a separate, reviewed, provenance-bearing
  evidence record through an explicit policy boundary. Retention is not admission.
- Treat every shared writable namespace, including caches, filenames, object metadata, and artifact
  listings, as a possible communication channel. Worker communication must be explicit, addressed,
  authorized, bounded, and reconstructable.
- Amber records and enforces declared authority at Padawan's control-plane boundary. It does not
  create or attest an OS, container, VM, network, accelerator, filesystem, or secret sandbox.
- Do not activate cloud/GPU resources, deploy endpoints, launch a live swarm, or broaden network,
  data, credential, or tool authority without explicit user authorization.

## Current implementation boundary

Padawan implements the PPRL and Amber control plane: distributions, repeated rollouts, immutable
project-state transitions, leases, forks, outcome authority, governed model calls, replay, and
training-product compilation. It does not yet implement a live PPRL communication fabric, executable
worker assignment lifecycle, PPRL launcher/scheduler/hydrator, complete forensic capture, connected
Inkling mechanistic telemetry, generic project sandbox, trainer, or parameter-update backend.
`worker_assignments` are declarative state today.

An explicit local CPU container tool runner now binds observation, admitted authority, reviewed
profile, resource hold and privileged execution evidence. It is inert by default and is a narrow
execution boundary inside the trusted Docker/host/kernel perimeter, not a model-serving sandbox,
authenticated worker lifecycle or independent attestation. See `docs/pprl-container-execution-boundary.md`.

Explicitly enrolled executions also require broker-issued, expiring worker capabilities and a private
one-action lease assignment at protected APIs. A bounded request adapter accepts already connected
streams; it launches no listener or worker. Authored `worker_assignments` remain declarative.
Credential possession is not attestation of a model, process, host or reviewer. Full assignment
lifecycle, credential delivery/isolation and replacement scheduling remain unfinished. See
`docs/pprl-worker-identity-boundary.md`; enrolled executions cannot fall back to legacy unauthenticated
APIs or fork into unenrolled child executions.

An explicit reviewed recovery API now fences stale leases, releases reservations only when no
effect intent exists, and reconciles independently retained complete results. Per-rollout claim and
admission barriers stop uncommitted effects even when global budget remains. Unknown effects retain
holds; known results without a process transition remain review-required. These private receipts
are not hydration or permission to rerun work. See `docs/pprl-assignment-recovery-boundary.md`.
Automatic detection, placement, multi-action lifecycle and scientific replacement validation remain
unfinished.

A privileged terminal abandonment API now cancels a fenced unresolved rollout with an immutable
review and independent source ownership. It preserves state/events, costs and unknown-effect holds;
private replay and compiler exclusions retain the original attempt. It does not create a continuing
successor, admit a domain outcome, prove cleanup, or authorize a new task. See
`docs/pprl-effect-resolution-boundary.md`.

An explicitly enrolled finite task plan now binds each declared instance/condition/replication to
one exact rollout, execution, initial payload and original shared resource grant. Enrollment precedes
all rollouts in that authorization; replacement preserves the task owner, and undeclared roots or
forks are refused. This is private control state, not authored `worker_assignments` or scientific
preregistration. Legacy authorizations gain no implicit ownership guarantee. The first finite
workflow uses terminal abandonment for unresolved effects; general reviewed continuing successors,
dynamic task graphs and scheduling remain ahead. See `docs/pprl-task-ownership-boundary.md`.

Use **PPRL** for the general program and **PPRL-VR** only when the declared outcome is genuinely
verifiable. RL evidence requires sampled task distributions and repeated stochastic rollouts; a
single historical run may be evidence but is not a training or comparative distribution.

Small safe slices are implementation stages, not reductions of the full program. Documentation and
reports must distinguish implemented contracts, offline validation, GPU validation, live execution,
and scientific evidence. Preserve unrelated changes, never commit secrets or credentials, and run
checks proportional to the change before committing.
