# Architecture

Padawan is an independent process and database. It coordinates model runtimes; it is not embedded
in a checkpoint server, a teacher provider, Heirloom, or VECL-QB. Symbolic algebra keeps its
original complete research path. Lean mathematics and appellate briefing use a shared complete
developmental path with domain-owned execution and verification authorities. Magellan Improvement
remains a corpus/verifier package behind explicit external execution gates. State, evidence,
orchestration, and adapter boundaries remain model-neutral.

## Runtime composition

`padawan.config.composition.build_live_application` is the only live composition root. It creates
one async database engine, the configured artifact backend, registered domain authorities,
external-call executors, a domain-selected workflow handler, and a supervisor. A verifier-only
domain cannot masquerade as a complete autonomous workflow. The authorities remain separate:

| Authority | Implemented owner | Role |
| --- | --- | --- |
| student runtime | `adapters.*` | real async generation and capability reporting |
| corpus | `CorpusRegistry` | lineage, leasing, exposure, retirement, quarantine |
| domain registry | `DomainRegistry` | versioned domain packages and workflow capability boundary |
| shared developmental control | `DomainDevelopmentalWorkflowHandler` | matched leasing, state forks, teacher validation, transfer, memory decision, retirement, and episode commit for non-algebra domains |
| algebra verifier | `AlgebraGrader` | deterministic SymPy outcome and first-invalid-step evidence |
| Lean verifier | `LeanVerifier` | pinned, sandboxed Lean-kernel proof authority |
| appellate verifier | `AppellateBriefVerifier` | closed-pack rule, record, citation, quotation, leakage, and separately adjudicated semantic evidence |
| appellate adjudicator | `AppellateAdjudicationService` | strict-schema, claim-scoped semantic judgment from admitted evidence only |
| citator | provider adapter plus admitted court-pack source | independent currentness evidence; intentionally unconfigured in the first pack |
| Magellan verifier | `MagellanScenarioVerifier` | isolated-world, authorization, trace, and replay authority |
| temporal frame | `padawan.temporal` | authoritative event time, activity, freshness, active-operation, and duration contracts |
| temporal verifier | `TemporalPolicyVerifier` | deterministic continuity, honesty, action, duration, and next-check authority |
| operation telemetry | `OperationTelemetryStore` | append-only operation transitions and reproducible duration profiles |
| teacher | `TeacherService` plus provider adapter | structured intervention generation |
| comment validation | `CommentValidator` | evidence, contradiction, span, and leakage checks |
| state | `StateStore` | immutable lineage, symmetric forks, canonical promotion |
| episode | `EpisodeStore` | typed attempts, grades, interventions, trials, final episodes |
| memory | `LessonMemory` | versioned lessons, retrieval decisions, conflicts, rollback |
| experiment | `ExperimentEngine` | deterministic blocks, counterbalancing, paired analysis |
| research control | `ResearchControlRegistry` | immutable harness/execution identity and fail-closed comparability |
| study | `StudyEngine` and `EvaluationScheduler` | versioned aggregation, retention/interference due work |
| reward | `RewardEngine` | immutable verifier evidence, policies, utility recomputation |
| checkpoint | `CheckpointRegistry` | external lineage, sealed-suite comparison, lifecycle decisions |
| governance | `governance.*` | access, retention, export, and command-manifest policy |
| consolidation | `MemoryConsolidationBackend` | evidence-gated lesson consolidation and rollback |
| exploratory interaction | `InteractionService` and `InteractionStore` | explicit-history chat, immutable branches, consent snapshots, and non-benchmark traces |
| student target selection | `StudentTargetRegistry` | model-neutral target descriptors, batch-one leases, and explicit readiness preflight |

`padawan.interaction.composition.build_interaction_application` is the separate local Interaction
Lab composition root. It reuses the artifact catalog, external-call ledger, operation telemetry,
and canonical model-serving identity from the research-control foundation. It does not construct a
run, episode, state fork, memory writer, or training compiler. Target-specific adapters live only at
this composition boundary; interaction schemas, routes, and services use student target IDs and
descriptors rather than a deployment product name.

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
Interaction invocations reuse the same intent/artifact/telemetry boundary with an
`interaction_trace_id` owner instead of inventing a `run_id`. The database requires exactly one of
those owners. A true event iterator forwards public text deltas while keeping private-reasoning
deltas out of the transcript; the terminal result still closes the idempotency and telemetry
records.
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

Algebra, Lean mathematics, and appellate briefing have complete software workflows. Their real
target studies still depend on the Inkling endpoint; no live result is claimed by software tests.
The first appellate pack has no admitted citator, so currentness remains `unknown` and cannot be
self-attested by its adjudicator. The audited Magellan tree lacks the required world isolation,
durable idempotency, identity enforcement, protected approvals, and Responses endpoint, so it has
no live workflow. GCS is implemented; S3 is intentionally absent. There is no parameter update
implementation. `UnsupportedParameterUpdateBackend` fails explicitly because no backend can yet
isolate, evaluate, commit, and restore a real weight update. Retention and interference are durably
scheduled, leased, completed, and recovered, but no instrumented Inkling extension currently
supplies router/expert or activation telemetry. The research-control contracts expose typed seams
for that later instrumentation, capability-atlas ingestion, interactive trajectories, the
retention × compaction factorial, and checkpoint N+1 evaluation; absent seams are not results.
