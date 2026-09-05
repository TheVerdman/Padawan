# PPRL execution plan and evidence ledger

Status: active engineering work; no live institutional or parameter-learning result.

## Mission and source of authority

The user activated the institutional-learning goal on 2026-09-04. The objective is a rigorously
validated persistent research institution and its first authorized, preregistered learning cycle.
The complete research thesis remains in `pprl-epsilon-charity-program.md`; architecture and
dependencies remain in `pprl-four-fabric-architecture.md`. This ledger tracks execution and evidence,
not a replacement research scope.

The starting checkout was clean at `9d58ceb4ad5cb0ceac77c2ddf30e0ef83c5e69d0`. Work is on
`codex/pprl-information-boundary`. Root `AGENTS.md` and every required document were read in the
specified order before implementation.

Authorized: local Padawan code/documentation, proportional offline tests, disposable simulated-worker
fixtures, isolated branches/worktrees, and reviewable local commits. Preserve unrelated work and
check for secrets before commits. Sibling repositories are read-only context.

Not authorized: delegation, sibling source edits, model training, live experimental workers,
cloud accelerator activation, spending, deployment, publication, or broader network, tool, data, or credential
authority. Actual inference remains gated except for the local Nemotron testing authorized below.
Prepare concrete manifests, budgets, evidence criteria, stop conditions, and cleanup before
requesting additional authority. Continue independent authorized engineering when a later execution
gate is unavailable.

Additional user decision, 2026-09-04: local Nemotron 3.5 Lightning tests are authorized when needed,
including staged stress testing with the requirement to avoid a kernel panic. Use the current
Nemotron-Metal-Lab gates, conservative host-health bounds, and explicit stop conditions. This does
not authorize training, cloud activation, or sibling source edits. At the point an `a2-ultragpu-4g`
campaign is needed, stop before activation, present its concrete manifest and cost ceiling in a
question box, and wait for the user's decision. If clearing swap requires a reboot, commit the work
and retain a resume point before asking the user to reboot; never reboot automatically.

## Requirements and status

| Requirement | Current state | Evidence needed for completion |
| --- | --- | --- |
| Information boundaries and retention | In progress, stage 1 below | Adversarial ingress/read/export/projection tests, authoritative classification, reviewed admission, transactional retention |
| Containment, identity, causal tracing, budgets | Existing control-plane contracts; enforcement gaps remain | Exact workload/request/effect binding, independent enforcement, conserved reservations, explicit unknown effects |
| Recovery and replacement | Committed state/leases exist; executable lifecycle absent | Crash/retry/fencing/reconciliation and exact hydration, including 100% roster replacement |
| Communication, tracking, escalation | Declarative state and roles | Explicit delivery/read authority, causal replay, durable live views, bounded dispatch and escalation |
| Atlas and mechanistic integration | Worker Atlas and separate runtime laboratories | Institutional subjects; versioned interchange; matched identities; protected forensic evidence and causal controls |
| Long-horizon institutional science | Declared distributions and outcomes | Preregistered independent replicates, bounded-regret estimators, matched baselines, resumable 10M+ token validation |
| Parameter learning and promotion | Offline compiler and external-checkpoint registry | Admitted worker-decision projection, selected trainer, real update, exported-runtime validation and independent evaluation |
| First research cycle | Not executed or authorized | Completed preregistered institutional cycle and actual candidate evaluation, with negative/null outcomes preserved |

Local Nemotron, optional Inkling coordination, and selective Astra/Sol strategic consultation are
candidate arrangements to evaluate. Routing may bypass an intermediate model. The General is also
replaceable. Owned-hardware operation and bounded paid-compute campaigns are design requirements;
model suitability, hierarchy benefits, emergence, epsilon-charity, and learning gains are hypotheses.

Optimize verified progress per dollar and elapsed time within primary objectives and local-regret
constraints. Distinguish aggregate output, input processing, sequential dependency depth, context
length, and eligible training tokens. Billion-token research remains a longer-term ambition with
separate resource and evidence gates; do not silently replace the explicit 10M+ milestone.

## Dependency sequence

1. Executable information classes, process/forensic separation, reviewed admission, projections,
   authoritative artifact reads, and retention ownership.
2. Actual containment and identities, exact request/runtime/effect binding, causal reconstruction,
   and resource conservation.
3. Assignment ownership, worker lifecycle, recovery, reconciliation, allowlisted hydration, and
   complete replacement.
4. Governed messages/delegation, deterministic institutional tracking, budgeted scheduling, and
   evidence-based escalation.
5. Atlas worker/institution comparisons and explicit Atlas–Inkling schema/digest interchange, with
   mechanistic access and intervention authority kept separate.
6. Outcome/regret estimators, preregistered distributions and controls, progressively scaled pilots,
   and long-horizon validation.
7. Attributable training examples, trainer integration, actual candidate parameters/adapters,
   independent evaluation, promotion, and separately authorized deployment.

An offline interface test never establishes OS isolation, loaded-model identity, hardware safety,
live replacement, or scientific improvement. Testing and implementation of interfaces can proceed
locally while their external execution gates remain explicitly unverified.

## Stage 1: offline information boundary

Boundary: the trusted local broker, artifact backend/catalog, process admission, and offline compiler.
The broker, database administrator, and broker-owned filesystem are trusted for this stage. Inputs,
references, nested payloads, and persisted legacy records may be malformed or adversarial. Arbitrary
worker code and direct access to broker storage are outside this stage's claim.

Invariants:

- Stored authority, not a caller's flags, governs artifact classification. Missing or conflicting
  classification fails closed; an ordinary write/read cannot reclassify legacy material.
- Raw traffic, private reasoning, security/environment traces, and mechanistic content or references
  cannot enter worker-visible state or training projections automatically.
- Reviewed derivatives retain provenance, sensitivity, contamination scope, and admission authority
  in the proper access domain. Retention does not grant institutional use.
- Initial state, transitions, forks, nested fields, exports, and training projections all enforce
  the same boundary. Unknown extensions and legacy content receive no implicit admission.
- Successful process admission pins all retained dependencies in the same database transaction.
  Failed admission does not advance state or leave a falsely complete evidence record.

Implementation checkpoints (each is part of stage 1, not completion of the stage):

1. Completed offline: persist and enforce local artifact metadata; reject caller relabelling and
   raw-only default reads/exports; preserve classification across restarts, duplicate writes,
   failures, and GC. See `artifact-classification-boundary.md` for the trusted-broker assumption.
2. Completed offline: explicit process/forensic classifications and reviewed
   evidence admission with durable provenance, separate worker references, scoped reads, and
   transactional admission ownership. The current forensic-origin adapter binds completed PPRL
   model I/O in the exact execution. See `process-evidence-admission.md` for limits and non-goals.
3. Enforce process ingress and retention ownership at initial state, transitions, forks, and reads;
   define a narrow model result and allowlisted worker projection.
4. Project training and Atlas records through the same information boundary, preserving privileged
   source joins separately; reject legacy/unclassified evidence for new worker/training admission.
5. Verify the complete boundary, update schemas/migrations and canonical claims, and retain evidence.

Acceptance cases include forged/relabelled references; unknown/missing classification; conflicting
duplicate writes; nested forensic references; unauthorized extensions; cross-domain reads;
unreviewed derivatives; raw-only export; deterministic safe projections; training exclusion;
retention under GC; crash/transaction rollback; and incomplete forensic writes.

Retain source/test identities, exact commands and outcomes, synthetic adversarial inputs, expected
decisions, projected bytes/digests, privileged provenance joins, ownership/GC assertions, and failure
injection evidence. Real restricted data does not enter Git.

Failure and rollback: deny before state advancement, preserve immutable history and privileged
failure evidence, and disable new admissions if classification or evidence integrity fails. A
rollback must not restore permissive reads or silently label legacy data safe. Keep orphaned or
interrupted publication distinguishable from an admitted object.

Non-goals of this stage: live workers, scheduler/mailbox execution, actual sandbox attestation,
model/GPU calls, real mechanistic capture, regret estimation, live graders, training, and scientific
improvement claims. These remain required later work, not reductions in program scope.

## Verified findings and evidence

- 2026-09-04: starting HEAD and clean worktree verified. The starting code reproduces the audit's structural
  local-read gap: `LocalArtifactStore.read_bytes` trusts `reference.restricted`, and `ExportPolicy`
  classifies raw-only artifacts as restricted while checking only the restricted flag for permission.
  `ArtifactCatalog.register` returns an existing catalog row without revalidating backend metadata.
- Baseline command: `PYTHONPATH=. .venv/bin/pytest -q tests/unit/test_artifacts.py
  tests/unit/test_gcs_artifacts.py tests/integration/test_heirloom_export.py
  tests/integration/test_pprl_store.py tests/integration/test_pprl_generation.py
  tests/integration/test_pprl_training.py tests/system/test_pprl_coordinator.py
  -m 'not postgres and not live and not lean and not gcs'`.
  Result before code changes: 15 passed, 3 failed in 1.76 seconds. Failures were the generation
  integration case and two coordinator cases: a fixed 2026-08-23 fixture with one-day authorization
  was tested against the real 2026-09-04 clock. The runtime correctly withheld the expired work.
  Add an explicit, scoped fixture clock; do not lengthen or bypass production authority.
- With the scoped fixture clock and unchanged production code, the same baseline passed: 18 passed
  in 1.76 seconds. The one-day authorization and expiry checks remain unchanged.
- Before artifact fixes, `PYTHONPATH=. .venv/bin/pytest -q
  tests/unit/test_artifact_classification.py tests/unit/test_gcs_artifacts.py --tb=line` reproduced
  all 19 new adversarial failures; 6 existing cases passed. These are synthetic offline probes.
- After the storage fixes, the expanded artifact/governance/export/PPRL selection passed 42 tests in
  2.04 seconds. Additional metadata-publication/missing-field cases were added before the final sweep
  below; stage 1 is not complete.
- First full offline sweep: 316 passed, 1 failed, 6 deselected in 21.63 seconds. The additional failure
  was the CLI Amber lifecycle fixture using the same expired August envelope. Apply the scoped test
  clock to that case too; no production expiry behavior changes.
- The CLI exercised two implicit lifecycle timestamps, exposing a fixed-clock fixture limitation:
  the next sweep had 316 passed, 1 failed, 6 deselected in 21.77 seconds because equal timestamps
  correctly violated monotonic lifecycle ordering. The fixture now advances deterministically by
  one microsecond per observation; real expiry and ordering checks remain intact.
- Final full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **317 passed, 6 deselected in 21.52 seconds**. Synthetic fake-GCS cases are included;
  PostgreSQL, live service/model, Lean, and real GCS validation are excluded.
- `.venv/bin/ruff check .` passed; `.venv/bin/ruff format --check .` reported 286 files formatted;
  `PYTHONPATH=. .venv/bin/python scripts/generate_schemas.py --check` verified 122 schemas;
  `.venv/bin/mypy padawan` passed for 156 source files; `git diff --check` passed.
- The checkpoint's adversarial inputs and failure injections are retained in
  `tests/unit/test_artifact_classification.py` and `tests/unit/test_gcs_artifacts.py`. No model,
  live worker, cloud resource, production artifact migration, or external publication was used.
- A local credential-pattern scan of the 12 changed/new nonignored files found no findings; no
  `.env`, model-weight, or private-key files are tracked, and `.env` remains ignored. This is a
  checkpoint change scan, not a new whole-history secret audit.

Checkpoint 2, 2026-09-04:

- Committed locally as `11e9651` (`feat: add scoped process evidence admission`).
- Added `artifact_information` and `process_evidence_admissions`, six versioned contracts, and
  trusted-broker classification/admission/read APIs. Review receipts retain the exact policy,
  actual admission time, and Amber sequence. PPRL generation classifies its raw request/response.
- Candidate classification alone grants no process use. A separate reviewed receipt binds exact
  candidate bytes, scope, rights, and forensic sources. Worker references exclude privileged
  source/review identifiers; reads revalidate current authority and ownership.
- A first targeted selection passed 17 tests in 2.23 seconds. Additional rights/scope/retention
  regressions and migration checks passed 24 tests in 4.45 seconds. After admission timestamps were
  bound into receipts, that selection passed 24 tests in 4.48 seconds. These are intermediate
  results; provenance-quota/time-boundary cases and the final full sweep follow.
- New invariants are tested against synthetic model-call traces, without real inference. The
  source adapter does not yet bind MI/tool/security/environment records. No source completeness,
  authenticated reviewer identity, semantic-redaction guarantee, complete hydration firewall, or
  coordinated concurrent-GC guarantee is claimed.
- The first full offline sweep after provenance quotas passed 341 tests with 6 deselected in
  23.50 seconds. Adversarial review then found that a malformed privileged receipt could echo
  forensic values through a Pydantic validation exception. A new synthetic-marker regression
  reproduced the leak (1 failed in 0.41 seconds). Worker-facing reads now return one denial class
  and message, with no private exception cause or context; the 25 admission tests passed in
  2.89 seconds after that fix.
- Final full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **342 passed, 6 deselected in 23.89 seconds**, including migration schema matching and
  all-revision upgrade/downgrade. Ruff passed; formatting checked 292 files; all 128 schemas match;
  mypy passed for 159 source files; `git diff --check` passed. Live, PostgreSQL, Lean, and real GCS
  checks remain excluded. No real model inference was used.
- A local credential-pattern scan of all 18 changed/new nonignored files found no findings before
  staging. No credentials, model weights, or real restricted traces were added.

Checkpoint 3, model-result portion, 2026-09-04:

- `ProcessGenerationResult` now contains only broker invocation identity and normalized public
  text/integer token counts. It no longer exposes the complete generation or raw artifact references.
  Privileged invocation/call records retain request/response joins. Successfully finalized model I/O
  remains classified and pinned before return.
- New event commits reject top-level raw references and must bind an invocation when one exists for
  the admission. They verify completed invocation identity, chronology, forensic classification,
  catalog identity, and ownership, and count retained source bytes against the artifact reservation.
  Omitting the invocation cannot evade this accounting. Detailed limitations and rollback are in
  `pprl-worker-output-boundary.md`.
- Worker-facing generation errors and evidence-read cancellations contain no private provider or
  validation payload, including exception cause/context. Cancellation still propagates as cancellation.
  Unknown, Boolean, and noninteger token usage is rejected rather than counted as zero.
- Targeted PPRL generation/admission tests passed 36 tests in 4.43 seconds before the cancellation
  cases. The first full sweep passed 346 tests with 6 deselected in 23.88 seconds. Final full command
  after cancellation coverage: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **348 passed, 6 deselected in 24.01 seconds**. Ruff passed, formatting checked 293 files,
  all 129 schemas match, mypy passed for 159 source files, and `git diff --check` passed.
- Tests use synthetic provider/private-reasoning/telemetry fields and failure injection only. No real
  model inference, worker activation, cloud use, or sibling edits were needed. Initial state, nested
  payloads, generic references, legacy projections, and coordinated retention/GC remain unfinished.

Checkpoint 3, reference/ownership portion, 2026-09-04:

Boundary and threat assumptions are recorded in `pprl-reference-ingress-boundary.md`. The broker,
database, and owned backend remain trusted; references and old records may be malformed. This portion
does not authenticate principals or validate nested/free-text content, hydration, training, or GC.

- The model-result portion was committed locally as `fe6761e`. Its exact 15-file staged scan found
  no credential patterns. Reviewed-reference ingress now preserves legacy parsing for privileged
  replay while requiring admitted `ProcessArtifactRef` values in new initial/event artifact fields.
  Claims and relevant retries validate admission and state/event ownership. Missing boundary
  configuration denies reference use; old flags cannot grant authority.
- Candidate and private source ownership is pinned with state/event mutations in savepoints.
  Multi-child fork failure rolls back new executions, earlier children, parent advancement, and pins.
  Cross-execution evidence transfer is not implicit. A rollback must preserve history and disable
  consumers; it must not restore permissive legacy ingress or weaken transaction guarantees.
- First new regression selection: **12 passed, 2 failed in 1.97 seconds**. One was a fixture error
  (Amber requires the cumulative, not incremental, artifact projection). The other was a real
  SQLite failure: a successful first savepoint survived outer rollback, leaving two rollouts/states
  where one was expected. SQLite legacy transaction control had not begun the outer transaction.
- `Database` now delegates SQLite `BEGIN` to SQLAlchemy for reads and savepoints too. Tests also
  verify that an outer rollback removes a successful evidence admission and all its pins. The
  earlier checkpoint-2 savepoint tests had not covered this driver-level failure mode.
- After the corrections, the reference-ingress/admission/generation/store/coordinator selection
  passed **52 tests in 5.73 seconds**. Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **363 passed, 6 deselected in 25.17 seconds**. Ruff passed; formatting checked 296 files;
  all 129 schemas match; mypy passed for 159 source files; `git diff --check` passed.
- Retained fixtures cover raw/unclassified/unreviewed/forged initial references, missing configured
  admission, initial source ownership, unsafe initial retries, state/event pin failures, missing
  ownership at claim, second-child scope failure, historical JSON/digest replay, forged event
  references, and outer rollback. No real inference, cloud use, sibling edit, or production migration.

Checkpoint 3, structural content portion, 2026-09-04–05:

Reference/ownership was committed locally as `b806b2f`; its exact 18-file staged credential scan
had no findings. The content portion now implements closed core shapes, explicit versioned extension
schemas, admitted process IDs for memory/evidence links, and immutable policy receipts bound to exact
stored state/event digests and execution. Missing receipts deny new use without rewriting historical
envelopes. Schema configuration is broker-owned; workers cannot register schemas or change policy.
No runtime identity or semantic-redaction attestation follows from this structural validation.

- New core event payloads use public `summary`/`plan` fields or a registered envelope. Fork metadata
  keeps explicit control/treatment identities and supports registered intervention schemas. Existing
  offline fixtures were adapted to the new public shapes without admitting old historical records.
- `process_content_admissions`, migration `a84e61c39d20`, three generated contracts, and trusted
  registry/validation/receipt APIs retain exact declared policy and source identity. The migration
  has no automatic legacy backfill. Source/execution mismatches, missing/corrupt receipts, policy
  drift, and rollback after a partial receipt write are checked.
- State/event validation bounds structural traversal and serialized content, checks keys and values,
  rejects known private/unclassified storage and undeclared candidate references through bounded
  indexed digest lookups, and preserves legitimate unrelated scientific hashes. Extension schemas
  must be closed, locally defined, and tied to their state/event/intervention surface. A serializer
  may not silently add different content. Returning a policy copy cannot mutate the active registry.
- Initial integrated PPRL selection: **53 passed in 6.53 seconds**. The new content-specific selection
  then passed **25 tests in 2.34 seconds**. After surface-binding/serializer cases and stronger source
  checks, the expanded content/reference/admission/generation/store/coordinator/training selection
  passed **80 tests in 8.65 seconds**.
- Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **390 passed, 6 deselected in 27.84 seconds**. Ruff passed; formatting checked 300 files;
  all 132 schemas match; mypy passed for 161 source files; `git diff --check` passed. Migration schema
  matching and disposable all-revision upgrade/downgrade are included; PostgreSQL/live/Lean/real GCS
  remain excluded. No actual model inference, cloud use, sibling edit, or production migration.
- Threat assumptions, exact payload shapes, limits, retained fixtures, and rollback are in
  `pprl-content-admission.md`. Structural checks do not prove prose provenance or remove encoded
  channels. Runtime/code identity attestation and matched-policy experimental interpretation remain
  later gates. Rollback disables consumers and preserves history/receipts instead of adding approval.

## Next executable step

Continue checkpoint 3 with narrow worker observations and then checkpoint 4 training/Atlas
projections. Source state/event records and their content receipts remain broker records, not a
prompt interface. Preserve privileged historical parsing/digests; legacy records gain no automatic
worker/training approval. The compiler still serializes complete events into trajectory rows.
Exact observation receipts, projection use/rights, worker/control metadata separation, and coordinated
GC remain unfinished. These local foundation changes require no model testing or cloud activation.

## Completion audit

The goal remains active until foundational gates and an authorized preregistered institutional
learning cycle are supported by retained evidence, including a real candidate update and independent
evaluation. Keep incomplete long-horizon and external-execution requirements explicit. A credible
null or regression is a valid research result; compilation or simulated workers alone are not.
