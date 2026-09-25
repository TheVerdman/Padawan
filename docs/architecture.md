# Architecture

Status: current subsystem map, reviewed during the 2026-09-07 repository consolidation.
Dated validation and the consolidation findings are in the
[audit report](../reports/verification/2026-09-07-stage-2-consolidation-audit.md).

Padawan coordinates research on improving smaller open-weight models across developmental
learning, capability measurement, and persistent institutions. Domain difficulty and outcome
authority are distinct from the learning method and the scale of the process being studied.
The system owns orchestration, evidence, state, memory, and evaluation; model serving and parameter
training remain external integrations.

The [PPRL and epsilon-charity program](pprl-epsilon-charity-program.md) preserves the scientific
thesis and distribution-level evaluation requirements. The
[four-fabric architecture](pprl-four-fabric-architecture.md#architectural-layers) owns the boundary
among process memory, live coordination, privileged forensics, and recovery/scheduling.

## Subsystem map

Each row identifies current code, its input/output connection, and executable evidence. Tests
establish bounded software behavior; live execution and scientific conclusions require their own
evidence. Paths under `padawan/` are implementation owners, not claims that every planned feature exists.

| Subsystem and purpose | Owner, inputs, outputs, and consumers | Validation and remaining boundary |
| --- | --- | --- |
| Developmental learning | [Composition](../padawan/config/composition.py), [shared workflow](../padawan/domains/developmental/workflow.py), and [algebra workflow](../padawan/domains/algebra/workflow.py) consume governed tasks, parent states, model calls, and feedback; emit episodes, matched transfer, and lesson decisions for experiments and compilation. [Local composition](../padawan/experiments/local_developmental.py) binds the graduate-algebra pilot to these native stores. | [Algebra system test](../tests/system/test_algebra_workflow.py) and [domain workflow test](../tests/system/test_domain_developmental_workflow.py) cover restart, failures, transfer, and memory. An episode or memory update does not change weights. |
| Corpus, teaching, and lesson memory | [Corpus registry](../padawan/corpus/registry.py), [teacher service](../padawan/teaching/service.py), [comment validation](../padawan/teaching/validator.py), [lessons](../padawan/memory/lessons.py), and [update backends](../padawan/updates/backends.py) turn lineage, exposure, cited feedback, and matched outcomes into admitted branch memory, retrieval decisions, and rollback. The developmental loop consumes those lessons. | [Teaching](../tests/unit/test_teaching.py) and [memory](../tests/integration/test_memory.py) tests cover their gates. Recorded direct teacher influence is excluded from training; inherited retrieved lessons have a known [exported-input limitation](training-products.md#teacher-influence-and-exported-inputs). |
| Mathematics | [Builtin domains](../padawan/domains/builtin.py) register algebra and Lean. SymPy and the pinned Lean kernel own correctness; [graduate algebra](../padawan/domains/graduate_algebra.py) is a separate reviewed local pilot authority. | [Algebra tests](../tests/unit/test_algebra.py), system fixtures, and separately gated Lean execution. Graduate-algebra rubric reviews are not kernel proofs; see the [dated 64K result](graduate-algebra-64k-results.md). |
| Appellate briefing | [Appellate domain](../padawan/domains/legal/appellate) binds a closed record and typed claims to deterministic integrity gates, then an evidence-bound semantic adjudicator. Qualified verifier evidence feeds rewards and compilation. | [Verifier tests](../tests/unit/test_appellate_verifier.py) and the [contract](appellate-briefing.md) preserve hard-gate precedence. [Citator-backed currentness](adr/0011-citator-currentness-boundary.md) remains unconfigured. |
| Temporal grounding | [Temporal contracts](../padawan/temporal), [telemetry](../padawan/temporal/telemetry.py), and [domain](../padawan/domains/temporal_grounding) turn authoritative event frames and operation durations into matched tasks, freshness decisions, and authored demonstrations for the developmental/compiler paths. | [Temporal workflow tests](../tests/system/test_temporal_grounding_workflow.py) and the [contract](temporal-grounding.md) distinguish real and virtual clocks. Authored rows do not establish learned competence. |
| Magellan | [Domain package](../padawan/domains/magellan_improvement) defines scenario, environment handshake, trace, authorization, and verifier contracts. The registry deliberately installs no autonomous workflow. | [Verifier tests](../tests/unit/test_magellan_verifier.py) exercise refusals and evidence interpretation. [External execution acceptance](magellan-improvement.md) remains blocked. |
| Capability Atlas | [Registry](../padawan/atlas/registry.py), [orchestration](../padawan/atlas/orchestration.py), and campaign/adapter modules consume pinned suites and model/harness conditions; retain trial outcomes and failures. [Study binding](../padawan/atlas/studies.py) supplies exact comparisons; [reviewed derivatives](../padawan/atlas/evidence.py) may feed process evidence. | [Registry](../tests/integration/test_atlas_registry.py) and [study-bridge](../tests/integration/test_atlas_study_bridge.py) tests; [Atlas contract](capability-atlas.md). Training materialization and institutional subjects remain incomplete; eligibility flags alone admit no training data. |
| PPRL and Amber | [Composition](../padawan/pprl/composition.py), coordinator/store, observation, identity, resource, recovery, and task modules bind distributions and reviewed actions to persistent state, accounting, replay, and addressed worker views. [Amber](../padawan/governance/amber_store.py) admits control-plane actions. | [Observation tests](../tests/integration/test_process_observations.py) and [scripted continuity](pprl-scripted-continuity-boundary.md) cover bounded contracts. [Four-fabric gaps](pprl-four-fabric-architecture.md) include generic scheduling, live coordination, and physical attestation. |
| Training compilation | [Developmental compiler](../padawan/training/compiler.py), [PPRL compiler](../padawan/training/pprl.py), source rights, and reviewed projections emit immutable products and reason-coded exclusions from admitted snapshots. External checkpoints may cite those bundles. | [Compiler tests](../tests/integration/test_training_compiler.py), [product contract](training-products.md), and [PPRL projection boundary](pprl-training-projection-boundary.md). No external trainer or working parameter-update backend is bundled. |
| Studies and checkpoint evaluation | [Experiments](../padawan/experiments), [study engine](../padawan/studies/engine.py), evaluation scheduling, and [checkpoint registry](../padawan/checkpoints/registry.py) bind exact conditions and sealed results to retention, comparison, promotion, and revocation. | [Research-control tests](../tests/integration/test_research_controls.py) and the [measurement contract](research-controls.md) cover lineage and comparability. Checkpoint registration does not execute training. |
| Interaction Lab | [Composition](../padawan/interaction/composition.py), service/store, and web routes consume explicit history and target descriptors; emit exploratory conversations and restricted traces using shared evidence infrastructure. | [Interaction tests](../tests/integration/test_interaction_lab.py) and the [contract](interaction-lab.md). Chat creates no developmental episode or automatic memory/training admission. |
| Adapters and shared evidence | [Adapters](../padawan/adapters), [artifact stores](../padawan/artifacts), provenance, SQL stores, and [external calls](../padawan/orchestration/external_calls.py) retain prepared requests/results and expose outputs only to admitted consumers. Heirloom is a governed export boundary. | Provider mock-HTTP, prepared-request, storage, governance, and admission fixtures. Recorded authority does not attest a provider, host, or physical sandbox; see [private reasoning](private-reasoning-policy.md). |

The main connections are task → verified episode → memory decision; Atlas trial → fixed study
result → sealed checkpoint evidence; separately reviewed Atlas derivative → process evidence;
and admitted developmental/PPRL snapshot → training product. Raw forensics, exploratory chat,
and Atlas eligibility flags have no automatic path into worker memory or training.

## Runtime composition

Padawan has separate composition roots for developmental research, exploratory interaction, and
persistent-process research. `padawan.config.composition.build_live_application` creates the
developmental engine: one async database engine, the configured artifact backend, registered
domain authorities, external-call executors, a domain-selected workflow handler, and a supervisor.
A verifier-only domain cannot masquerade as a complete autonomous workflow. The subsystem map above
identifies their separate authorities.

`padawan.interaction.composition.build_interaction_application` is the separate local Interaction
Lab composition root. It reuses the artifact catalog, external-call ledger, operation telemetry,
and canonical model-serving identity from the research-control foundation. It does not construct a
run, episode, state fork, memory writer, or training compiler. Target-specific adapters live only at
this composition boundary; interaction schemas, routes, and services use student target IDs and
descriptors rather than a deployment product name.

`padawan.pprl.composition.build_pprl_application` is the separate process-scale control-plane
root. It reuses the database, artifact catalog, external-call ledger, training compiler, and
checkpoint boundary, but it does not construct a developmental run, episode, student-state fork,
or Interaction Lab trace. A concrete process environment injects a typed handler; the root itself
does not expose an arbitrary command executor. Provider clients may be wrapped by
`ProcessGenerationExecutor`, which requires the current rollout lease and the exact admitted Amber
decision before it can cross the model-I/O boundary.

Atlas has a distinct finite campaign path: preparation registers exact suites and execution
bindings; dispatch uses the shared external-call ledger; reporting reads native trial and accounting
records. Reusable manifest construction, local registration/reporting, and Vertex control belong to
`atlas.preparation`, `atlas.local_campaign`, and `atlas.vertex_control`. Local runtime inventory lives
with `adapters.openai_compatible.local_runtime`; source identity and process ownership live in
`orchestration.source_identity` and `orchestration.local_host`. Historical script names remain entry
points. Their exact prerequisites and controller/source pin rules are in [operations](operations.md).
Cloud-control recovery, one-attempt model transport, unresolved-call holds, and owned-process cleanup
remain distinct contracts.

The official OpenAI adapter is fixed to `POST /v1/responses` and has no legacy fallback. The
validated Inkling client is likewise Responses-only: it negotiates the exact
Inkling-Small-Ampere model, profile, checkpoint, structured-output, streaming, batch-one, TP4, and
configured-window identity before its first generation. It uses the authenticated consumer edge,
not the Vertex `Invoke` RPC, requests SSE explicitly, disables response storage/continuation, and
makes one transport attempt. The model-server image and mutable Responses-edge image/deployment
are separate serving identities; the edge origin is credential-sanitized. Only the generic
OpenAI-compatible client may use legacy Chat
Completions when the operator explicitly enables compatibility fallback. Anthropic is a distinct
`/v1/messages` implementation.
Provider and research role are orthogonal: OpenAI is a baseline or teacher, while Inkling and an
explicit compatible open-weight runtime are target candidates. Role is persisted through student
state, run, attempt, and episode records; baseline runs cannot consolidate target memory.

The harness is also an experimental object. The live loop registers an immutable `HarnessProfile`
and one `ResearchExecutionManifest` before it creates a controlled run. The manifest binds student
and auxiliary model serving, exact learned parent-state identity, task/corpus, effective
workflow/condition/sampling parameters, environment, and seed to the profile's continuation,
reasoning-retention, context, prompt/tool, instrumentation, and budget policies. `runs` and
`experiments` point to the same content digest; workflow provenance and episode records repeat it.

The composition root also derives a worker identity from the full harness, task scope, clients, and
transport settings it actually constructs. The supervisor recomputes the current executable corpus
digest and admits a controlled lease only when the profile, every harness parameter, task/corpus,
model/transport, and environment identity match the registered execution. Missing or mismatched
identity leaves the controlled run unclaimed. Run and experiment creation separately reject
payload/design conditions that contradict the manifest. These checks do not weaken the Inkling
continuation guard.

`DomainRegistry` installs `math.algebra@1.0.0`, `math.lean@1.0.0`,
`legal.appellate.fourth_circuit@1.0.0`, `agent.magellan_improvement@1.0.0`, and
`temporal.grounding@1.0.0`. Algebra owns its specialized durable handler under
`padawan.domains.algebra`. Lean, appellate, and temporal grounding bind their domain authorities to
`DomainDevelopmentalWorkflowHandler`; the temporal workflow uses synthetic authoritative frames
and duration profiles. Magellan intentionally has no workflow; selecting it as the live domain
fails until its real sandbox and environment driver satisfy the handshake.

## Durable action loop

A `RunRow` is a state machine, not a call stack. One worker claim protects exactly one action. The
worker persists the next transition and releases the lease before another action may begin. The
specialized algebra path contains 20 transitions from `CREATED` through `COMPLETE`; the shared
domain path combines the same research invariants into 10 durable transitions. Retryable,
terminal, and review states are explicit. A database uniqueness constraint permits only one active
run for a student, while terminal transitions release that slot.

Before network I/O, `IdempotentGenerationExecutor` commits an external-call intent and restricted
request artifact. After I/O it commits a restricted response envelope before the run transition.
Replaying the same request ID returns the stored response and rejects a different request body.
Thus a crash after a provider response but before a state transition does not generate twice.
The same boundary records a `model_generation` operation span before I/O and an append-only status
event on wait, failure, or success. Terminal spans can be compiled into versioned p50/p90/p95 and
timeout profiles without retaining prompt text in workload metadata.
Interaction and persistent-process invocations reuse the same intent/artifact/telemetry boundary
without inventing a developmental run. The database requires exactly one owner among `run_id`,
`interaction_trace_id`, and `process_rollout_id`. A true Interaction Lab event iterator forwards
public text deltas while keeping private-reasoning deltas out of the transcript; the terminal
result still closes the idempotency and telemetry records.
The corpus registry also commits a conservative student exposure before each student transport call;
a terminal failure releases its leases but the exposed sibling group remains ineligible for that
student.

Both implemented paths preserve this episode flow:

1. lease one three-item matched sibling group and create an active episode;
2. record the immutable base state and fork treatment/control states transactionally;
3. run and deterministically grade the cold item;
4. request a strict evidence-citing teacher intervention and validate it;
5. run and grade a revision on the treatment branch;
6. run unseen sibling transfer on both inherited branches;
7. record the matched block and decide whether evidence permits a lesson-memory write;
8. atomically record exposures and retirement;
9. append the selected branch state and promote it as canonical;
10. commit the typed episode and complete the run.

Treatment must show a strict transfer advantage to become canonical; a tie retains control. Both
branches and all failed work remain stored. Lean student output is a narrow proof term and only the
pinned kernel decides correctness. Appellate student output is a content-only draft: Padawan binds
brief identity, scenario, research role, and time; deterministic integrity gates run before any
semantic adjudicator call, and the adjudicator can cite only evidence attached to each claim.

## Persistent-process action loop

A `ProcessRolloutRow` is a separate macro-agent state machine. One lease protects one proposed
action. The handler first produces a side-effect-free `ProcessActionProposal`; Amber persists the
complete request and decision against the immutable active envelope; only an `admitted` decision is
passed to the handler's execution method. The resulting immutable event cites that one decision,
its parent and resulting states, artifacts, optional worker invocation, and rollout status.

Project distributions contain train, adaptive-development, validation, and sealed partitions plus
generator, contamination, difficulty, and replication identity. The default contract requires at
least two unique instances and two stochastic rollouts per instance, and the compiler rechecks the
declared minima rather than inferring a learning distribution from one trajectory. Forks create
paired continuations from the same state. Episodic and continual persistence are both represented;
cross-project memory requires explicit Amber authority and remains separate from student lesson
memory.

Outcome assessment and training eligibility are independent append-only decisions. Verifiable,
empirical, adjudicated, and hybrid authorities may support ordinary PPRL trajectory or preference
products when their policy and rights permit. The PPRL-verifiable product additionally requires
genuinely verifiable authority and RLVR rights; a scalar score alone cannot upgrade another
authority kind.

## Research evidence and offline checkpoint lifecycle

`VerifierResult` rows are append-only raw evidence. A reward cites those results through hard gates
and retains every raw component, normalizer, coefficient, missing-data action, input digest, and
policy digest. `RewardEngine.recompute` rebuilds the complete record from the registered immutable
policy; a missing component is never silently converted to zero, and a failed hard gate makes
scalar utility unavailable. Training eligibility is a separate cited decision.

Project-authored gold behavior has a separate admission identity. An `AuthoredDemonstration` must
cite deterministic verified evidence and SFT-confirmed source/output rights; the compiler emits it
as `authored_sft`, while independently successful target attempts remain in `sft`. This keeps an
oracle bootstrap example from masquerading as an observed student success.

`StudyEngine` groups independently persisted, state-forked experiments under one immutable suite
manifest. Aggregation remains block-level and reports missing, contaminated, and infrastructure
attrition separately. `EvaluationScheduler` creates retention probes from a source episode's final
immutable state and interference probes from the post-interference state. Both require a fresh
rotating-shadow or sealed-anchor item in the source competency. Due claims lease the trial and its
exact corpus item together; completion requires a matching persisted prompt exposure unless the
outcome is an explicit infrastructure failure.

Study bindings also repeat each experiment's research-execution digest and carry open factor
values. A study declares which research axes may vary; observed undeclared differences or missing
controls disable causal-claim permission. Completion requires an outcome for every bound block and
content-addresses that evidence into immutable condition/checkpoint results. A result is causal only
when every block is analyzable with no missingness, contamination, or infrastructure exclusion. New
checkpoint evaluation records require metrics that exactly resolve to one eligible result for the
same condition, checkpoint, and sealed suite, plus hard-gate verifier evidence scoped to that same
immutable lineage. Legacy experiments remain inspectable but do not acquire comparative authority
from a report generated after the fact.

Checkpoint weights remain external and frozen during a study. `CheckpointRegistry` records model,
tokenizer, parent, training-bundle, and runtime identities; accepts evaluations only against a
registered identical suite; and compares N with N+1 under an immutable lexicographic policy.
Integrity gates, required missing metrics, and regression limits precede capability or efficiency.
Comparisons and promotion decisions have independent integrity/recomputation checks. Promotion,
including the promotion transition itself, revalidates the metric-result lineage. Rejection,
quarantine, revocation, and registry-level rollback do not imply that Padawan trained or mutated
any weights.

## Storage

PostgreSQL is the production concurrency target. PostgreSQL claims use row locks with `SKIP LOCKED`;
SQLite uses conditional updates and exists for local work and tests. Alembic owns schema creation.

The database stores normalized, indexed records and immutable artifact references. Raw provider
bytes, exact rendered requests, private traces when actually exposed, and command manifests live in
a SHA-256 content-addressed backend. The local backend uses a same-directory temporary file,
`fsync`, and atomic replacement. The GCS backend uses create-only generation preconditions, CRC32C,
generation-pinned reads, and immutable object metadata. Both recheck SHA-256 on read. Networked
artifact operations are moved off the async worker loop. Garbage collection defaults to dry-run and
accepts the database-derived referenced digest set.

Provenance is a separate append-only hash chain. Each event commits a canonical payload hash, prior
chain hash, actor, code revision, environment, state lineage, and episode link. Verification
recomputes the stream rather than trusting a stored boolean.
Harness profiles and research execution manifests are immutable content-digested rows. They contain
component identities and digests, not raw prompts, responses, credentials, or private reasoning.

## Repository integrations

- Inkling remains an independently served vLLM/OpenAI-compatible runtime. Padawan pins the validated
  conversion checkpoint separately from its served-model alias, records the conversion/profile/image
  evidence, runtime revision, configured and measured context limits, tensor parallelism, negotiated
  server identity, and any server telemetry; it never invents unavailable token or reasoning data.
- Heirloom is a restricted audit export. Padawan emits a signed, policy-filtered bundle that the
  sibling Heirloom structural validator can inspect. This does not upgrade structural validation
  into semantic correctness.
- VECL-QB is not imported. Its provenance, artifact, and episodic-store ideas were independently
  reimplemented to avoid coupling Padawan to its experimental trainer.
- GCS uses Application Default Credentials at runtime. Padawan stores no credential path or key and
  has no S3 symmetry requirement.
- Lean uses a committed `lean-toolchain`, Lake dependency lock, and exact Mathlib revision. The
  verifier resolves the pinned closure before sandboxing and invokes Lean directly, so candidate
  execution cannot trigger Lake dependency updates.
- Magellan is inspected as an external Git worktree without imports. A runtime-only handshake binds
  its complete dirty-state digest, content-addressed source materialization, PostgreSQL/
  authorization/reset policy, typed tool surface, Responses protocol, and durable idempotency.
  Local paths are not persisted.

## Present boundaries

The subsystem map states the current gaps; [operations](operations.md) defines runtime prerequisites.
Developmental software workflows do not imply a currently deployed target, a successful live study,
or a learned improvement. The local graduate-algebra pilot is separately composed and reported.
Magellan execution, admitted appellate currentness, Atlas training materialization, and generic PPRL
scheduling/coordination remain incomplete. `UnsupportedParameterUpdateBackend` explicitly refuses
weight updates. No fixture or small pilot establishes an RL advantage across a sampled distribution.

Amber records authority at the control-plane boundary; it does not attest an OS, container, VM,
network, accelerator, filesystem, secret sandbox, or loaded model. Private reasoning, raw traffic,
security telemetry, environment traces, and mechanistic records remain researcher-only. Any
institutional use requires a separately reviewed, provenance-bearing derivative through the
applicable admission boundary. These invariants are specified in the
[four-fabric architecture](pprl-four-fabric-architecture.md) and [private-reasoning policy](private-reasoning-policy.md).

## Retained evidence and plans

Active contracts are linked above. Dated reports record what a particular checkpoint or campaign
actually established; historical launch instructions and budgets do not authorize a new run.
Their original files and evidence links remain intact.

| Record | Status and use |
| --- | --- |
| [Stage 1 preservation](../reports/verification/2026-09-07-stage-1-preservation-checkpoint.md), [Stage 2 consolidation](../reports/verification/2026-09-07-stage-2-consolidation-audit.md) | Repository checkpoints with exact validation and limits. |
| [Round 2 plan](round-2-plan.md) | Historical roadmap and implementation chronology; use this architecture map for current navigation. |
| [First slice](../reports/verification/2026-08-01-round-2-slice.md), [R2.3](../reports/verification/2026-08-01-r2-3.md), [Magellan protocol](../reports/verification/2026-08-01-r2-5-magellan-protocol.md), [compiler](../reports/verification/2026-08-01-r2-7a-training-compiler.md), [appellate](../reports/verification/2026-08-02-r2-6-appellate-briefing.md) | Dated software acceptance and their remaining execution gates. |
| [First provider attempt](../reports/live/2026-08-01-pilot-attempt.md) | Historical blocked attempt; not a live success. |
| [PPRL execution ledger](pprl-execution-ledger.md), [complexity review](pprl-complexity-review.md) | Foundation evidence and dated roadmap decisions. The earlier consolidation hold is superseded by this authorized consolidation. |
| [Fixed-algebra preregistration](pprl-nemotron-pilot-preregistration.md) | Withdrawn proposal; prepared files remain evidence. |
| [Frontier scout](atlas-frontier-experiment.md), [64K/128K attempt](atlas-64k128k-second-attempt.md), [fixed comparison](atlas-fixed-comparison.md), [compiler experiment](atlas-compiler-experiment.md) | Closed or superseded campaign designs with retained partial outcomes and cleanup evidence. |
| [Local coding endurance](atlas-local-endurance.md) | Historical local condition; its configuration and receipt dependencies remain frozen. |
| [Graduate-algebra pilot](graduate-algebra-local-pilot.md), [64K results](graduate-algebra-64k-results.md) | Completed exploratory episode with reviewed scores, no transfer advantage, and no admitted lesson; not an independent proof or efficacy study. |
