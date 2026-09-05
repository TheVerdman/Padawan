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
role, worker-model identity, and explicit exact-input boundary, then records the rollout as the
exclusive owner of the shared external call. Provider-hosted state and tools are disabled on this
path: state remains
local, and tool use crosses its own separately admitted action boundary. Each admission declares
the next cumulative action, token,
artifact-byte, wall-time, and cost totals. The committed state must equal that reservation, and the
lease covers the reserved action wall time so a timeout cannot be mistaken for an unowned retry.
Those state fields remain per-action declarations. A separate explicitly reviewed resource grant
now funds one shared authorization-wide account; Amber reserves incremental allowances atomically,
and retained model results or committed events reconcile them once. Forks and replacement workers
do not replenish capacity. Unknown effects hold capacity and concurrency; known overages stop the
account. See `pprl-resource-boundary.md` for exact units, rates, evidence and trust limits. New PPRL
dispatch does not automatically retry an unresolved external effect.

The PPRL model wrapper returns normalized public output and explicit token counts. Raw results,
private channels, arbitrary provider metadata, and artifact references remain in privileged
invocation records. Event commit verifies their classification and retention through the invocation
ID and counts their bytes against the reservation; it does not require those raw references in
shared events. See `pprl-worker-output-boundary.md` and the observation/training boundaries below for
the implemented offline interfaces and their remaining runtime and semantic limits.

New initial-state and event artifact fields require reviewed process references. State/event
ownership pins the admitted candidate and private source dependencies transactionally; fork child
admission failure rolls back the whole fork. Claims refuse legacy or unretained references, while
privileged replay preserves their historical representation. See `pprl-reference-ingress-boundary.md`
for scope and SQLite rollback validation. `pprl-content-admission.md` describes the subsequent closed
content shapes, extension registry, admitted memory/evidence links, and policy receipts. Narrow
training projections are now explicit as described below; semantic provenance remains unfinished.
`pprl-worker-observation-boundary.md`
describes the allowlisted public planner DTO, exact canonical observation receipts, independent
retention, scope/rights validation, and admitted/denied proposal linkage. The planner receives a
detached observation; the effect executor is a separately configured trusted broker adapter.
Unbound decisions do not execute through the coordinator, and binding failure preserves the original
Amber decision. `pprl-generation-workload-boundary.md` adds a private exact-input receipt joining the
observation to fixed reviewed instructions/sampling/schema, configured model/transport and prepared
body/destination. Current admission is rechecked before dispatch and output admission. Completed
replay preserves its original time and ownership. The explicit local CPU tool path described in
`pprl-container-execution-boundary.md` additionally binds a reviewed Docker profile, exact workload,
resource reservation and independent broker capture, with an unprivileged command and a separate
container-local watchdog. Local fixture tests establish bounded enforcement inside the trusted
Docker/host/kernel perimeter. `pprl-worker-identity-boundary.md` adds explicit execution enrollment,
reviewed capability issuance/revocation, private ownership of one leased action, and bounded
authenticated claim/observe/propose requests. Protected APIs reject a copied lease without the
assigned credential. The stream adapter opens no listener and returns only a control envelope plus
an allowlisted observation or disposition. This proves capability possession inside the trusted
broker; model/host attestation, credential delivery/isolation, action masks, model-serving
containment and automatic replacement hydration remain absent. Enrolled forks are denied pending
explicit child-scope support; legacy scopes receive no implicit enrollment or backfill.

`pprl-assignment-recovery-boundary.md` adds an explicit reviewed recovery operation. Claim and
admission barriers reject earlier uncommitted effects independently of spare shared capacity.
Recovery checks the expected state and lease, original reviewer and current authority, fences the
old lease and optionally retires its credential. It releases only an untouched reservation with no
effect intent; retained complete model/container sources may reconcile usage once. Unknown effects
retain holds. Completed effects with no committed state transition stay review-required. Recovery
does not reconstruct a lost state update or admit raw output. Immutable private recovery receipts
and independent source owners support later inspection, including after a late result. Separate
native broker/client fixtures recover identical public observation bytes from persistent stores;
this is simulated replacement evidence, not an automatic scheduler or scientific continuity result.
Checked reads also reconstruct native source joins and original settlement evidence; fresh recovery
publication must pass that same check. `pprl-recovered-evidence-boundary.md` now implements explicit
reviewed source admission for complete model/container effects, including late retained results.
The private version-3 origin binds the full checked source set; only a separate process derivative
is admitted. Ordinary sources with recovery ownership cannot bypass that origin, and training use
is denied. Admission neither changes canonical state nor clears the effect barrier. Reviewed
successor/abandonment, task disposition and training-exclusion dependencies remain unimplemented
designs in `pprl-effect-resolution-boundary.md`.

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

The version-1 compiler products remain privileged research archives containing source state,
events, outcomes, and lineage. They are not model inputs. `ProcessTrainingProjectionStore` adds a
separate explicit projection: public trajectory, verifiable, and fork-preference JSONL is separated
from immutable private source/observation/decision/rights receipts. It reconstructs exact archive
rows, checks current Amber training authority, requires existing content/observation receipts and
training-use evidence admission, validates task content through an explicitly registered closed
generator schema, and reapplies replication minima after each product's exclusions. Equal public
examples remain separate samples with private lineage. No legacy admission is backfilled.

Archive and evidence dependencies receive independent transactional ownership. Public reads
revalidate source integrity, current policy/authority, and retention. An authority-sequence change
requires a new projection receipt, even when training remains permitted during pause or release
approval. `parameter_training_ready` is always false: attributable parameter learning still needs
actual observation-to-provider binding, action masks, behavior-policy and credit evidence, trainer
integration, and execution authorization. See `pprl-training-projection-boundary.md` for the exact
read/write paths, bounds, retained evidence, failure cases, and rollback.

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
mathematical environments inject separate typed planners and trusted executors into the PPRL
composition root, which preserves the
full declared context, horizon, workers, and tools inside Amber's boundary.

Amber is the authorization and evidence plane; it does not by itself instantiate or attest an OS,
container, VM, or accelerator sandbox. The repository therefore does not activate any real project
environment by default. `PPRLApplication.container_executor(profile)` explicitly constructs the
bounded CPU tool adapter; construction launches nothing. It observes and checks its pinned client,
daemon, image, command and controls, and does not provide cryptographic attestation. Other environment
adapters still need their own identity, enforcement and evidence contracts before execution. A
container's stdout/trace remains forensic; domain result admission is a separate reviewed boundary.

Padawan compiles restricted offline products and can register an externally trained descendant
checkpoint against their bundle digest. It does not yet bundle a trainer or a parameter-update
backend, so software completion is not evidence that any process policy or checkpoint has already
improved.
