# Research-control slice verification

## Scope

This verification covers the immutable harness-profile and research-execution contracts, exact
learned parent-state identity, live worker admission, fail-closed comparability, suite membership
and checkpoint-participation gates, immutable condition/checkpoint study results, complete-block
sealing, checkpoint metric and scoped hard-gate lineage, endpoint-verified mutable edge-artifact
identity, eligibility-aware corpus admission, credential-safe endpoint provenance, generated
schemas, migration graph, and legacy-read compatibility implemented from Padawan revision `4bd45ef`.

## Local validation

- `ruff format --check .`
- `ruff check .`
- `PYTHONPATH=. mypy padawan`
- `PYTHONPATH=. python scripts/generate_schemas.py --check`
- `PYTHONPATH=. pytest -m "not postgres and not live and not lean"`
  - 209 tests collected
  - 203 passed
  - 6 deselected by the explicit marker expression

The selected suite includes SQLite migration upgrade/downgrade/metadata checks, controlled
run/experiment/study bindings, semantic-axis comparison, retention × compaction seam behavior,
configuration-drift rejection, exact parent-state comparison, full harness/task/corpus worker
mismatch rejection, contradictory initial run/experiment input rejection, transport flag and
endpoint identity, startup and pre-generation server-reported edge mismatch rejection,
URL-credential redaction, unknown
compactor/instrumentation and declared-transport rejection, retirement/quarantine corpus exclusion,
post-sampling frozen-inventory continuation, legacy active-run compatibility, task/environment suite
admission, incomplete-study sealing rejection, strict attrition eligibility, active-study and
invented metric-lineage rejection, immutable result resolution, condition-specific uniqueness,
cross-condition hard-gate rejection, evaluated-checkpoint participation, the validated Inkling
continuation guard, and the existing algebra/domain/temporal system tests. All 74 generated JSON
Schemas match their Pydantic contracts.

## Not validated in this slice

No PostgreSQL-marked, live-provider, or Lean-marked test was run. The new edge-identity check was
validated only with a local mock transport; the deployed edge must publish the documented transport
identity before a future live composition will be admitted. No serving node, external model call,
training job, checkpoint mutation, capability-atlas ingestion, interactive trajectory, mechanistic
telemetry capture, retention × compaction experiment, or checkpoint N+1 evaluation was performed.
The sibling Inkling-Small-Ampere repository was not modified.

The 240,000-token Inkling value remains transport/exact-retrieval evidence only. This slice adds no
benchmark-reproduction, long-horizon reasoning, quantization-fidelity, router-stability, teaching,
or training result.
