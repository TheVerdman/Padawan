# Padawan

Padawan is an independent, persistent research operating system for developing and evaluating
open-weight student agents. Its core authority is evidence, not a teacher model: deterministic
graders outrank model opinion, every external response is retained, state-forked controls inherit
the same student cognition, and both successful and failed episodes remain research data.

This repository implements the durable core, complete bounded developmental workflows for
symbolic algebra, kernel-backed Lean mathematics, and closed-record appellate briefing, plus a
hard-gated Magellan agentic environment protocol:

- PostgreSQL-targeted SQLAlchemy persistence with SQLite for local development;
- immutable student states and transactional treatment/control forks;
- governed corpus lineage, matched-sibling leasing, exposure, contamination, and retirement;
- local and GCS content-addressed artifact stores plus append-only cryptographic provenance;
- a SymPy item generator and deterministic public-step grader;
- a pinned Lean 4.32.2/Mathlib 4.32.2 corpus and sandboxed kernel verifier;
- a pinned Fourth Circuit appellate pack with synthetic records, typed briefs and claim maps,
  deterministic rule/citation/quotation/leakage gates, and evidence-bound semantic adjudication;
- Magellan scenario, environment, world, plan/tool trace, approval, replay, reward, and training
  eligibility contracts with a deterministic verifier;
- temporal grounding with dual clocks, authoritative event frames, activity/freshness policy,
  durable operation spans, duration profiles, matched counterfactuals, and calibrated ETA actions;
- Responses-API-first OpenAI and OpenAI-compatible adapters, a contract-pinned authenticated
  Inkling-Small-Ampere/vLLM adapter, and an Anthropic teacher adapter;
- a loopback-only, model-neutral Padawan Interaction Lab with explicit-history streaming chat,
  immutable branches, per-turn serving manifests, temporary mode, and restricted trace inspection;
- a distinct persistent-process RL substrate for versioned project distributions, repeated
  macro-rollouts, immutable project-state/event replay, paired checkpoint forks, dynamic worker
  roles, and verifiable, empirical, adjudicated, or hybrid outcome authority;
- the actor-neutral Amber Protocol, which binds exact models, tools, targets, sandbox identity,
  egress, budgets, persistence, reviewers, stop conditions, and checkpoint policy before admitting
  each process action;
- evidence-citing teacher contracts and deterministic comment validation;
- algebra and domain-general crash-resumable developmental paths, idempotent external calls,
  worker leases, and stale recovery;
- matched-block experiments with counterbalancing, McNemar analysis, and bootstrap intervals;
- immutable harness profiles and execution manifests that bind checkpoint, serving, task, context,
  prompt/tool, environment, budget, and seed identity to runs, experiments, and comparisons;
- durable verifier/reward ledgers with versioned meta-utility recomputation and training eligibility;
- versioned multi-experiment studies plus fresh-item retention and interference scheduling;
- an external frozen-checkpoint registry with sealed-suite comparison, promotion, and revocation;
- a deterministic, rights-aware internal compiler for evidence, observed SFT, separately identified
  authored SFT, preference, RLVR, episode-process, PPRL trajectory, PPRL fork preference,
  PPRL-verifiable, continued-pretraining, and sealed-evaluation products with reason-coded
  exclusions;
- versioned lesson memory with branch isolation, negative retrieval evidence, snapshots, and
  rollback;
- an evidence-gated memory consolidation backend and an explicit refusal for unsupported parameter
  updates.

Live model execution is never replaced by a dummy. A pilot can proceed only when a real student
endpoint and real teacher credentials are configured; unavailable integrations are recorded as
failures rather than converted into passing evidence.

The current scope is explicit: algebra retains its original complete path, while Lean mathematics,
appellate briefing, and temporal grounding bind their own authorities to a shared, durable
developmental workflow.
Lean correctness still comes only from the pinned kernel. Appellate deterministic hard gates run
before a typed, evidence-bound semantic adjudicator; the closed pack still reports authority
currentness as unknown because no dependable citator is configured. Magellan's audited tree does
not yet satisfy Padawan's isolation, authorization, durable replay, approval, or Responses
requirements, so this repository reports it as blocked rather than fabricating a sandbox result.
GCS is operational, and delayed-retention/interference scheduling is durable and tested. The
external Inkling-Small-Ampere hotfix image and batch-one context ladder are validated through a
240,000-token target; Padawan now pins that exact Responses-only model/profile/checkpoint identity,
authenticates its capability preflight, and records the measured scope without calling Vertex
`Invoke` as though it were an OpenAI URL. Live target outcomes still require a deployed model,
stable consumer edge, and runtime credential. Training compilation is operational as an
internal, restricted artifact path, but no external trainer or parameter-update backend is bundled.
Temporal scenarios and authored bootstrap rows are implemented, but no claim is made that an
existing checkpoint has already learned the behavior.
The standardized developmental harness is now a versioned research object. Legacy evidence remains
readable, while new comparative claims fail closed if research controls are absent or changed axes
were not declared. Controlled work also binds its exact learned parent state and may be leased only
by a worker whose complete harness, current task/corpus, model, transport, domain, and environment
identity matches the recorded execution. Sealed checkpoint metrics must resolve to immutable
results from a fully observed study condition, and hard gates must verify the same immutable result,
suite, condition, and checkpoint. Suite labels, favorable subsets, and free-form evidence references
are not accepted as promotion lineage. Mutable Inkling edge identity is admitted only after the
responding endpoint reports and matches the configured deployment identity.
The Padawan Interaction Lab is a separate exploratory surface: its conversations are never
reported as experiment episodes or controlled benchmark evidence, and its first slice performs no
memory synthesis or training admission.
Persistent-process RL is a third, separate surface. Its control plane, replay engine, distribution
and replication contracts, Amber admissions, governed model-call binding, and training compilation
are implemented; no unrestricted generic project handler, external trainer, or weight-update
backend is bundled. Programs with non-verifiable objectives are PPRL, while only genuinely
verifiable outcome authority enters the PPRL-VR product. A single historical run can remain
evidence, but it cannot satisfy the distribution replication gate.
No S3 backend is planned for the current GCS deployment. Parameter calls fail as unsupported rather
than degrading to a no-op.
The first real-provider pilot attempt is documented in
[reports/live/2026-08-01-pilot-attempt.md](reports/live/2026-08-01-pilot-attempt.md); it was blocked
before episode creation by an unavailable Inkling server and by credentials not being selected in
that process. The later external-env check confirmed both provider key names are available, but no
provider request was made and the attempt is not reported as a live success.

## Development

Python 3.12 or 3.13 is required.

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,gcs]"
.venv/bin/alembic upgrade head
make check
```

Run PostgreSQL locally with `docker compose up -d postgres`, set
`PADAWAN_DATABASE_URL`, and run the PostgreSQL-marked concurrency tests separately.

The CLI is available as `padawan --help` after installation. The OpenAI implementation uses the
Responses API and structured outputs; legacy Chat Completions exists only behind an explicit
compatibility flag for non-OpenAI servers.

Lean and Magellan setup/verification are documented in [operations](docs/operations.md) and the
[Magellan integration contract](docs/magellan-improvement.md). Acceptance evidence is in the
[first-slice report](reports/verification/2026-08-01-round-2-slice.md) and the
[R2.3 report](reports/verification/2026-08-01-r2-3.md). The
[R2.5 protocol report](reports/verification/2026-08-01-r2-5-magellan-protocol.md) records both the
verified Padawan boundary and the still-blocked external acceptance gate. The
[R2.7a compiler report](reports/verification/2026-08-01-r2-7a-training-compiler.md) records the
rights-aware product boundary and its verification scope.
The [appellate briefing contract](docs/appellate-briefing.md) documents the closed court pack,
verification layers, currentness boundary, and offline training eligibility.
The [temporal grounding contract](docs/temporal-grounding.md) documents event-time evidence,
duration calibration, authored SFT provenance, and the real-versus-virtual clock boundary.
The [citator ADR](docs/adr/0011-citator-currentness-boundary.md) defines the licensed-API or
governed-import path required before currentness may become verified.
The [R2.6 verification report](reports/verification/2026-08-02-r2-6-appellate-briefing.md) records
the software acceptance evidence and remaining live gates.

Start with the [Round 2 plan](docs/round-2-plan.md), [architecture](docs/architecture.md),
[data model](docs/data-model.md), [experiment semantics](docs/experiment-semantics.md), and
[operations](docs/operations.md). The compiler and source-rights boundary is documented in
[internal training products](docs/training-products.md).
The [Padawan Interaction Lab contract](docs/interaction-lab.md) documents chat, trace, consent,
temporary-mode, private-reasoning, and deletion boundaries.
The [research-control contract](docs/research-controls.md) defines the harness measurement model,
comparability gate, and deferred retention/compaction, trajectory, mechanistic, atlas, and N+1
seams.
The [persistent-process RL contract](docs/persistent-process-rl.md) defines macro-agent state,
distributions, repetition, forks, outcome authority, PPRL/PPRL-VR training, and the Amber boundary.
Its architectural decisions are recorded in
[ADR 0013](docs/adr/0013-persistent-process-reinforcement-learning.md) and
[ADR 0014](docs/adr/0014-amber-protocol.md).
The full [PPRL and epsilon-charity research program](docs/pprl-epsilon-charity-program.md) preserves
the scientific thesis beyond the first implementation slices. The
[four-fabric architecture and audit](docs/pprl-four-fabric-architecture.md) maps what is implemented,
partial, and absent across persistent process, communication, forensic, and recovery substrates.
Future repository agents receive the required reading order and safety invariants from
[`AGENTS.md`](AGENTS.md).

## Repository boundaries

Padawan owns orchestration, evidence, state, experiments, and memory. Inkling remains the student
runtime and is called through an adapter. Heirloom remains an optional restricted audit/export
target. VECL-QB supplied audited design ideas, but Padawan does not import its prototype LoRA,
artifact, ledger, or episodic-store implementations.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
