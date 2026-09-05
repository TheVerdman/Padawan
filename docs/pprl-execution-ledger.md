# PPRL execution plan and evidence ledger

Status: local implementation resumed by the user; no live institutional or parameter-learning result.

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

Earlier user instruction, 2026-09-05: finish the current generation checkpoint, then pause the goal
and explain all changes since goal activation against the audit and full roadmap. Do not start the
next implementation slice until the user resumes. The current goal tool exposes no pause operation;
Computer Use explicitly refused access to the Codex app. An app-level pause therefore requires its
user-operated progress-row control. This ledger records the requested work hold, not a false claim
that the scheduler was paused, the goal was blocked, or its objective was completed.

Latest user decision, 2026-09-05: "No I un-paused manually, you may continue." This explicitly
resumes local implementation. The preceding repeated holds were an incorrect interpretation of the
manual unpause, not a withdrawal of the goal's engineering authority. The generation checkpoint is
committed as `0452cba`; the checkout was clean on resumption. Docker Desktop is available for local
validation if needed. Cloud/GPU campaigns still require the separately specified question-box
authorization; no new external authority was granted.

## Requirements and status

| Requirement | Current state | Evidence needed for completion |
| --- | --- | --- |
| Information boundaries and retention | In progress, stage 1 below | Adversarial ingress/read/export/projection tests, authoritative classification, reviewed admission, transactional retention |
| Containment, identity, causal tracing, budgets | Exact generation binding, shared accounting, local CPU tool containment, scoped worker credentials and one-action ownership | Credential delivery/isolation, loaded-model attestation, model-serving containment, complete capture/metering and external-effect recovery |
| Recovery and replacement | Reviewed lease fencing, unresolved-effect barriers and retained-result accounting; native simulated replacement preserves admitted observations | Domain resolution of uncommitted results, heartbeat/task/retry scheduling and scientific continuity through 100% roster replacement |
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

## Stage 2: exact generation workload binding

The Atlas checkpoint is committed as `e486696`; the checkout was clean afterwards. The direct
approval above resolves the local execution block. The app still reported the goal as blocked at
that check. On the later continuation `get_goal` reported active again; no workaround goal was
created and no goal-completion claim was made.

The read-only consumer audit found no automatic production PPRL archive-to-worker/trainer consumer.
`build_pprl_application` supplies no evidence policy and process references then fail closed.
CLI inspect/replay and the version-1 compiler remain privileged reconstruction/archive surfaces;
the explicit projection broker has no default learner caller. Heirloom exports developmental
episodes rather than PPRL state. Atlas eligibility rows remain unconsumed. This is a call-graph
finding under the trusted-broker assumption, not proof that arbitrary worker code is isolated.

Implementation decision before editing: first bind the actual model-input and transport boundary,
then implement conserved shared reservations. A configured generation policy must reconstruct the
request from an exact current observation and fixed reviewed instructions/sampling/schema. A new
private immutable workload receipt must join that request, the observation/decision, model and
configured transport identities, actual destination and prepared request-body bytes. Missing
composition, lineage or legacy admission must fail closed before provider I/O. The adapter must
send those prepared bytes, with no hidden fallback, redirect or retry in this path. PPRL retries
may reuse a persisted completed result; an unresolved prior dispatch cannot be sent again.

Threat assumptions: the broker, database, artifact backend, configured adapter implementation,
prompt-policy reviewer and HTTP transport are trusted. Malformed or substituted inputs, mutable
nested request fields, changed configuration, damaged receipts/pins, stale authority/leases,
duplicate dispatch and interruption are in scope. Declared runtime/checkpoint identities are not
loaded-model attestation. An in-process fake is not evidence of network or OS containment.

Acceptance: exact transmitted mock-HTTP body and destination; no calls for changed prompts,
metadata, schema, sampling, destination, model/configuration, stale authority or missing observation;
detached inputs under mutation; completed-response reuse; no duplicate send for a pending call;
canonical immutable receipts and original/independent artifact ownership; caught errors and outer
rollback leave no partial admission. Preserve private source errors, uncertain effects and artifacts.
Retain the generation policy, canonical normalized request, prepared transport body/configuration,
observation and decision joins, classified source bytes, dispatch/result logs and test evidence.

Failure withholds worker output and does not reinterpret legacy invocations. Uncertain external
effects remain unresolved, without automatic redispatch. Rollback disables the new dispatcher and
preserves receipts and pins; it must not restore unbound PPRL calls. Non-goals for this first binding
checkpoint: conserved account/reservation ledgers, authenticated workload identity or sandbox,
complete response/trace capture, executable replacement/reconciliation, role-specific action masks,
trainers, live models/workers, cloud activation, publication, or long-horizon scientific validation.
Those remain required subsequent parts of the full goal.

Implemented in this checkpoint:

- `ProcessGenerationPolicy`, `ProcessGenerationWorkload`, `PreparedGeneration`, explicit generation
  composition and migration `d8b541c9e2a6`. New invocations retain an exact workload digest; missing
  new lineage cannot fall back to legacy records. Reviewed instruction rights, exact current
  observation/decision, configured role/model/provider/destination and request byte bounds are
  checked before publishing private intent.
- The prepared OpenAI-compatible path sends canonical body bytes with explicit single dispatch,
  no compatibility fallback or redirect, and no inherited client cookies/auth/headers/query values.
  It rejects malformed/missing/coerced usage instead of turning it into zero. It retains available
  malformed response bytes privately. Transport, credentials and loaded-model identity are still
  trusted declarations, not attestation or independent resource measurement.
- Current lineage and authority are checked after external intent before dispatch and again before
  output admission. Admission time does not restart the action deadline. A pause or deadline while
  a call is in flight withholds output while retaining the completed external result if available.
- Completed replay preserves its original completion time and refuses to repair missing original
  ownership. Pending/failed/cancelled process effects never automatically resend. Ambiguous effects
  remain explicit in private call/invocation records; response-storage failure may leave a pending
  call requiring future reconciliation.
- Prepared source classification, original ownership, event artifact-byte accounting and independent
  training-projection retention now travel together. The prepared envelope and its references stay
  out of public worker output and learning JSONL. Projection readiness remains false.
- Mock HTTP and disposable SQLite tests cover substituted prompts/schema/sampling/metadata,
  configured identity/destination drift, fixed-policy rights/forensic identifiers/byte bounds,
  nested mutation, concurrent delivery, pauses, deadlines, cancellation, persistence failure,
  missing/corrupt source records, independent pins, outer rollback and immutable receipts. Existing
  observation tests now scope their receipt counts to the tested worker, because their synthetic
  model evidence itself requires an earlier observation. No acceptance invariant was removed.

The implementation, read/write map, trust assumptions, evidence limits and rollback are specified in
`pprl-generation-workload-boundary.md`. The canonical handoff and earlier boundary notes now distinguish
implemented offline observations, generation input and learning projections from the still-absent
live execution, recovery and trainer gates. Exact final validation and staged scan are recorded below
before the local checkpoint commit.

Final offline validation: `PYTHONPATH=. .venv/bin/pytest -q
-m 'not postgres and not live and not lean and not gcs' --tb=short` completed with
**604 passed, 6 deselected in 72.72 seconds**. Disposable SQLite migration/schema matching and
all-revision upgrade/downgrade are included. PostgreSQL, live service/model, Lean and real GCS
validation remain excluded. Ruff passed; formatting checked 326 files; mypy passed for 171 source
files; all 158 generated schemas match; `git diff --check` passed. Earlier full sweeps exposed stale
fixture receipt counts; a first test edit matched a different assertion and was corrected before
this final sweep. No production acceptance check was loosened.

The whole-goal checkpoint explanation is retained in `pprl-goal-checkpoint-review.md`. Only
documentation and the precommit scan record change after this validation. No real inference,
worker/model launch, cloud/GPU activation, trainer, sibling edit, production migration, push or
deployment was performed.

The 33-file changed/new credential-pattern scan passed with no findings. No tracked credential,
private-key or model-weight filename candidate was found; `.env` remains ignored. Exact staged
bytes then passed the same 33-file scan with no findings or working/staged mismatches;
`git diff --cached --check` also passed. This is a change-scope check, not a new whole-history audit.

## Stage 2: shared resource-accounting checkpoint

Implemented after the user's explicit manual resume, starting from clean `0452cba` on the same
branch. The ordered root reading list and actual admission, invocation, fork, retention and test
paths were refreshed. `pprl-resource-boundary.md` defines the pre-implementation boundary, units,
threat assumptions, invariants, tests, evidence, failure behavior and non-goals, now reconciled to
the resulting implementation.

- Five new tables retain explicit immutable funding, immutable reservations and hash-linked
  accounting events, with checked mutable account/phase heads. Migration `f6a8c2d4e913` has no
  implicit funding or legacy backfill. Rates, currency and named-review evidence are explicit.
- `AmberStore.admit` reserves capacity in the same transaction as admission. Every rollout and fork
  under an authorization competes for its account; alternate decision IDs, new worker identities
  and replacement do not replenish it. Root declarations charge once, and inherited fork budgets
  require the exact committed parent fork state.
- Dispatch records a started phase under current authority, lease, state and original deadline.
  Unknown effects retain capacity and the concurrent slot. Reviewed release is limited to unstarted
  reservations and races safely with dispatch. Generic completion requires an actual committed
  process event and charges its full allowance.
- Completed generation accounting reopens the exact original private request, response and prepared
  workload artifacts, verifies usage without coercion, applies the pinned rate, and independently
  retains its sources. Only evidenced token/cost differences release capacity. Known overages stop
  admission even when output fails; unrepresentable actual usage stops the account and preserves the
  unresolved hold and raw source. This is provider-reported logical accounting, not physical metering.
- Private operator commands fund, inspect/replay, release unstarted reservations and reconcile
  retained model results. No model adapter is composed by these commands. Funding/accounting
  references remain outside worker state and automatic learning projections.

Adversarial review added retained-event checks against forged completion, raw JSON hash checks
against coercive record substitution, no-refund behavior for malformed/coerced usage, independent
source-pin checks on reconciliation replay, and explicit overflow stops. Early validation exposed
SQLAlchemy multi-hop foreign-key type inference, a pytest duplicate module basename and formatting
issues; these were corrected before the final checks. Tests instantiate synthetic brokers and fake
model clients. Replacing every broker instance after lease expiry proves retained accounting only,
not continuity of a live institution through complete worker replacement.

Final validation on 2026-09-05:

- `make lint schemas`: 336 Python files formatted; Ruff passed; 163 schemas match their contracts.
- `.venv/bin/mypy --no-incremental padawan`: passed, 174 source files.
- `PYTHONPATH=. .venv/bin/pytest -q -m 'not postgres and not live and not lean' --tb=short`:
  **639 passed, 10 deselected in 84.58s**. Includes resource, CLI, migration, generation, coordinator,
  content, training-projection and existing offline regressions.
- `PADAWAN_TEST_POSTGRES_URL=<disposable-local-fixture> PYTHONPATH=. .venv/bin/pytest -q
  tests/integration/test_postgres_process_resources.py tests/integration/test_postgres_concurrency.py
  --tb=short`: **8 passed in 59.50s**. Four new cases cover competing admission at two capacities,
  repeated settlement and dispatch/release races; four existing PostgreSQL cases also pass.
- `git diff --check`: passed before the final documentation-only evidence update.

The PostgreSQL fixture used cached ARM64 image
`sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193`, with no image pull,
one CPU, 512 MiB memory and equal memory/swap limit, 128 PID limit, read-only root, tmpfs data and no
host mounts. The task-owned Docker network was internal-only. Its published port was unavailable,
so the tests used a localhost-only TCP-to-`docker exec -i ... busybox nc` relay rather than opening
external networking. These are fixture settings and SQL concurrency evidence, not sandbox attestation
for a model worker. Docker Desktop was started because its daemon was initially unavailable.

Cleanup verified: relay exited with `POSTGRES_TEST_RELAY_STOPPED`; the task-owned container and
network `padawan-resource-validation-01a06e62` were removed, and label-filtered inventories were
empty. Docker Desktop and preexisting images were left available. No model/worker launch, GPU use,
training, cloud activation, sibling edit, production migration, publication or deployment occurred.
Validation metadata, fixture relay source and secret-scan reports are retained locally under ignored
`runs/pprl-resource-validation-20260905/`. The cached ARM64 Gitleaks image
`sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` ran with networking
disabled and repository access read-only. All-local-ref history scan: **28 commits, 5,680,489 bytes,
zero findings**. The exact staged snapshot must also pass before committing; its source manifest
and redacted `staged.json` report are retained in that directory. No remote synchronization is part
of this local checkpoint.

## Stage 2: bound local CPU container checkpoint

Started from clean `7f64f9b62ac14111fa922683361f269636b7031e`. The goal objective, root instructions
and required documents were read in order. The pre-implementation audit found that the coordinator
still invoked trusted in-process executors and worker identity was a declaration. The selected
scope, invariants, evidence, threat model and rollback were written in
`pprl-container-execution-boundary.md` before implementing the local CPU tool path. There was no
delegation, model inference, training, cloud/GPU use, sibling edit, production migration or publication.

The new runner binds original observation, admitted role/model/tool, reviewed client/socket/daemon/
image/supervisor/command, funding and deadline before launch. Durable private intent precedes create;
resource dispatch precedes start. The command runs as UID/GID 65534 with no supplementary groups
or effective capabilities inside a fresh, bounded, network-disabled container. A container-local
PID-1 supervisor retains only set-UID/set-GID capabilities and independently terminates descendants
on deadline. Raw input, configuration, lifecycle, output, failure and cleanup records remain
forensic. The broker returns a private receipt; no automatic domain result, process-memory or
training admission was introduced.

Three tables retain immutable workloads, mutable phases and immutable receipts. Original sources
have separate workload/capture/result/receipt/accounting ownership. A fully retained terminal
receipt supports conservative accounting, including after pause/cancellation or an accounting-only
failure. Missing capture, truncation, unknown cleanup or receipt persistence failure preserves the
hold; the decision cannot launch again. Event commit revalidates the independent source pins.
PostgreSQL contenders execute once and reconciliation retries charge once.

Adversarial observations and corrections:

- The first real smoke probe failed closed before start because Docker 29 reports `CAP_SETUID` and
  `CAP_SETGID` in inspect data. The strict serializer expectation was corrected; the owned container
  was removed and the failed evidence retained.
- An additional probe found that `--network=none` still inherited the host resolver file. The runner
  now supplies loopback DNS, no search domain and fixed options, checks those effective settings,
  and tests the actual file contents. This closes an observed ambient host-configuration channel.
- Docker restart/init defaults are explicit and inspected. A bounded SIGKILL probe killed an owned
  broker process group, including its Docker client, after observing the unprivileged child active.
  The container subsequently exited with watchdog code 124 and was removed by the fixture observer.
- Live pause and cancellation tests retained complete terminal evidence and charged once; output
  overflow retained the truncated prefix and an unresolved reservation. No partial capture was
  relabeled complete. PostgreSQL's first invocation selected unavailable `asyncpg` and failed before
  tests; the recorded rerun used the repository's already installed `psycopg`, without installing
  dependencies or changing network authority. One initial lint issue in a fixture was also corrected.

Final validation, 2026-09-05:

- `make check`: **690 passed, 18 deselected in 90.97s**; Ruff passed on 347 Python files; all 167
  schemas match; mypy passed on 178 source files. The offline selection explicitly excludes Docker,
  PostgreSQL, live and Lean execution. SQLite upgrades from every revision and drift checks passed.
- `.venv/bin/mypy --no-incremental padawan`: passed on 178 source files.
- Explicit opt-in CPU Docker tests: **6 passed in 18.63s** after the resolver/default corrections.
  Checks include exact public stdin, UID/capabilities, denied privilege/supervisor signals, root
  writes, owned host-canary access, Docker socket, external network, executable scratch files,
  active peer scratch visibility, output bounds, pause, cancellation, broker death and cleanup.
- PostgreSQL concurrency/migration suite: **10 passed in 11.19s**. The two container cases were
  rerun after the last policy correction: **2 passed in 3.29s**. They cover four independent brokers
  competing for one launch and four independent reconciliation retries. The eight existing SQL
  resource/concurrency/migration cases were unchanged by that correction.
- `git diff --check`: passed. Exact staged bytes and all local Git refs must pass redacted secret
  scans before commit; scan reports, manifests and commit verification are retained with validation.

The CPU probes used cached Linux ARM64 Python image
`sha256:a3699f905b890636146817f204e73d9aa61329127b0c60e46310c44f9f0612b2`, Docker 29.7.2,
LinuxKit 7.0.12, 250 millicores, 128 MiB memory with equal swap limit, 32 PIDs, 8 MiB scratch and
5-second workload timers (6 seconds for the broker-death probe). Actual client hash, daemon identity,
reviewed fixture profiles, inspect records, raw output, receipts and broker SQLite snapshots are
retained under ignored `runs/pprl-container-validation-20260905/`; earlier/superseded probes and failed
checks remain there. `docker-ambient-fixed/` and its XML/log contain final container evidence.

PostgreSQL used the existing cached 17-alpine digest, one CPU, 512 MiB memory/equal swap limit,
128 PIDs, tmpfs data, no host mounts and `--network=none`. A temporary localhost-only stdio relay
provided test access. It exited with `POSTGRES_TEST_RELAY_STOPPED`; the exact owned PostgreSQL
container was removed; both its label-filtered inventory and the runner-container inventory were
empty. Docker Desktop and preexisting images remain available. This is local fixture validation,
not a claim about Metal, CUDA, loaded model identity or an institution's scientific competence.

## Stage 2 checkpoint: scoped worker capability and one-action ownership, 2026-09-05

The checkout began clean at `2f884ab`. `pprl-worker-identity-boundary.md` specified this boundary,
threat assumptions, invariants, tests, retained evidence, rollback and non-goals before code changes.
The preceding goal turn and this continuation are progress; the full learning objective is unfinished.

Implemented seven private SQL tables, ten schema exports, explicit pre-rollout enrollment, reviewed
256-bit capability issuance with digest-only retention, expiry/revocation, capacity limits and
one-action lease ownership. Protected observations, Amber admission/binding, dispatch, model/tool
execution, commit and release recheck exact worker/role/model/state/lease authority. New event actors
must equal their lease owners. The legacy coordinator cannot claim enrolled executions; enrolled
forks are denied pending explicit child-scope support. A populated identity ledger cannot be
downgraded to recover unauthenticated access.

The inert `ProcessWorkerBroker` accepts bounded claim/observe/propose requests, authenticates the
caller capability, retains sanitized request/observation/decision/reply lineage and independently
owns admitted observation evidence. It accepts already connected streams and launches no listener.
Assignment IDs are runtime control fields; model observations and learning content exclude credential
material and private identity references. Trusted direct APIs accept explicit proof and retain
assignment-to-admission lineage; the RPC wrapper adds its own source and retention records.

Adversarial review covered forged/missing credentials, audiences and assignments; role/model/actor
substitution; old-API bypass; concurrent current leases; replay after revocation or commit; backdated
expiry; missing decision bindings and evidence owners; malformed/oversize/timed-out/disconnected
streams; conservative history quotas; rollback after receipt failure; and enrolled-fork refusal.
A manually released and reviewed replacement incarnation receives identical observation bytes while
the old credential and assignment lose access. This is a primitive continuity fixture, not a
scheduled worker replacement or long-horizon institutional result.

Validation:

- Full offline suite: `PYTHONPATH=. .venv/bin/pytest -q -m 'not postgres and not live and not lean
  and not docker'` — **755 passed, 22 deselected in 116.85 seconds**. Includes native disposable
  client processes, SQLite migrations, guarded populated downgrade and synthetic model callbacks.
- Dedicated PostgreSQL fixture: `PYTHONPATH=. .venv/bin/pytest -q -m postgres` — **14 passed,
  760 deselected in 16.58 seconds** at that checkpoint. Four new cases cover competing claims across
  rollouts, duplicate proposals, revocation before dispatch and registration replacement capacity.
  Existing concurrency and PostgreSQL migration drift checks passed too.
- Enrolled local CPU integration:
  `tests/integration/test_process_containers_docker.py::test_real_container_execution_controls_and_private_evidence`
  — **1 passed in 2.24 seconds**. The six broader containment probes remain the preceding checkpoint's
  evidence; the Docker driver/supervisor enforcement was unchanged in this slice.
- Ruff checked 359 formatted files and passed lint. All 177 schema exports match. Mypy with
  `--no-incremental` passed for 182 source files. `git diff --check` passed.
- An intermediate full sweep found one old fork fixture attributing an event to `operator` while
  `worker` held the lease. The fixture now has its named actor actually claim that lease; the actor
  check remains strict. Intermediate fixture errors and failed probes are retained, not relabelled
  as successful evidence.

Ignored evidence is in `runs/pprl-worker-validation-20260905/`: fixture manifest, intermediate/final
logs, private Docker profile/captures/receipts, SQL backup with credential verifier digests only,
cleanup inventory, source manifest and secret-scan reports. PostgreSQL used cached 17-alpine
`sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193`, one CPU, 512 MiB
memory/equal swap limit, 128 PIDs, read-only root, bounded tmpfs, no host mounts and no network.
The owned localhost-only relay stopped; the exact PostgreSQL container and generated CPU test
container were removed. The CPU fixture reused the preceding checkpoint's reviewed cached image
and profile. No model inference/training, GPU/cloud resource, live experimental worker, external
publication, sibling edit or delegation was used.

## Stage 3 checkpoint: reviewed recovery and unresolved-effect barrier, 2026-09-05

The checkout began at `ffe3195` with no unrelated edits. Root instructions and all required documents
were read in order. `pprl-assignment-recovery-boundary.md` defined this slice before implementation.
The preceding goal turn and this continuation are progress, not blocked or complete.

The audit reproduced six unsafe replacement claims at the previous HEAD: reserved, started and
settled-but-uncommitted actions, each under legacy and enrolled authority, still allowed an expired
lease to be reclaimed. Shared resource holds stopped additional effects only when aggregate capacity
happened to bind. The baseline was **6 failed, 1 passed**; the final barrier stops earlier, so an older
test expecting a claim followed by budget denial was corrected to require no replacement claim.

Claims and new admissions now independently anchor the barrier in admitted decisions and validate
their reservations, original releases and committed events. A missing reservation or phase cannot
erase an admission. A mutable `released` label alone cannot clear it. Spare funding and a relabelled
lease/request do not permit a second uncommitted effect. Amber's internal atomic reservation writer
exempts only its just-inserted decision from its own historical-gap check.

The private `ProcessRecoveryStore` checks reviewed state/lease expectations and original authority,
serializes rollout/scope/worker/Amber/account access, fences the prior lease and can retire its
credential. It refunds only an unstarted reservation without retained effect intent. The existing
reviewer refund API now enforces that same condition; a reserved container may already have been
created. Complete independently retained model/container results can reconcile once after broker
loss. Unknown effects retain holds. Completed effects without a successor remain review-required;
there is no synthetic state update, raw-result admission or successful scientific outcome.

One immutable SQL table and four schema exports retain review, phase assessment, original state,
assignment/retirement, Amber lifecycle and account identities. Each cited source gets independent
`process_recovery_receipt` ownership. Replay requires original and recovery owners and permits late
external-call status changes without rewriting the earlier assessment. Source count/byte limits,
stale review, authority expiry during inspection, idempotency mismatch and failed receipt retention
all fail conservatively. A populated recovery ledger cannot be downgraded to erase its history.

A separate native recovering broker and a separate native replacement broker/client operate from
durable SQLite/artifacts alone, mint a new scoped incarnation, and recover identical admitted
observation bytes after an untouched action. This replaces every simulated worker in that fixture;
it does not prove autonomous fleet operation, preservation of worker-local execution or useful
scientific competence under 100% churn. Delayed synthetic model results remain private, are billed
once and cannot commit after fencing. PostgreSQL tests exercise duplicate recovery, dispatch commit
versus rollback, claim contention and a commit waiting behind recovery.

Retained evidence is under ignored `runs/pprl-recovery-validation-20260905/`: original failing probes,
intermediate/final logs and XML, native broker/client fixtures, private SQL receipts/source joins,
artifacts and ownership, exact source/schema/migration manifests, cleanup and redacted secret scans.
Early implementation tests found a lifecycle timestamp-field mismatch and two fixture helper-field
mistakes; corrected runs are distinct from those retained failures. Final review added missing
reservation/head probes and a completion-time authority recheck before readiness publication.

Final validation:

- Full offline suite: `PYTHONPATH=. .venv/bin/pytest -q -m 'not postgres and not live and not lean
  and not docker'` — **799 passed, 27 deselected in 132.49 seconds**. This includes 43 recovery
  scenarios and the added populated-ledger downgrade guard. The retained focused recovery run was
  **43 passed in 13.17 seconds**; its SQLite/artifact fixtures remain available for inspection.
- Fresh bounded PostgreSQL fixture: `PYTHONPATH=. .venv/bin/pytest -q -m postgres` — **19 passed,
  807 deselected in 22.66 seconds**, including five recovery race cases and migration checks.
- Ruff passed lint and formatting on 370 Python files; all 181 schemas match; mypy with
  `--no-incremental` passed on 185 source files. `git diff --check` passed.
- The final PostgreSQL rerun is separate from a failed fixture run: repeated schema rebuilds filled
  the first fixture's 256 MiB tmpfs WAL, yielding **13 passed, 6 setup errors**, with no OOM kill.
  The failure logs and container state are retained. A fresh fixture used the same pinned cached
  image, one CPU, 512 MiB memory/equal swap limit, 128 PIDs, 256 MiB data tmpfs, read-only root,
  no host mounts and no container network. WAL targets were 64/32 MiB with 30-second checkpoints,
  64 MiB shared buffers and 40 connections. Acceptance assertions were unchanged.
- Both exact owned PostgreSQL containers were removed and both localhost-only relays reported
  `POSTGRES_TEST_RELAY_STOPPED`. The owned label inventory is empty. The preexisting
  `padawan-postgres-1` and `common-ground-postgres-test` containers remain. No application container,
  model inference/training, cloud/GPU, publication, sibling edit or delegation was used.
- The all-ref secret scan covered **31 commits and 7,282,191 exported patch bytes**, with zero
  findings and no tracked credential/runtime paths. The separate exact-staged scan also found zero
  secrets across 31 files. The final ledger update is rescanned before commit; source/scan manifests
  and commit-byte verification are retained with evidence.

## Stage 3 follow-up: historical recovery-source integrity and resolution design

Starting HEAD: `ebe1000` on `codex/pprl-information-boundary`. Re-read the goal and canonical entry
documents and audited recovery, ordinary event commit, evidence admission, outcomes/eligibility and
the PPRL compiler before selecting the next slice. No additional user authority was needed.

The audit found a prerequisite to recovered-effect admission: checked historical recovery reads did
not reconstruct all native source relationships. Nine initial probes with self-consistent outer
receipt digests were accepted despite omitted model/container sources, substituted result identity
or inconsistent observed status. These are malformed/imported-record probes inside the trusted
store perimeter, not claims that workers can write SQL or that a live incident occurred.

`RecoveryEvidenceReader` now verifies exact immutable workload/request/result identities, prepared
wire bytes, container source/capture sets and dispositions. `ProcessRecoveryStore.read` requires the
exact receipt-wide ownership set and original phase events. Completed assessments additionally
require the original settlement's evidence basis, rate/result/source digests and independent pins.
It never invokes reconciliation or dispatch. Historical partial snapshots survive a later result;
mutable external-call status cannot rewrite the original snapshot. New receipt publication passes
the same checked read atomically, then rechecks expiry before reporting readiness. Failure rolls
back the new receipt, fencing, accounting and pins. There are no schema, worker DTO or authority
expansions.

The separate `pprl-effect-resolution-boundary.md` records the next dependency sequence: explicitly
reviewed recovered-source admission, then a distinct reviewed successor or terminal abandonment,
task/effect ownership that prevents silent repeats, and compiler/replay treatment of intervention
and infrastructure exclusions. These are designs, not implemented transitions. A recovered result
does not establish domain truth or authorize training. The canonical four-fabric overview now
acknowledges PPRL's private recovery primitives while preserving the absent automatic runtime.

Retained evidence is in ignored `runs/pprl-recovery-source-validation-20260905/`: original failing
probes, subsequent source/phase/result probes, final retained SQLite/artifact fixtures, full/static
check logs, exact source manifests and redacted secret scans. One expanded probe initially used a
nonexistent denormalized SQL column; the setup failure remains separate from corrected runs. An
initial tracked-path check matched the checked-in `.env.example` template; that template stays in
the full history scan, while actual credential/runtime paths remain forbidden. No secret finding
was suppressed.

Final validation:

- Full offline suite: `PYTHONPATH=. .venv/bin/pytest -q -m 'not postgres and not live and not lean
  and not docker'` — **828 passed, 27 deselected in 144.11 seconds**. An earlier full run before
  the final expiry guard was **827 passed, 27 deselected in 141.82 seconds**; it is separate evidence.
- Focused recovery suite: **72 passed in 24.87 seconds**, with retained SQLite/artifact fixtures.
  This includes 29 new source/disposition/phase/settlement/publication probes. Re-running those
  exact 29 probes against retained `ebe1000` recovery modules gave **29 expected assertion failures,
  zero setup errors in 11.76 seconds**. The baseline source modules, probe copy, runner and hashes
  are retained. An earlier baseline-runner attempt collected outside the test fixture directory;
  its 29 setup errors are retained separately and are not counted as defect reproductions.
- Ruff lint/format passed; 181 schemas match; mypy `--no-incremental` passed on 185 source files;
  `git diff --check` passed. No contracts or migration files changed. PostgreSQL and physical Docker
  execution tests were not rerun for this source-reader change; no new SQL schema or lock order was
  introduced, and no current PostgreSQL/runtime-validation claim is made.
- All-ref secret scanning covered **32 commits and 7,460,682 exported patch bytes**, with zero
  findings. The tracked `.env.example` is included; actual credential/runtime paths are untracked.
  The exact staged files receive a separate redacted scan before commit; its manifest, result and
  committed-byte verification are retained with the ignored evidence.
- No model inference/training, application container, live research worker, PostgreSQL fixture,
  cloud/GPU, sibling edit, delegation or publication was used. Docker use was limited to the cached,
  network-disabled CPU secret scanner with read-only input and bounded disposable resources.

## Next executable step

Implement the explicit recovered-source admission adapter specified in
`pprl-effect-resolution-boundary.md`, using the hardened historical reader. Bind one exact completed
effect and its full private source set to a separately reviewed, rights/scoped process derivative;
keep training use disabled. Revalidate the pinned evidence broker's current authority, native
provenance, retention and public-content boundaries. This admission must not clear the unresolved
effect barrier, dispatch/reconcile work, forge an old worker event or change canonical state.

Then define executable task disposition and a distinct reviewed successor/terminal-abandonment
transition together with replay/compiler treatment of interventions and infrastructure exclusions.
Select independent domain evidence for successor review; preserve original effects and costs and
keep unknown effects stopped. A new lease or task ID cannot silently repeat an abandoned effect or
hide an exclusion in scientific credit. Automatic replacement scheduling depends on these contracts.

Then connect heartbeat, stale-worker detection, durable task ownership, role-specific action masks
and deterministic scheduling to the existing scoped credential and recovery boundary. Credential
delivery/isolation and child enrollment need explicit contracts. Retain byte-exact hydration and
test complete roster churn, interrupted handoffs and lifecycle races with simulated workers first.
The current clear-claim integrity check traverses rollout history; benchmark and bound its growth,
and design checked incremental frontiers before long-horizon execution. No new authorization is
required for these next local audit/design and bounded engineering steps.

The CPU runner is complete for its stated local scope. Model-serving containment, independent
forensic service credentials and full capture, actual model identity, physical metering, domain
result admission and recovery of unknown effects remain gaps. SQL revocation and Docker start are
not atomic; bounded polling/watchdog evidence is not an instantaneous revocation guarantee.
Institutional continuity through 100% worker replacement is still unproven.

Model/GPU validation, Atlas institutional subjects and causal MI interchange, automatic recovery,
explicit communication/economics, the 10M+ token milestone, preregistered learning, an actual trainer/
parameter candidate and independent evaluation remain in the full roadmap. Keep Atlas training
materialization gated. Prepare the required concrete campaign before requesting any additional
execution authority; no new user authorization is required for the next local engineering audit.

## Completion audit

The goal remains active until foundational gates and an authorized preregistered institutional
learning cycle are supported by retained evidence, including a real candidate update and independent
evaluation. Keep incomplete long-horizon and external-execution requirements explicit. A credible
null or regression is a valid research result; compilation or simulated workers alone are not.
