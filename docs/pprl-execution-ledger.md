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

Direct user confirmation, 2026-09-05: the user answered "Approved" to continuing local
implementation, offline tests, and local commits, including the tested Atlas evidence checkpoint.
This resolves the staging-authorization block below. The separate resource and authority gates
above still apply.

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

Checkpoint 3, worker observation portion, 2026-09-05:

Previous goal turn: verified progress, ending at `096e8af` with a clean worktree and 390 passing
offline tests. The exact 24-file staged credential scan had no findings.

The bounded slice implements an explicit worker-observation contract and broker service. Project only the
allowlisted public state fields; keep state/event lineage, lease credentials, authorization details,
content receipts, and forensic ownership in a separate privileged receipt. Retain the exact canonical
UTF-8 bytes and digest delivered to the planner, bound to the source state, content/projection policies,
declared lease owner, current Amber sequence, execution, and research/retention rights. Revalidate
current scope and retention before delivery. Bind proposal admission/denial to its observation without
changing historical Amber request hashes. Split planner input from trusted effect execution in the
coordinator; a Python protocol is not an execution sandbox.

Acceptance: deterministic public bytes across fresh workers, exact receipt reconstruction, no private
headers or forensic identifiers in planner input, stale/wrong-owner/paused/expired scope rejection,
unknown legacy/receipt/policy rejection, independent observation ownership, caught-error rollback,
immutable/idempotent delivery and decision bindings, and planner mutations that cannot alter broker
state or the retained observation. Preserve negative admission evidence. Record synthetic fixtures,
schema/source/receipt identities, exact checks, failure cases, and limitations. Failure returns no
observation and leaves authority/head/ownership intact. Rollback disables consumers, preserving prior
records and receipts rather than restoring full-state prompts.

Threat assumptions: trusted broker, database, backend, schema configuration, and adapter composition;
malformed input, stale workers, and forged records/references are in scope. Non-goals: authenticated
runtime identity, a complete action mask, actual prompt/provider-request binding, scheduler/worker
launch, scientific proof of 100% replacement continuity, training permission, role-specific access
policies, sandbox attestation, or coordinated GC. These remain required later work, not exclusions
from the full goal. No real model test is needed for this slice.

- `ProcessWorkerObservation` explicitly projects the eleven approved state fields; full broker
  state, lease credentials, admission records, and forensic source identifiers stay in the privileged
  plane. Five generated contracts, two immutable tables, and migration `b7f418d6a0c5` retain the
  canonical observation bytes, policy/source/rights/lease-owner/Amber context, and proposal bindings.
  There is no migration backfill or implicit admission of legacy records.
- `ProcessObservationStore` revalidates active scope, exact source/content admission and policy,
  rights, and independent observation ownership on delivery. Identical retries are idempotent;
  fresh worker/lease receipts can carry identical public bytes. Planner mutation cannot change
  retained bytes, source state, or the trusted executor's fresh observation.
- The coordinator and application composition now take separate planner and trusted executor
  interfaces. Initial observation failure rolls back the claim. Planning failure retains the
  observation and releases the lease. Review found that binding failure could otherwise erase
  Amber's decision in the same transaction: admission/denial now commits before binding, and an
  unbound decision cannot reach this executor. Both dispositions have failure-injection coverage.
- Initial coordinator/migration selection: **4 passed in 2.52 seconds**. The first new observation
  test run had **35 failed in 4.60 seconds** because its helper incorrectly expected lease owner/
  expiry on `ProcessRolloutRecord` and used an invalid event kind; these were fixture errors. After
  correction, **34 passed, 1 failed in 4.65 seconds** exposed one remaining wrong fixture owner.
  Correcting that and adding coordinator regressions gave **42 passed in 5.20 seconds**. A final
  post-receipt retention-failure case is included in the full sweep below.
- Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **431 passed, 6 deselected in 33.52 seconds**. Ruff passed; formatting checked 306 files;
  all 137 schemas match; mypy passed for 163 source files; `git diff --check` passed. Disposable
  migration schema matching and all-revision upgrade/downgrade are included. PostgreSQL, live,
  Lean, and real GCS validation remain excluded. No real inference, cloud use, sibling source
  edit, production migration, or external publication was used.
- `pprl-worker-observation-boundary.md` retains the threat assumptions, read/write paths, public
  versus privileged fields, failure behavior, acceptance evidence, and rollback. Canonical handoff
  and operations instructions now reflect the planner/executor split. Observation receipts are not
  actual provider-prompt/loaded-model attestation, role-specific action masks, live hydration,
  complete replacement/recovery evidence, or permission to train.
- The 24-file changed/new credential-pattern scan found two unchanged example-value matches in
  `docs/operations.md`. Both were checked against exact documented placeholders and HEAD; neither
  is a credential finding. No provider-token/private-key pattern, tracked `.env`/model-weight/key
  candidate, or new credential was found; `.env` remains ignored. This is a change-scope scan,
  not a new whole-history audit.

## Checkpoint 4: PPRL training projection

Current goal turn: verified engineering progress; the worker-observation portion of checkpoint 3
passes the full authorized offline checks. The full goal remains incomplete and active.

The observation portion was committed as `0b54532` with a clean worktree. Its exact 24-file staged
scan had no credential findings; two unchanged documentation placeholders were explicitly checked.

Checkpoint 4 implementation decision: preserve existing version-1 training bundles as privileged
research archives. Their full state/event/outcome and lineage rows are not model input. Add an
explicit PPRL projection step with separate public trajectory, verifiable, and fork-preference
payload contracts and private immutable receipts. Public JSONL will omit source IDs, authorization,
lease, forensic references, outcome evidence links, and archive metadata; private receipts will bind
row offsets/digests to the original archive, observations, source content, rights, and decisions.

The trusted broker will reconstruct the archive's PPRL products from its exact source snapshot,
validate current research/training/retention authority, require admitted state/event content and
observation bindings, and enforce training-use evidence admission. Task projection requires an
explicit closed schema bound to the generator identity. Numeric outcome labels are separate from
their privileged evidence. Apply replication minima again after projection exclusions, preserving
duplicate public examples and their distinct private sample lineage. Old bundles remain readable
for research but gain no implicit projection approval.

Persist bounded canonical public bytes in a private projection receipt, with atomic ownership for
the source archive and all admitted-evidence dependencies. Public reads return only those bytes
after current-policy/authority/integrity/retention checks. Missing or conflicting evidence fails
closed; empty/excluded results remain retained research evidence. These are institutional learning
payloads, not yet attributable worker parameter-training examples: real prompt/policy identity,
action masks, interference-aware credit, trainer integration, and execution authorization remain
later gates. Atlas projection paths remain the next part of checkpoint 4.

Validation and review, 2026-09-05:

- `ProcessTrainingProjectionStore` now implements the explicit broker step. Thirteen generated
  contracts and migration `c3e746d2a9f1` add bounded public payloads and one immutable private receipt
  table. Existing version-1 archives and hashes are unchanged. Public reads reconstruct source rows
  and require current authority, exact observations/content/bindings/rights, explicit task schemas,
  training-use evidence, and original plus independent projection retention.
- Review exposed a gap in the older archive verifier: its schema/hash/snapshot checks can accept a
  PPRL row whose content differs from its retained source. The new path compares the exact rebuilt
  source rows to archived bytes. A synthetic hash-valid/source-divergent archive test demonstrates
  the old verifier's limitation and the new reader's rejection; the original verifier is not
  described as a learning-admission API.
- Review also corrected the draft's distribution-only replication filtering and use of
  trajectory-lane labels in fork preferences. Minima now apply separately after every product's
  exclusions, requiring enough replicates for every admitted instance; fork preferences carry
  their exact selected outcomes. Equal public examples remain distinct privately attributable rows.
- Independent ownership retains every archive product, reviewed derivatives and all forensic
  sources, and direct completed-invocation traffic. Missing original or projection ownership denies
  reads. Reviewed training-purpose reads can follow pause/release approval when Amber permits;
  new admissions and process observations remain active-only. Authority-sequence changes require
  fresh projection receipts. No receipt grants parameter-training readiness.
- The early evidence/observation/migration selection had **68 passed, 1 failed in 9.92 seconds**:
  migration schema matching found a 192/128-character foreign-key width mismatch, which was fixed.
  Subsequent focused migration checks passed **2 tests in 1.96 seconds**.
- New fixture setup initially failed on omitted policy bounds, wrong enum names, an invalid fork
  intervention shape, releasing an already released fork lease, a protected receipt foreign key,
  release approval without its required envelope/independent reviewer, and positional construction
  of keyword-only simulated-call executors. These were corrected in the fixtures without weakening
  production validation. Intermediate selections included **21 passed, 3 failed in 9.89 seconds**,
  **24 passed in 10.44 seconds**, and **26 passed, 1 failed in 11.33 seconds**. Final projection
  selection: **33 passed in 14.42 seconds**.
- Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **464 passed, 6 deselected in 45.72 seconds**. Ruff passed; formatting checked 311 files;
  all 150 schemas match; mypy passed for 165 source files; `git diff --check` passed. Disposable
  migration schema matching and all-revision upgrade/downgrade are included. PostgreSQL, live,
  Lean, and real GCS validation remain excluded. Calls in the new tests use synthetic callbacks;
  no real inference, cloud/GPU use, sibling edit, production migration, or publication occurred.
- `pprl-training-projection-boundary.md` retains the exact stores, paths, policies, payload fields,
  limits, acceptance evidence, failures, rollback, threat assumptions, and non-goals. Fatal
  compilation failures publish no partial result; a durable operational attempt/failure ledger,
  semantic provenance, role-authenticated access, coordinated GC, actual prompt/behavior/action/credit
  attribution, and a trainer remain unfinished. These engineering results are not scientific evidence.
- The 27-file changed/new credential-pattern scan found no provider-token, private-key, or credential
  literal matches. No tracked `.env`, model-weight, or key-file candidate was found; `.env` remains
  ignored. Exact staged-blob comparison and the same credential rules are required before the local
  commit. This is a change-scope check, not a new whole-history audit.

### Checkpoint 4 continued: Atlas forensic retention prerequisite

Continuation audit: prior turn was verified progress. Current HEAD is `b2f3628`, initially clean;
its exact 27-file staged credential scan had no findings or working/staged byte mismatches.

Atlas implementation decision, recorded before editing: close forensic classification and retention
at trial request/result and exploratory-proposal writes before admitting any Atlas projection.
At the prior HEAD, `record_trial_request` checked preflight catalog metadata, `record_trial_result`
checked captured-call/verifier metadata, and `register_exploratory_proposal` checked protected trace
flags; none established independent forensic ownership. Keep metadata-only registry operations
available, but require an explicitly configured artifact boundary for these three source writes.
Validate actual backend identity/bytes, classify explicit source references as forensic, and pin
them atomically with the owning record. Retries
validate existing classification and exact ownership without silently repairing legacy evidence.
Retain source request/preflight dependencies with result ownership as well as their original owners.

This portion assumes a trusted broker, database, and configured backend. Test missing backend/blob/
classification/ownership, forged flags and nested explicit evidence references, idempotent retries,
independent owners, and caught-error/outer rollback using real disposable local storage. Keep raw
traffic and findings privileged; Atlas's existing `direct_compiler_ingestion_permitted: false` and
`direct_memory_write_permitted: false` remain enforced. Retain original source formats and digests,
classification records, exact owner sets, fixtures, and checks. Failure rejects the write or read;
rollback disables consumers while preserving evidence. No model input, corpus materialization,
training permission, live campaign, semantic redaction, runtime identity attestation, or coordinated
GC is added by this retention prerequisite. Those remain subsequent gates of the full objective.

Validation and review, 2026-09-05:

- `AtlasArtifactBoundary` now verifies physical source bytes, immutable forensic classification, and
  exact ownership. All three source writes have an outer savepoint, and retries validate rather
  than repair. Result ownership independently retains request preflights, captured request/response
  and envelope, grader artifacts, and nested explicit verifier artifact references. Reference count
  and declared-byte bounds are 64 and 64 MiB. Native source schemas and digests are unchanged;
  there is no migration, new table, or historical backfill.
- Adversarial review found failed-call requests were initially omitted because pre-response results
  have no generation envelope. Timeout/infrastructure results now retain their captured request and
  any explicitly named provider-error response. Review also added source timestamp/column checks,
  verifier identity, and the captured-output/verifier join on reuse. These are stored metadata and
  byte checks, not provider, consent, preflight, or verifier-execution attestation.
- New Atlas Study block admission and result sealing revalidate physical evidence through explicit
  backend composition. Missing backend, owner, or file prevents new sealing. Existing reporting and
  same-status completed-Study access are not fresh retention validation. Upstream extraction,
  cluster/probe/reproduction/eligibility, and other analysis/reporting paths still need use-boundary
  review before they can feed new consumers.
- An early boundary selection passed **16 tests and failed 1 in 2.89 seconds** because its expected
  exception families omitted the correct `FileNotFoundError` for a removed blob. The fixture was
  corrected to include `OSError`; production rejection was preserved. Subsequent boundary and Study
  selections passed **19 in 2.80 seconds** and **9 in 2.69 seconds**. The expanded combined Atlas
  selection passed **42 tests in 7.10 seconds** before the final captured-output drift case.
- Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **493 passed, 6 deselected in 50.14 seconds**. Ruff passed, formatting checked 314 files,
  all 150 generated schemas match, mypy passed for 166 source files, and `git diff --check` passed.
  Disposable migration schema matching and all-revision upgrade/downgrade are included. PostgreSQL,
  live, Lean, and real GCS validation remain excluded. No actual inference, training, cloud/GPU use,
  sibling edit, production migration, or publication occurred.
- [Atlas forensic retention](atlas-forensic-retention-boundary.md) retains the exact stores, sources,
  APIs, bounds, trust assumptions, acceptance evidence, failure behavior, rollback, and non-goals.
  The tests use synthetic call/verifier/preflight bodies in real disposable local storage. GC was
  checked with a captured reference snapshot, not against concurrent DB/GC races. Errors and
  inspection objects remain privileged; authenticated readers and complete forensic capture are
  not implemented by this checkpoint.
- `AtlasTrainingEligibilityRow` and `AtlasMemoryEligibilityRow` have registry writers but no direct
  compiler/memory consumers. Their fixed-false direct-ingestion/direct-write contracts remain.
  No Atlas source/admission adapter or governed corpus materialization was added; the PPRL projector
  cannot substitute for those boundaries. Scientific, long-horizon, and learning gates stay open.
- The 11-file changed/new credential-pattern scan found no provider-token, private-key, or credential
  literal matches. No tracked `.env`, key-file, or model-weight candidate was found; `.env` remains
  ignored. Exact staged-blob scanning and working/staged byte comparison are required before commit.
  This is a change-scope check, not a new whole-history audit.

### Checkpoint 4 continued: reviewed Atlas-to-process origins

Continuation audit: previous turn was verified progress. `ea41ba7` is committed and the checkout
was clean at this continuation; its exact 11-file staged scan had no credential matches or byte
mismatches. The full goal remains incomplete and active.

Implementation decision before editing: add an explicit reviewed Atlas-source origin to the
existing process-evidence broker. Preserve version-1 reviews and receipt bytes. A version-2 private
receipt will retain the unchanged candidate review plus exact Atlas source identities and a pinned
disclosure policy naming the target process execution, contamination scope, source scopes, source
rights, reviewers, validity period, and declared intervention. No default Atlas disclosure policy
will be installed. Validate this origin again on every admission, retry, read, and downstream
ownership operation. Never let a missing origin fall back to a version-1 PPRL source interpretation.

This slice admits only separately reviewed institutional evidence from development/adaptive Atlas
trials into train/adaptive-development process executions; adaptive sources require an adaptive
destination. Challenge/sealed sources and indexed item/prompt overlaps are excluded. Bind exact
request/result/context digests, all explicit retained source artifacts, source governance and
reviewed output rights, current target Amber authority, and separate candidate bytes. Reject literal
source identifiers in candidate content. Keep all raw references and review details private.
Atlas eligibility records remain unconsumed. This does not write developmental memory or satisfy
its episode requirement. Parameter-training use and Atlas corpus materialization remain gated;
these first Atlas derivatives are process-use-only, including when encountered by PPRL projection.

Threat assumptions remain a trusted broker/configuration, database, backend, clock, and declared
reviewers; malformed/stale/forged inputs and retained-state damage are in scope. Tests must cover
exact source/policy/target joins, missing and excess sources, raw/discoverable-reference leakage,
rights and partition isolation, original ownership loss, duplicate/downgraded receipts, read errors,
policy expiry/change, atomic rollback and downstream process ownership, with version-1 compatibility.
Retain native Atlas records/digests, candidate bytes/classifications, private origin and review,
source/target policy and rights, independent pins, fixtures, and validation output. Failure withholds
candidate bytes and publishes no partial receipt. Rollback disables new consumers while preserving
evidence. No actual inference, trainer, authenticated sandbox, semantic-redaction proof, causal
claim, source independence claim, or live/institutional Atlas campaign is part of this slice.

Validation and review, 2026-09-05:

- `AtlasEvidenceSourceBoundary` and `ProcessEvidenceStore.admit_atlas` implement the decision above.
  Five new contracts/schemas retain the exact source selection, disclosure policy and reviewed
  candidate join. Version-2 receipts occupy the existing immutable admission table; version-1
  schemas, review/receipt formats, and native Atlas source formats are unchanged. No migration,
  default disclosure policy, data backfill, or eligibility consumer was introduced.
- Source inspection revealed Atlas results have no native provider-output-rights receipt. The
  explicit disclosure policy therefore requires reviewed output rights per exact source scope,
  separately from dataset governance and candidate rights. Evaluation permission alone cannot
  establish retention/disclosure authority. These are declared reviews, not legal or identity
  attestation. Challenge/sealed item/prompt overlap blocks disclosure; any indexed adaptive overlap
  requires an adaptive destination.
- Exact context digests cover source manifests, indexed memberships, captured-call columns, and
  explicit artifact metadata. A regression test changes a captured provider-response ID while the
  prior retention validator still passes; the new origin reader rejects reuse of the old review.
  Two separately registered trial origins with identical artifact bytes retain distinct private
  source identities and a deduplicated artifact dependency set. That is no independence claim.
- The first new selection had **18 passed, 1 failed in 4.12 seconds**: the positive process-state
  fixture omitted its required artifact-byte budget. The fixture was corrected and uses the actual
  returned state ID. A subsequent expanded run had a collection error from a misplaced fixture
  import; it was moved to the import block. The combined selection then had **97 passed, 1 failed
  in 14.23 seconds** because an extra-source fixture classified its source after the original review;
  its review time was advanced to exercise the intended exact-source-set rejection. Production
  chronology checks were preserved. The expanded new selection passed **37 tests in 7.85 seconds**.
- Final added regressions cover source-context drift, version-1 canonical receipt compatibility,
  and trial-selection bounds. Full offline command: `PYTHONPATH=. .venv/bin/pytest -q
  -m 'not postgres and not live and not lean and not gcs' --tb=short`.
  Result: **534 passed, 6 deselected in 58.07 seconds**. Ruff passed; formatting checked 318 files;
  all 155 schemas match; mypy passed for 168 source files; `git diff --check` passed. Disposable
  migration schema matching and all-revision upgrade/downgrade are included. PostgreSQL, live,
  Lean, and real GCS validation remain excluded. No actual inference, training, cloud/GPU activation,
  sibling edit, production migration, or publication occurred.
- [Atlas process evidence](atlas-process-evidence-boundary.md) records exact paths, source joins,
  bounds, rights and contamination assumptions, failures, rollback, and retained evidence. Atomic
  checks cover caught pin/publication errors, cancellation, and outer rollback. New broker instances
  can read through persisted origin receipts; this is not a full worker-replacement experiment.
  Candidate semantics/source completeness still rely on review. Process-use-only references do not
  automatically decontaminate future outputs influenced by them. Authenticated identity, actual
  prompt/effect/credit attribution, independent forensic capture, coordinated GC, controlled
  evaluation-memory conditions, and Atlas training materialization remain later gates.
- The 20-file changed/new credential-pattern scan found no provider tokens, private keys, or
  credential literals. No tracked `.env`, key-file, or model-weight candidate was found; `.env`
  remains ignored. Exact staged-blob scanning and working/staged byte comparison are required
  before commit. This is a change-scope check, not a new whole-history audit.

Staging was rejected twice by automatic approval review because it did not accept the attached
goal's authorization over the original read-only instruction. No staging bypass was attempted.
Two read-only continuations preserved the checkpoint and inspected remaining consumer and dispatch
paths; after the same blocker persisted across three goal turns, the goal was marked blocked.
The direct user approval recorded above now permits the local implementation and commit work.
The exact 20-file staged-blob credential scan then passed with no findings or working/staged byte
mismatches; sensitive-filename checks and `.env` exclusion passed, as did `git diff --cached --check`.
Only this ledger changed since the recorded full test suite; no additional model or resource test
was needed to commit the checkpoint.

## Next executable step

Current turn was verified engineering progress on explicit Atlas institutional-evidence admission.
Commit this checkpoint after changed/new and exact staged-blob credential scans, then re-read Git
state and this ledger on continuation. The goal remains active and incomplete.

Audit the remaining stage-1 consumer call graph before declaring its gate complete: default PPRL
composition, compiler/export/reporting entry points, legacy record reads, and caller access to
privileged source/receipt objects. Identify actual worker/training paths that bypass the validated
interfaces, and distinguish privileged historical inspection from model input. Fix concrete bypasses
within the information boundary; do not build an automatic Atlas eligibility consumer. Keep Atlas
training materialization explicitly gated for its later attributable-learning path.

Then take the dependency-correct step into workload/identity/resource integrity: bind the actual
generation request and intended effect to admitted authority, preserve independent invocation
evidence, and define conserved reservations across attempts, forks, and replacement. Revalidate
those gaps in current code before choosing the implementation boundary. Define threat assumptions,
invariants, acceptance tests, retained evidence, failure and rollback before editing. Offline fake
providers/disposable workers remain available for validation; control-plane checks must never be
reported as runtime authentication or an attested sandbox.

Non-goals: live Atlas campaigns, sibling telemetry integration, a trainer, parameter updates,
permission to train, live workers, model stress tests, or cloud activation. Atlas institutional
subjects and causal MI interchange, conserved resources, authenticated execution, coordinated GC,
executable recovery, full replacement, and the 10M+ token milestone remain required later gates.
No additional resource authority is needed for this local boundary review.

## Completion audit

The goal remains active until foundational gates and an authorized preregistered institutional
learning cycle are supported by retained evidence, including a real candidate update and independent
evaluation. Keep incomplete long-horizon and external-execution requirements explicit. A credible
null or regression is a valid research result; compilation or simulated workers alone are not.
