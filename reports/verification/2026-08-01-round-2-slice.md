# Round 2 implementation slice verification — 2026-08-01

## Scope

This report covers the first non-Magellan Round 2 slice: research roles, domain registration,
external dotenv loading, GCS artifacts, Lean mathematics corpus/verifier integration, and the
initial hard-gate/reward contracts. It is a software-verification report, not an Inkling empirical
result.

No OpenAI or Anthropic generation request was made. The user-authorized external secret file was
used only to confirm that the expected variable names exist; no value or local path was copied,
printed, hashed, or committed. Magellan was not accessed.

## Hermetic gate

`make check` passed:

- Ruff format and lint: passed;
- generated JSON Schemas: 17 matched contracts;
- strict mypy: 77 source files passed;
- local tests: 102 passed, 6 explicitly deselected external/platform gates.

The local suite includes role propagation and baseline memory exclusion, algebra regression,
domain registry and hard-gate invariants, dotenv precedence/permission behavior, GCS fake-client
deduplication/corruption/interruption/GC behavior, and Lean input-policy/environment classification.

## GCS gate

The credentialed GCS integration test passed against the user-authorized existing bucket:

- two concurrent create-only writes converged on the same content address;
- generation, CRC32C, ETag, restricted/raw classification, and byte-level SHA-256 readback were
  verified;
- the test used a unique object prefix;
- its object was generation-conditionally deleted in `finally`;
- result: `1 passed`.

The implementation uses explicit generation-conditional retry policies. A response lost after a
successful create therefore converges through precondition failure plus immutable-object
validation, not an overwrite.

## Lean gate

The committed environment pins:

- Lean `4.32.2`;
- Mathlib `v4.32.2`;
- Mathlib revision `905b95818eb32af7874a58b427f50c1711a5e96c`.

The full Mathlib cache was downloaded into ignored workspace storage and `lake build` completed
successfully with 8,656 jobs. The real integration test then ran outside Codex's outer sandbox so
Padawan's own macOS sandbox could apply:

- one generated integer-linear-equation proof was kernel-verified;
- one generated natural-number-bound proof was kernel-verified;
- an invalid `rfl` proof was kernel-rejected with a structured diagnostic;
- the verifier fingerprinted the Lean executable and resolved `.olean` dependency closure;
- network isolation and temporary-directory-only writes were enforced;
- result: `1 passed`.

macOS rejected lowering `RLIMIT_AS`; verifier evidence records
`memory_limit_enforced=false` rather than claiming a nonexistent memory ceiling. Wall/CPU/output
and file-descriptor limits remain enforced.

## Honest boundaries

- Algebra remains the only complete autonomous developmental workflow.
- Lean has real corpus generation and verification, but no autonomous teaching/transfer workflow.
- Reward and eligibility contracts exist; durable reward recomputation/compiler work is pending.
- Appellate briefing remains planned.
- Magellan remains explicitly deferred.
- Inkling serving remains unavailable, so there is no target-student pilot result.
