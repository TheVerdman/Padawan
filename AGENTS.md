# Padawan agent entry point

## Scope and completion

Carry the user's current request through implementation, relevant validation, and fixes. Ordinary
local code/documentation edits and offline tests using disposable fixtures can proceed within the
requested scope without approval at each step. Check the selected test's dependencies before
running it; `live`, `gcs`, `postgres`, and `docker` tests have additional runtime requirements.
Run checks proportional to the change and expand them only when a failure or affected contract
justifies it. A documentation-only change does not require a model run or a full research campaign.

Honor authorization already given for this task. A previous run's review checkpoint, read-only
monitor scope, or fixture budget applies to that run; do not carry it into unrelated development.
Ask only when a required decision or authorization is missing or the next action expands scope.
When blocked, name the exact action and applicable boundary and continue independent authorized work.

## Read according to the change

Use the relevant sections of these documents when the task needs them; there is no mandatory
reading sequence for every edit:

- Research thesis, terminology, hypotheses, or experimental design:
  [PPRL and epsilon-charity](docs/pprl-epsilon-charity-program.md).
- Architecture, present implementation, missing layers, or sequencing:
  [four-fabric architecture](docs/pprl-four-fabric-architecture.md). Follow its links to the specific
  execution, identity, recovery, effect-resolution, or task-ownership boundary being changed.
- PPRL contracts and project-state semantics: [persistent-process RL](docs/persistent-process-rl.md)
  and [ADR 0013](docs/adr/0013-persistent-process-reinforcement-learning.md).
- Private reasoning or forensic information flow: [private-reasoning policy](docs/private-reasoning-policy.md)
  and the affected admission/projection boundary linked from the architecture document.
- Amber authorization or release behavior: [ADR 0014](docs/adr/0014-amber-protocol.md).

Check current source and relevant evidence before making capability claims. Historical snapshots
and memory notes are scoped evidence, not a replacement for current implementation or task intent.

## PPRL implementation invariants

The following rules govern the Padawan system being built. Its worker admission, lease, recovery,
and review protocols are runtime contracts; they are not extra approval steps for ordinary
repository editing or offline development tests. Preserve their enforcement in code.

- Keep persistent process memory, live communication/coordination, researcher-only forensics,
  and recovery/scheduling distinct. Durability alone does not admit a record to worker context.
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
  data, credential, or tool authority without explicit user authorization. Existing authorization
  remains valid within its stated scope and limits.

Use **PPRL** for the general program and **PPRL-VR** only for verifiable outcomes. RL and comparative
claims require sampled task distributions and repeated stochastic rollouts. Small implementation
fixtures do not set the scientific task's difficulty, horizon, or scale. Reports must distinguish
implemented contracts, offline validation, GPU validation, live execution, and scientific evidence.
Preserve unrelated changes and never commit secrets or credentials.
