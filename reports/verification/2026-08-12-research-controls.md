# Research-control slice verification

## Scope

This verification covers the immutable harness-profile and research-execution contracts, live
developmental binding, fail-closed comparability, study/checkpoint gates, generated schemas,
migration graph, and legacy-read compatibility implemented from Padawan revision `4bd45ef`.

## Local validation

- `ruff format --check .`
- `ruff check .`
- `PYTHONPATH=. mypy padawan`
- `PYTHONPATH=. python scripts/generate_schemas.py --check`
- `PYTHONPATH=. pytest -m "not postgres and not live and not lean"`
  - 198 tests collected
  - 192 passed
  - 6 deselected by the explicit marker expression

The selected suite includes SQLite migration upgrade/downgrade/metadata checks, controlled
run/experiment/study bindings, semantic-axis comparison, retention × compaction seam behavior,
configuration-drift rejection, legacy active-run compatibility, checkpoint-evaluation admission,
the validated Inkling continuation guard, and the existing algebra/domain/temporal system tests.

## Not validated in this slice

No PostgreSQL-marked, live-provider, or Lean-marked test was run. No serving node, external model
call, training job, checkpoint mutation, capability-atlas ingestion, interactive trajectory,
mechanistic telemetry capture, retention × compaction experiment, or checkpoint N+1 evaluation was
performed. The sibling Inkling-Small-Ampere repository was not modified.

The 240,000-token Inkling value remains transport/exact-retrieval evidence only. This slice adds no
benchmark-reproduction, long-horizon reasoning, quantization-fidelity, router-stability, teaching,
or training result.
