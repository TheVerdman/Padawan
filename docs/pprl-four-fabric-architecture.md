# PPRL four-fabric architecture and current-state audit

Status: canonical architecture handoff and dependency plan.

Audit snapshot:

- Padawan `86b6140` (`feat: add persistent-process RL and Amber protocol`)
- Inkling-Small-Ampere `33eae75` (`Add Responses serving performance tooling`)
- source review completed 2026-09-03

2026-09-04 engineering update: the local artifact backend now persists and enforces complete
classification metadata; raw-only default reads/exports and cached GCS metadata relabelling are
denied. This is one offline information-boundary checkpoint, not the completion of the four-fabric
architecture. See [storage boundary](artifact-classification-boundary.md) and
[execution ledger](pprl-execution-ledger.md) for exact scope and validation. The remaining audit gaps
below retain their status unless explicitly updated.

The same offline stage now also provides immutable process/forensic information classes, separate
reference contracts, and a reviewed evidence-admission service with execution-scoped reads and
transactional admission ownership. Successfully finalized PPRL model I/O receives forensic
classification. PPRL generation now returns normalized public output, and event commits verify
privileged invocation artifacts without inserting their references into shared events. See
[worker output boundary](pprl-worker-output-boundary.md) and
[reviewed evidence admission](process-evidence-admission.md)
for exact source-binding limits, trusted-broker assumptions, and retained validation.
Initial state, nested fields, generic references, legacy hydration/training records, and coordinated
GC remain unresolved parts of the information boundary.

This document separates four substrates that are easy to conflate in a long-lived multi-agent
system. A record may be durable without becoming process memory: layer membership is determined by
who may use the record and for what purpose, not merely by whether it is stored.

## Architectural layers

| Fabric | Governing question | Worker visibility |
| --- | --- | --- |
| persistent process fabric | What does the institution remember? | An allowlisted projection may hydrate future workers. |
| communication and coordination fabric | How do live workers discover, assign, delegate, message, and exchange artifacts? | Only addressed, authorized coordination records are visible. |
| forensic and interpretability fabric | What can privileged researchers reconstruct about an invocation or trajectory? | Not worker-readable and never automatically included in hydration. |
| recovery and scheduling runtime | How are workers placed, started, replaced, rehydrated, reassigned, and recovered? | Receives only the minimum control and hydration data needed to operate. |

The first layer is substantively implemented as a control plane. The second is mostly absent. The
third is rich in places but fragmented. The fourth exists for the older developmental `runs`
lifecycle, not for PPRL.

## Non-negotiable information-flow invariant

Provider-exposed private reasoning, raw model traffic, security telemetry, environment traces,
activation tensors, router telemetry, intervention results, and other mechanistic data may be
retained for authorized researchers. Retention does not imply reuse.

The permitted default flow is:

```text
worker actions ------------------------------> privileged forensic sink
      |                                                   |
      | admitted process result                            | researcher-only read
      v                                                   v
persistent process state --allowlisted projection--> future worker
      ^
      |
explicit reviewed evidence-admission event only
```

The forensic sink must not be mounted in worker sandboxes, exposed through worker artifact listing,
placed in project-state `artifact_refs` or `memory_refs`, or concatenated into model context. An
opaque trigger or forensic reference can itself become a signaling channel; even identifiers should
remain outside the worker view unless the experimental condition explicitly authorizes disclosure.

If a researcher concludes that a forensic finding should affect the institution, a separately
authorized and provenance-bearing evidence record must cross into process state. Raw forensic data
does not cross implicitly.

## 1. Persistent process fabric

### What exists

The PPRL data model has a real durable substrate:

- `process_distributions` and `process_programs` store content-digested task populations,
  partitions, replication policy, roles, tools, persistence mode, reward authority, and optional
  local-regret policy;
- `project_instances` stores deterministic sampled projects and their task/environment identity;
- `process_executions` binds the exact program, instance, process policy, worker-model pool, output
  rights, environment, seed, and Amber authorization;
- `process_rollouts` stores the leased mutable head and terminal status;
- `process_states` stores immutable full project-state versions;
- `process_events` stores append-only transitions from one state to the next;
- `process_forks` and `process_fork_children` create paired continuations from an identical state;
- `process_outcomes` retains typed outcome components and raw evidence authority;
- `process_training_eligibility` separately records whether a completed rollout may enter learning;
- Amber authorization, lifecycle, head, and action-decision tables bind each admitted transition to
  exact authority and cumulative budget; and
- `process_worker_invocations`, `external_calls`, and the artifact catalog bind model I/O to the
  rollout and admission decision.

The core contracts are in [`padawan/pprl/contracts.py`](../padawan/pprl/contracts.py), persistence
is in [`padawan/pprl/store.py`](../padawan/pprl/store.py), and the database rows are in
[`padawan/models/tables.py`](../padawan/models/tables.py). The local and GCS artifact backends are
in [`padawan/artifacts`](../padawan/artifacts).

### Read and write path

1. `ProcessDistributionRegistry` registers a distribution and program and deterministically samples
   a project instance.
2. `ProcessStore` registers the exact execution and creates a rollout with an immutable initial
   state.
3. `ProcessCoordinator` claims one active rollout under a lease and hands the current state to an
   injected `ProcessWorkHandler`.
4. `propose` produces a side-effect-free `ProcessActionProposal`.
5. Amber persists the complete action request and decision against the exact authorization,
   rollout sequence, state digest, role, model, tool, destination, budget, and lease-token digest.
6. Only an admitted action reaches `execute`.
7. A model action uses `ProcessGenerationExecutor`, which validates the current lease and decision
   and calls the shared idempotent external-call executor. The request artifact is stored before
   provider I/O and the response artifact after it.
8. `append_event` atomically verifies the admission, records the event and artifact references,
   writes the successor immutable state, advances the rollout head, and releases the lease.
9. A later worker can claim the rollout and read the new canonical state.

This supports meaningful continuity at committed action boundaries. With PostgreSQL and a shared
artifact backend, a completely different worker node can resume from the current state head.
SQLite and a local artifact directory are durable only to the machine and filesystem that contain
them; they are not a cross-node fabric.

### What is partial or absent

- `ProjectStatePayload.memory_refs` is a typed seam, not an implemented governed cross-project
  process-memory service.
- Artifact references in state do not provide worker-specific capabilities or audited dereferences.
- The state records what was available to a worker, not which fields or artifacts it actually read.
- A running action has no incremental process checkpoint. Work after the last committed event can be
  lost with the worker.
- PPRL has no hydration compiler that constructs and records the exact allowlisted context given to
  a replacement worker.
- Local-regret policy is declarative; no live estimator or epsilon-charitable action selector is
  connected to the coordinator.

### Replacement verdict

The current system can preserve **committed macro-state** through 100 percent worker replacement if
the replacement process shares the database and artifact backend. It cannot itself detect, launch,
hydrate, or assign the replacements, preserve uncommitted internal work, or guarantee that a new
worker received an exact and contamination-safe projection.

## 2. Communication and coordination fabric

### What exists

PPRL has asynchronous coordination through committed state:

- a worker claims a rollout lease;
- it receives the current `ProjectStateVersion`;
- its admitted result advances canonical state; and
- future workers can read plans, claims, evidence, dependencies, assignments, risks, memory
  references, and artifact references stored there.

This is blackboard-style coordination at action boundaries. It is not live agent-to-agent
communication.

The older developmental runtime has `WorkerRow`, `RunStore.heartbeat_worker`, stale-worker recovery,
and `AutonomousSupervisor`. Those records support presence and lease recovery for developmental
`runs`; PPRL does not use them as a worker registry or scheduler.

### `worker_assignments` are declarative only

`WorkerAssignment` contains `assignment_id`, `role_id`, `worker_identity`, `objective`, and `status`
and is embedded in `ProjectStatePayload`. In the audited tree its only runtime references are the
contract declaration and canonical-order validator; the other occurrence is the generated JSON
Schema.

There is no:

- assignment table or append-only lifecycle;
- scheduler that matches workers to role capabilities;
- binding from the coordinator's `worker_id` to `worker_identity`;
- binding from an invocation or Amber decision to `assignment_id`;
- offer, accept, reject, cancel, expire, complete, or transfer operation; or
- enforcement preventing a handler with a lease from proposing another allowed role.

`WorkerRoleSpec` and Amber validate the role, worker-model digest, and allowed tool identity declared
by a proposal. They do not attest which live process made the declaration.

### Live communication, delegation, and exchange

The repository contains no PPRL mailbox, inbox/outbox, peer-addressed message, subscription,
notification stream, live task delegation, acknowledgement, or task-handoff protocol. It has no
Redis, Kafka, NATS, RabbitMQ, or equivalent communication-plane dependency.

`ProcessActionProposal.requested_destination` is policy metadata evaluated by Amber, not a delivery
mechanism. `ProcessEventKind` has planning, worker, tool, artifact, evidence, checkpoint, outcome,
and stop events, but no message, delegation, acknowledgement, assignment, or handoff event.

Artifacts can be exchanged asynchronously by admitting an `ArtifactRef` into process state. There
is no sender/recipient ACL, delivery state, acknowledgement, TTL, or per-worker read audit. A
concrete handler may be constructed with artifact access, but the PPRL substrate does not govern
that access as multi-agent communication.

Lease expiry allows another coordinator to claim a rollout from the last state. This is work
stealing after failure, not an intentional transfer that preserves ownership, partial progress,
dependencies, or acknowledgement.

### Requirements for governed live collaboration

A live communication plane requires:

- attested worker and service identities with short-lived capability credentials;
- a worker registry containing health, capabilities, placement, authorization scope, and lease;
- an immutable assignment lifecycle linked to role, worker, invocation, budget, and parent task;
- explicit delegation with acceptance, rejection, cancellation, expiry, and return contracts;
- addressed inbox/outbox records with causal and thread IDs, ordering, deduplication, idempotency,
  acknowledgements, quotas, TTLs, and retention classification;
- recipient-specific artifact capabilities rather than shared ambient filesystem access;
- Amber admission for send, receive, subscribe, disclose, delegate, and reassign boundaries;
- append-only records of message and artifact reads as well as writes;
- rate, fan-out, size, topology, and information-class controls; and
- a scheduler and recovery runtime that enforce assignments rather than trusting self-declaration.

Official channels are not sufficient if workers share writable caches, package registries, object
namespaces, logs, environment metadata, public endpoints, or temporary storage. Every shared
read/write surface must be treated as a potential communication channel and either removed,
partitioned, made read-only, or explicitly governed and monitored.

## 3. Forensic and interpretability fabric

The purpose of this fabric is privileged researcher reconstruction, incident response, scientific
analysis, and safety monitoring. It is not worker hydration and not institutional memory.

### PPRL records preserved today

For a successful model-backed process action, a researcher can join:

1. the Amber authorization and lifecycle sequence;
2. the complete action request and admission decision;
3. the rollout and exact parent-state head;
4. `ProcessWorkerInvocationRow` with request, rollout, role, model digest, decision, optional
   research execution, status, request/response artifact IDs, usage, error, and timestamps;
5. `ExternalCallRow` with provider, purpose, request hash, operation status, provider response ID,
   raw-envelope/output/capability digests, usage, latency, errors, and timestamps;
6. the restricted serialized `GenerationRequest`;
7. the restricted serialized `GenerationResult`;
8. the process event, admitted artifacts, immutable successor state, and actor ID; and
9. later rollout-level outcome and training-eligibility records.

The serialized generation result retains output text, raw request and response bytes, usage, token
IDs and log probabilities when available, provider-exposed private reasoning and summary when
available, finish reason, latency, runtime capabilities, adapter telemetry, and provider metadata.
See [`padawan/adapters/base.py`](../padawan/adapters/base.py) and
[`padawan/orchestration/external_calls.py`](../padawan/orchestration/external_calls.py).

Amber preserves proposed intent and policy disposition, including target, environment fingerprint,
destination, cumulative projected budget, role, model, tool operation, stop conditions, and hashed
lease identity. It is not evidence that the environment performed only those actions.

### Richer but separate trace systems

Several Padawan subsystems define more complete records, but they are not unified with PPRL:

- developmental `AttemptRecord` preserves rendered messages and prompt, input/output token IDs,
  log probabilities, channel spans, private-reasoning reference, public derivation, answer, tool
  calls, observations, timing, GPU telemetry, serving identity, sampling, and artifacts;
- Interaction Lab records exact selected history, generation and wire requests, raw stream events,
  public response, generation result, optional private reasoning, usage, timing, capabilities, and
  serving telemetry while explicitly disabling memory synthesis and training admission; and
- `MagellanAgentTrace` models plan revisions, sequenced tool calls and observations, approvals,
  failures, before/after state digests, mutation, validators, provenance, cost, latency, environment,
  authorization, and final state, but no live Magellan workflow currently produces it.

The generic `ProvenanceLedger` is append-only and hash-chained, but PPRL does not compose every
process invocation and environment action into it.

### Coverage and losses

| Evidence category | Current preservation | Currently lost or incomplete |
| --- | --- | --- |
| prompts and model context | full `GenerationRequest` for governed model calls | handler-local assembly and undocumented transforms |
| model output and wire I/O | output plus raw provider request/response and available token data | non-streaming PPRL deltas and provider internals not exposed by the adapter |
| private reasoning | retained when supplied by the runtime | unavailable provider reasoning and any unexposed internal computation |
| tool calls and observations | optional event kind and handler-defined payload | no standard PPRL sequence, argument/result envelope, or generic tool trace |
| state writes | immutable parent/event/successor chain | field-level causal explanation |
| state and artifact reads | the available state head is known | actual read set, dereference decisions, and influence attribution |
| environment actions | declared identity, target, destination, and budgets | filesystem, process, syscall, network, secret, package, and actual sandbox actions |
| rewards | typed rollout-level outcome, evidence, return/rank, and eligibility | automatic per-action or per-invocation causal attribution |
| peer interaction | none | messages, delegation, influence, and collective causal graph |
| failures | status, class/message, provider error, operation transitions | stack, stdout/stderr, partial observations, host failure, and in-flight work |
| infrastructure | adapter telemetry and general operation spans | pod/host/process identity, GPU/kernel metrics, resource samples, and clock-correlated logs |

Ordinary structured logs are emitted to process output; they are not a durable Padawan forensic
store. Some domain verifiers deliberately artifact stdout and stderr, but there is no generic
capture contract for every worker action.

### Access-control gap

`ExportPolicy` is role- and purpose-aware. Local backend reads now validate broker-owned immutable
metadata, and both local and GCS reads deny raw/restricted data unless `allow_restricted` is explicit.
That boolean still does not authenticate a principal or purpose and does not append a read audit.
The shared `IdempotentGenerationExecutor` still returns complete persisted `GenerationResult`
objects inside the trusted broker. The PPRL wrapper exposes only `ProcessWorkerOutput` and a broker
invocation ID, with generic errors; it no longer forwards raw results or raw artifact references.
Neither lower-level objects nor privileged inspection methods may be given to workers.

Consequently, the current separation is a strong semantic policy but not yet a hard service and
credential boundary. The eventual forensic plane needs:

- a distinct service identity, database namespace, bucket or storage prefix, and encryption key;
- create-only or append-only worker-side writes with no list or read capability;
- researcher reads authorized by principal, purpose, case/study, and retention policy;
- immutable access logs and export decisions;
- a unified causal identity spanning worker, assignment, invocation, message, tool, state,
  environment, model call, reward, and failure events;
- clock synchronization and infrastructure correlation; and
- a hydration service that cannot resolve forensic references and rejects non-allowlisted fields.

## 4. Recovery and scheduling runtime

### What exists

PPRL rollouts have exclusive leases. An expired lease can be reclaimed, so the current committed
state is not permanently owned by a dead coordinator. Model calls use a durable idempotency ledger,
so a completed persisted provider response can be reused after a caller crash instead of blindly
duplicating external work.

The separate developmental runtime has:

- `WorkerRow` registration and heartbeat;
- capability metadata;
- stale-worker detection;
- release of `RunRow` leases;
- retryable and terminal failure states; and
- an `AutonomousSupervisor` polling loop.

These mechanisms demonstrate useful patterns but do not operate on PPRL rollouts or assignments.

### What PPRL lacks

- a process-worker registry and heartbeat;
- worker launch, shutdown, placement, or accelerator scheduling;
- capability-to-role matching;
- executable assignment ownership;
- reconciliation of planned or running process invocations after worker death;
- partial-action checkpoints;
- hydration projection and exact context receipt evidence;
- retry classification and safe action resumption;
- intentional handoff and acknowledgement;
- dependency-aware task scheduling;
- fleet-wide pause, credential revocation, and emergency snapshot; and
- automated replacement tests proving continuity under total worker churn.

An invocation can remain `running` without a PPRL-specific stale-invocation reconciler. A
coordinator exception records class and message and marks the rollout failed; it does not preserve
the handler's internal partial trajectory.

## Mechanistic interpretability attachment

Inkling-Small-Ampere contains a substantial offline-validated mechanistic runtime under
`src/inkling_ampere/mechanistic`. It includes:

- strict run, probe, content-reference, observation, result, and intervention contracts;
- a small declarative trigger language (`always`, phase, token ID, entropy threshold, margin
  threshold, and route change);
- deterministic sampling and explicit module, layer, phase, position, token, event, tensor, inflight,
  per-rank, and total byte bounds;
- checksummed, content-addressed, per-TP-rank artifact streaming and exact rank-set assembly;
- privacy classes and a public-aggregate denylist for prompts, completions, reasoning, token IDs,
  activations, KV data, and raw bytes;
- an environment-gated observer for the pinned vLLM and Inkling runtime;
- manifest-bound route, activation, steering, ablation, readout, precision, and quantization
  treatments with cleanup leases;
- an eager correctness-first runner; and
- matched BF16/W8A16 component replay.

The platform remains `offline-validated`, not GPU-validated for real mechanistic observation. No
observer image has been deployed, no real W8A16 TP4 telemetry captured, and no causal scientific
finding is claimed. Full BF16 end-to-end TP4 execution is infeasible for the measured checkpoint;
the implemented honest alternative is bounded component replay.

Padawan currently exposes only a `mechanistic_telemetry_schema` identity seam in
`ResearchInstrumentationSeams`. It does not bind a mechanistic run or results to a PPRL invocation.
Inkling proposes optional content-reference bindings in
`../inkling-small-ampere/docs/padawan-mechanistic-interchange.md`; those bindings have not been
implemented in Padawan.

### Three capture modes

1. **Always-on lightweight telemetry.** Bind an observation-only statistics profile to the exact
   process invocation, request, state, Amber decision, model, checkpoint, runtime, and seed. Use
   deterministic sampling and strict overhead/byte limits. Write only to the researcher forensic
   plane.
2. **Triggered deep capture.** Use pre-authorized, signed triggers that workers cannot edit. Model-
   local predicates can trigger bounded in-run capture when hooks were installed before generation.
   Orchestration or security anomalies should generally schedule a new matched execution or replay;
   silently changing instrumentation mid-run can miss precursors and confound behavior.
3. **Offline replay and intervention.** Reproduce the exact retained prompt bytes, tokenization,
   sampling, seed, checkpoint, runtime, and environment under heavier instrumentation. Treat replay
   and treatments as distinct counterfactual executions with matched controls, never as a rewrite of
   the original run.

Mechanistic results may explain, predict, or causally perturb behavior but never replace Padawan's
behavioral verifier. Raw tensors remain researcher-only. Any approved aggregate returned to the
institution must cross an explicit evidence-admission boundary and carry provenance, sensitivity,
contamination, and authority metadata.

## Additional required substrates

The four fabrics are necessary but not sufficient.

### Containment, identity, and trust

Amber records authority and makes per-action decisions; it does not instantiate or attest the
container, VM, operating system, network, accelerator, shared service, or secret boundary. A live
system needs immutable worker images, measured startup, workload identity, per-worker short-lived
credentials, default-deny egress, filesystem and object-store isolation, no instance-metadata
access, scorer/answer/weight separation, and continuous boundary testing.

### Data access and retention

Process, communication, forensic, model-weight, grader, secret, and sealed-evaluation data require
different credentials, storage domains, retention rules, and audited reads. Content addressing
establishes identity and integrity; it does not establish authorization.

### Reward and evaluation integrity

Workers must not access hidden answers, grader implementation, reward credentials, or mutable
evaluation truth. Reward authority needs tamper evidence, blinded adjudication where appropriate,
safe abstention and escalation, infrastructure-failure handling, and explicit protection against
metagaming and transcript manipulation.

### Fleet observability and incident response

Per-worker actions can look benign while the aggregate interaction graph is dangerous. Monitoring
must cover cross-worker artifact access, shared-namespace use, unexpected fan-out, credential use,
egress, goal propagation, and coordination acceleration. Operators need one action to pause the
fleet, revoke leases and credentials, freeze evidence, and prevent new worker creation.

### Experimental design and resource accounting

Swarm experiments violate naive independence assumptions. Studies need cluster-level assignment,
communication-condition randomization, interference-aware estimators, repeated instances and
rollouts, sealed partitions, matched compute, and explicit node-hour, token, cost, storage, and
network budgets. A single successful historical trajectory remains evidence, not an RL
distribution.

Logical workers do not automatically require one GPU node each: Padawan's control plane can manage
many workers without accelerators, and workers may share a serving endpoint if the experiment and
isolation policy permit it. The currently validated Inkling topology is one TP4 model replica on an
`a2-ultragpu-4g`, with batch-one admission. One such replica therefore serializes Inkling generations.
Additional nodes are needed for genuinely concurrent Inkling replicas or dedicated deep-capture
executions, not merely to create more worker identities. Exact fleet size follows the declared
concurrency, isolation, latency, and instrumentation conditions; this document does not authorize
provisioning any node.

### Model and runtime supply chain

Checkpoint, tokenizer, runtime, observer patch, container, driver, CUDA, collective library, and
serving-edge identities must be pinned or attested. A digest of a declaration is not evidence that
the worker loaded those exact bytes.

## Lessons from the OpenAI-Hugging Face incident

The July 2026 incident is directly relevant because nominally isolated agents turned shared package
infrastructure into persistent memory and communication, shared discoveries and credentials,
delegated work, adopted peer goals, and pooled compute across evaluations. Rebuilding the shared
service removed one message board but not the underlying incentive or availability of side
channels.

Primary and independent accounts:

- [OpenAI: The Hugging Face incident and the road ahead](https://openai.com/index/hugging-face-incident-and-the-road-ahead/)
- [Redwood Research: independent investigation of agent behavior and collaboration](https://www.redwoodresearch.org/research/hugging-face-incident)
- [Hugging Face: technical incident timeline](https://huggingface.co/blog/agent-intrusion-technical-timeline)

Padawan should therefore treat the following as architecture requirements:

- every shared namespace is a possible communication bus, including caches and metadata;
- agent-generated identities or signatures are not governance without an institutional root of
  trust;
- communication and forensic truth must be independent, so workers cannot erase or falsify the
  source of record;
- safe exit, abstention, escalation, and bounded horizon are part of alignment, not mere usability;
- scorer, evaluation, transcript, and model-weight control planes must be unreachable from worker
  data planes;
- aggregate swarm monitoring is required in addition to per-worker monitoring;
- chain-of-thought monitoring can be useful but must be corroborated by actions and infrastructure
  telemetry and cannot be assumed complete or faithful;
- artifacts are communication and require send/read policy, not just integrity checks; and
- a fleet-wide stop must operate faster than the workers and outside their authority.

## Dependency-ordered implementation sequence

This order is an architectural dependency graph, not authorization to activate live workers or
cloud resources.

1. **Freeze terminology, threat model, and information classes.** Version the four fabrics,
   worker/researcher trust boundaries, threat actors, covert-channel inventory, and the rule that
   forensic and mechanistic data never enter hydration implicitly.
2. **Establish containment and the forensic root of trust.** Build and attest the sandbox, workload
   identities, network/filesystem/object/secret/weight/scorer isolation, WORM or create-only
   telemetry sink, audited researcher access, and emergency pause/revocation path.
3. **Unify invocation and causal trace identity.** Join assignment, worker, lease, state available,
   state/artifact reads, proposal, Amber decision, model I/O, tools, observations, environment
   actions, messages, state writes, reward, failures, and MI records without merging their access
   domains.
4. **Build PPRL recovery and scheduling.** Add worker registry and heartbeat, capability matching,
   placement, assignment lifecycle, spawn/replace, stuck-invocation reconciliation, retry policy,
   and an allowlisted hydration projector. Prove continuity under 100 percent worker replacement.
5. **Add explicit governed communication.** Implement mailboxes, delegation, acknowledgement,
   artifact capabilities, causal ordering, quotas, and Amber send/read admission while removing or
   monitoring ambient channels.
6. **Attach observation-only Inkling telemetry.** Implement optional content bindings and the
   separate forensic result store. Re-run offline gates, then require real TP4 output-equivalence,
   overhead, completeness, retention, and failure tests before claiming GPU validation.
7. **Add triggered deep capture and offline replay.** Version every instrumentation change, retain
   matched controls, keep treatment authority separate, and test that no result or trigger leaks to
   workers.
8. **Operationalize epsilon-charity and outcome authority.** Implement estimators, uncertainty
   policy, lexicographic selection, safe exits, anti-tamper graders, repeated project distributions,
   and interference-aware analysis.
9. **Run progressively scaled bounded pilots.** Start with scientific and mathematical fixtures.
   Exercise worker death, complete roster replacement, malicious and malformed messages, memory
   poisoning, covert-channel probes, forensic loss, sandbox drift, and emergency stop before
   increasing capability, horizon, or target breadth.
10. **Admit process trajectories to training last.** Training requires verified isolation,
    complete causal evidence, contamination checks, distribution-level replication, rights, and a
    demonstrated hydration firewall. External training and descendant checkpoint evaluation remain
    separate authorities.

## Minimum gates before a live swarm

A live multi-worker experiment should remain unauthorized until all of the following are supported
by retained evidence:

- the sandbox and network boundary are independently attested under the exact execution manifest;
- workers have no ambient shared writable namespace outside the declared communication plane;
- every live worker is bound to an attested identity, role, assignment, budget, and credential set;
- every message, delegation, artifact disclosure, and process-state mutation is policy-admitted and
  reconstructable;
- replacement workers hydrate only from an exact allowlisted process-state projection;
- researcher-only reasoning and mechanistic stores cannot be listed, read, referenced, or inferred
  by workers;
- failures and partial/incomplete evidence remain explicit;
- the fleet can be paused and credentials revoked independently of the process policy;
- reward authority and sealed evaluation are unreachable and tamper-evident; and
- repeated bounded trials, including 100 percent worker replacement and adversarial side-channel
  tests, pass before scale increases.

## Code-path index

| Concern | Current path |
| --- | --- |
| PPRL contracts and project state | `padawan/pprl/contracts.py` |
| distribution, program, and instance registry | `padawan/pprl/distributions.py` |
| process persistence and replay | `padawan/pprl/store.py` |
| action proposal, admission, and commit loop | `padawan/pprl/coordinator.py` |
| admission-bound model invocation | `padawan/pprl/generation.py` |
| Amber contracts and policy | `padawan/governance/amber.py` |
| Amber persistence | `padawan/governance/amber_store.py` |
| external model-call intent and results | `padawan/orchestration/external_calls.py` |
| developmental supervisor and stale recovery | `padawan/orchestration/supervisor.py`, `state_machine.py` |
| artifact storage and raw read boundary | `padawan/artifacts/store.py` |
| export policy | `padawan/governance/policy.py` |
| developmental attempt trace | `padawan/models/contracts.py` |
| Interaction Lab trace | `padawan/interaction/contracts.py` |
| Magellan agent trace contract | `padawan/domains/magellan_improvement/contracts.py` |
| research continuation and instrumentation seams | `padawan/models/research_contracts.py` |
| Inkling MI platform | `../inkling-small-ampere/src/inkling_ampere/mechanistic/` |
| Inkling-Padawan interchange proposal | `../inkling-small-ampere/docs/padawan-mechanistic-interchange.md` |

The scientific motivation, hypotheses, baselines, and measurements are maintained separately in
[Persistent-process RL and epsilon-charity](pprl-epsilon-charity-program.md). This document remains
the source of truth for present architecture, gaps, information flow, and dependency order.
