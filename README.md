# Padawan

Padawan is an independent, persistent research operating system for developing and evaluating
open-weight student agents. Its core authority is evidence, not a teacher model: deterministic
graders outrank model opinion, every external response is retained, state-forked controls inherit
the same student cognition, and both successful and failed episodes remain research data.

This repository implements the durable core, one complete bounded symbolic-algebra research
workflow, a kernel-backed Lean mathematics verifier track, and a hard-gated Magellan agentic
environment protocol:

- PostgreSQL-targeted SQLAlchemy persistence with SQLite for local development;
- immutable student states and transactional treatment/control forks;
- governed corpus lineage, matched-sibling leasing, exposure, contamination, and retirement;
- local and GCS content-addressed artifact stores plus append-only cryptographic provenance;
- a SymPy item generator and deterministic public-step grader;
- a pinned Lean 4.32.2/Mathlib 4.32.2 corpus and sandboxed kernel verifier;
- Magellan scenario, environment, world, plan/tool trace, approval, replay, reward, and training
  eligibility contracts with a deterministic verifier;
- Responses-API-first OpenAI and OpenAI-compatible adapters, an Inkling/vLLM adapter, and an
  Anthropic teacher adapter;
- evidence-citing teacher contracts and deterministic comment validation;
- crash-resumable run transitions, idempotent external calls, worker leases, and stale recovery;
- matched-block experiments with counterbalancing, McNemar analysis, and bootstrap intervals;
- durable verifier/reward ledgers with versioned meta-utility recomputation and training eligibility;
- versioned multi-experiment studies plus fresh-item retention and interference scheduling;
- an external frozen-checkpoint registry with sealed-suite comparison, promotion, and revocation;
- a deterministic, rights-aware internal compiler for evidence, SFT, preference, RLVR, process,
  continued-pretraining, and sealed-evaluation products with reason-coded exclusions;
- versioned lesson memory with branch isolation, negative retrieval evidence, snapshots, and
  rollback;
- an evidence-gated memory consolidation backend and an explicit refusal for unsupported parameter
  updates.

Live model execution is never replaced by a dummy. A pilot can proceed only when a real student
endpoint and real teacher credentials are configured; unavailable integrations are recorded as
failures rather than converted into passing evidence.

The current scope is explicit: algebra has the complete autonomous developmental workflow; Lean
mathematics and Magellan Improvement have governed corpus generation and real evidence verifiers
but not live workflows. Magellan's audited tree does not yet satisfy Padawan's isolation,
authorization, durable replay, approval, or Responses requirements, so this repository reports it
as blocked rather than fabricating a sandbox result. GCS is operational, and delayed-retention/
interference scheduling is durable and tested. Live target outcomes still require the Inkling
endpoint; appellate briefing and parameter updates remain subsequent gates. Training compilation
is operational as an internal, restricted artifact path, but no external trainer is bundled.
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

Start with the [Round 2 plan](docs/round-2-plan.md), [architecture](docs/architecture.md),
[data model](docs/data-model.md), [experiment semantics](docs/experiment-semantics.md), and
[operations](docs/operations.md). The compiler and source-rights boundary is documented in
[internal training products](docs/training-products.md).

## Repository boundaries

Padawan owns orchestration, evidence, state, experiments, and memory. Inkling remains the student
runtime and is called through an adapter. Heirloom remains an optional restricted audit/export
target. VECL-QB supplied audited design ideas, but Padawan does not import its prototype LoRA,
artifact, ledger, or episodic-store implementations.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
