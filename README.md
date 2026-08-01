# Padawan

Padawan is an independent, persistent research operating system for developing and evaluating
open-weight student agents. Its core authority is evidence, not a teacher model: deterministic
graders outrank model opinion, every external response is retained, state-forked controls inherit
the same student cognition, and both successful and failed episodes remain research data.

This repository implements the durable core and one complete bounded symbolic-algebra research
workflow:

- PostgreSQL-targeted SQLAlchemy persistence with SQLite for local development;
- immutable student states and transactional treatment/control forks;
- governed corpus lineage, matched-sibling leasing, exposure, contamination, and retirement;
- an atomic local content-addressed artifact store and append-only cryptographic provenance;
- a SymPy item generator and deterministic public-step grader;
- Responses-API-first OpenAI and OpenAI-compatible adapters, an Inkling/vLLM adapter, and an
  Anthropic teacher adapter;
- evidence-citing teacher contracts and deterministic comment validation;
- crash-resumable run transitions, idempotent external calls, worker leases, and stale recovery;
- matched-block experiments with counterbalancing, McNemar analysis, and bootstrap intervals;
- versioned lesson memory with branch isolation, negative retrieval evidence, snapshots, and
  rollback;
- an evidence-gated memory consolidation backend and an explicit refusal for unsupported parameter
  updates.

Live model execution is never replaced by a dummy. A pilot can proceed only when a real student
endpoint and real teacher credentials are configured; unavailable integrations are recorded as
failures rather than converted into passing evidence.

The current scope is explicit: algebra is operational; delayed-retention scheduling, an S3 backend,
and parameter updates are not. Parameter calls fail as unsupported rather than degrading to a no-op.
The first real-provider pilot attempt is documented in
[reports/live/2026-08-01-pilot-attempt.md](reports/live/2026-08-01-pilot-attempt.md); it was blocked
before episode creation by an unavailable Inkling server and absent teacher credentials, and is not
reported as a live success.

## Development

Python 3.12 or 3.13 is required.

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/alembic upgrade head
make check
```

Run PostgreSQL locally with `docker compose up -d postgres`, set
`PADAWAN_DATABASE_URL`, and run the PostgreSQL-marked concurrency tests separately.

The CLI is available as `padawan --help` after installation. The OpenAI implementation uses the
Responses API and structured outputs; legacy Chat Completions exists only behind an explicit
compatibility flag for non-OpenAI servers.

Start with [architecture](docs/architecture.md), [data model](docs/data-model.md),
[experiment semantics](docs/experiment-semantics.md), and [operations](docs/operations.md).

## Repository boundaries

Padawan owns orchestration, evidence, state, experiments, and memory. Inkling remains the student
runtime and is called through an adapter. Heirloom remains an optional restricted audit/export
target. VECL-QB supplied audited design ideas, but Padawan does not import its prototype LoRA,
artifact, ledger, or episodic-store implementations.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
