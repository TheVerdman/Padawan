# R2.4/R2.6 Verification: Domain Developmental Workflows

**Date:** 2026-08-08
**Scope:** Lean mathematics workflow, appellate workflow/adjudicator execution, Responses strict
schema transport, and citator-currentness design boundary

## Implemented result

Padawan now has two durable developmental state-machine paths. The existing 20-transition algebra
path is unchanged. A new 10-transition domain path owns matched three-sibling leasing, symmetric
state forks, exposure before external I/O, cold grading, validated teaching, revision, matched
treatment/control transfer, memory decision, retirement, provenance, and final episode commitment.
Domain authorities own request rendering, output decoding, and grading.

`math.lean` binds the shared path to the pinned Lean verifier. Only a narrow tactic proof is accepted
as student content, and verified, rejected, unknown, and infrastructure outcomes remain distinct.

`legal.appellate.fourth_circuit` binds student content to a system-created submission identity.
Deterministic court-pack, task, rule, record, citation, quotation, and leakage gates execute before
the semantic adjudicator. The adjudicator receives only evidence attached to each claim under a
strict typed draft contract; Padawan binds model, provider, role, submission digest, and assessment
identity and re-runs the verifier. Currentness is excluded from this authority.

The official OpenAI and OpenAI-compatible Responses transport now normalizes Pydantic output
schemas to the strict Structured Outputs subset by requiring every object property, forbidding
additional properties, and removing defaults from the transmitted copy. It does not mutate the
durable source schema. The official OpenAI client remains `/v1/responses` only.

## Regression evidence

`make check` passed:

- Ruff formatting: 191 files formatted;
- Ruff lint: passed;
- generated contracts: 60 schemas matched their Pydantic models;
- mypy: 110 source files passed; and
- hermetic suite: 163 passed, with 6 PostgreSQL/live/Lean tests deselected by the default gate.

The separately selected pinned Lean integration passed: 1 real-kernel test passed with 168 tests
deselected. The first attempt inside the outer workspace sandbox correctly returned infrastructure
failure because macOS `sandbox-exec` could not apply a nested policy. Re-running outside that outer
sandbox allowed Lean's own required network-denied/write-confined policy to apply; reference proofs
were accepted and an invalid proof was rejected.

The new system regressions establish that:

- a Lean run selects only Lean inventory even when an appellate group sorts earlier in the same
  registry, makes four student calls and one teacher call, and commits verifier evidence;
- an appellate run makes four student calls, one teacher call, and exactly two adjudicator calls for
  its two hard-gate-valid submissions; the two hard-gate-invalid submissions never reach the
  adjudicator;
- unbound adjudicator evidence is rejected, fed back under a new durable request ID, and fails after
  the configured retry budget; and
- agentic sibling groups with intentionally absent fixed answers remain active only when their
  verifier task identities are distinct, while mixed or duplicate fixed-answer groups retain the
  quarantine policy.

`PADAWAN_TEST_POSTGRES_URL` was not configured, so no new PostgreSQL-marked result is claimed. No
OpenAI, Anthropic, Inkling, GCS, citator, Magellan, or trainer call was made for this checkpoint.
Provider keys were neither read into source files nor committed.

## Citator boundary

The first court pack still declares no dependable citator and therefore continues to produce
`unknown_without_citator`. ADR 0011 accepts two future routes: a licensed API whose contract permits
automated access and internal evidence retention, or a governed human import of an authorized
report/export. Either route must bind the court pack and authority digests, retain auditable query
and treatment evidence, satisfy cutoff/freshness policy, and cover every cited authority exactly.
Negative treatment rejects currentness lexicographically; stale, partial, ambiguous, missing, or
unauditable results remain unknown. Student, teacher, and adjudicator assertions cannot substitute.

## Remaining external gates

- operational Inkling-Small Responses endpoint and a real matched target study;
- selected citator/provider entitlement and retention review, followed by a new court-pack version
  and provider adapter or governed-import service;
- dedicated PostgreSQL concurrency run in a configured test database;
- external offline training and checkpoint N+1 evaluation; and
- deferred Magellan runtime retuning and handshake acceptance.
