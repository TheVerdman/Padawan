# Persistent-process reinforcement learning

Persistent-process reinforcement learning (PPRL) treats a durable project process as the learning
agent. The process owns canonical project state and survives the replacement of every individual
model or tool worker. PPRL-VR is the narrower case in which the declared outcome authority is
genuinely verifiable.

## Two Padawan learning layers

The developmental layer improves an individual student through bounded episodes, treatment/control
state forks, unseen transfer, lesson memory, and checkpoint evaluation. The PPRL layer consumes
worker capabilities and improves project-scale behavior: planning, delegation, experimentation,
evidence integration, recovery, and stopping.

The layers share research-control identities, artifacts, provenance, reward evidence, studies,
training compilation, and checkpoint governance. They do not share lifecycle enums or overload one
another's records.

## Macro-rollout identity

One process execution binds:

- a versioned program and project distribution;
- one sampled project instance and replication index;
- a process-policy identity and exact worker-model pool;
- the sandboxed environment and its effective parameters;
- one Amber authorization envelope; and
- a seed.

A rollout is reconstructed from immutable state versions and append-only events. A worker action is
one leased transition. Responses and artifacts are stored before the transition is committed so a
crash cannot silently duplicate external work.

The coordinator uses a two-phase boundary. `propose` must be side-effect-free. Amber then stores the
complete action request and its decision against the active immutable envelope. Only an admitted
decision is passed into `execute`; it is bound to the exact rollout sequence, state digest, and
hashed lease token, and the resulting event must cite that same decision. Model calls add a second
check: `ProcessGenerationExecutor` requires the current unexpired rollout lease, admitted request,
role, and worker-model identity, then records the rollout as the exclusive owner of the shared
idempotent external call. Provider-hosted state and tools are disabled on this path: state remains
local, and tool use crosses its own separately admitted action boundary. Each admission reserves
the next cumulative action, token,
artifact-byte, wall-time, and cost totals. The committed state must equal that reservation, and the
lease covers the reserved action wall time so a timeout cannot be mistaken for an unowned retry.

The PPRL model wrapper returns normalized public output and explicit token counts. Raw results,
private channels, arbitrary provider metadata, and artifact references remain in privileged
invocation records. Event commit verifies their classification and retention through the invocation
ID and counts their bytes against the reservation; it does not require those raw references in
shared events. This closes the model-result path only. See `pprl-worker-output-boundary.md` for
remaining state, hydration, and training-projection gaps.

New initial-state and event artifact fields require reviewed process references. State/event
ownership pins the admitted candidate and private source dependencies transactionally; fork child
admission failure rolls back the whole fork. Claims refuse legacy or unretained references, while
privileged replay preserves their historical representation. See `pprl-reference-ingress-boundary.md`
for scope, SQLite rollback validation, and the still-unfinished nested and projection boundary.

## Distributions and evidence

A process distribution declares train, adaptive-development, validation, and sealed partitions,
difficulty strata, generator identity, seed namespace, contamination scope, and replication
minimums. Learning and comparative claims require multiple unique instances and multiple stochastic
rollouts per instance. Checkpoint forks provide paired continuations from identical project states.

Reward authority is explicit:

- **verifiable**: deterministic kernels, exact tests, or formal checks;
- **empirical**: preregistered measurement and replication rules;
- **adjudicated**: blinded structured judgment with retained disagreement evidence; or
- **hybrid**: declared composition of multiple authorities and hard gates.

Unknown and infrastructure-failed outcomes remain non-learning-eligible. A later scalar policy may
not erase the raw evidence or retroactively turn an unknown result into success or failure.

Outcome assessment does not itself authorize training. A separate immutable process-training
eligibility decision cites outcome IDs, policy identity, source and output rights, evidence, and
allowed lanes. The compiler produces three distinct process-scale products:

- `pprl_trajectory` for complete eligible macro-trajectories;
- `pprl_fork_preference` for strict outcome orderings between paired continuations; and
- `pprl_verifiable` only when authority is genuinely verifiable and both rights manifests permit
  RLVR.

Before admitting any of them, the compiler verifies all state/event digests, split and completion
status, eligibility, rights, environment and execution identity, the complete Amber envelope and
lifecycle chain, every cited request/decision pair, and the distribution's unique-instance and
repeated-rollout minima. An eligibility decision is accepted only while Amber permits training;
later quarantine, revocation, or explicit expiry excludes the rollout from newer snapshots while
the append-only history keeps older point-in-time bundles reproducible. One historical rollout may
be retained as evidence, but it is not treated as a reinforcement-learning distribution.

## Persistence

Episodic PPRL resets project state for each instance. Continual PPRL may admit governed process
memory across projects. Project-local state, cross-project process memory, and student lesson memory
are separate stores with separate contamination and promotion rules.

The first implementation exercises the persistence, distribution, fork, outcome, training, and
Amber paths with bounded deterministic scientific/mathematical fixtures. Later environments can
increase horizon, worker dynamism, compute, and target breadth without replacing those contracts.

## Operator surface and present boundary

`padawan pprl` registers distributions, programs, Amber envelopes, exact executions, rollouts,
outcomes, and eligibility decisions, and exposes authorization history plus deterministic rollout
inspection/replay. It intentionally has no generic `execute` command. Concrete scientific and
mathematical environments inject typed handlers into the PPRL composition root, which preserves the
full declared context, horizon, workers, and tools inside Amber's boundary.

Amber is the authorization and evidence plane; it does not by itself instantiate or attest an OS,
container, VM, or accelerator sandbox. The repository therefore does not activate any real project
environment by default. A later environment adapter must enforce and attest the envelope's pinned
sandbox/tool identities, filesystem scopes, network policy, and resource ceilings before it can be
treated as an execution boundary.

Padawan compiles restricted offline products and can register an externally trained descendant
checkpoint against their bundle digest. It does not yet bundle a trainer or a parameter-update
backend, so software completion is not evidence that any process policy or checkpoint has already
improved.
