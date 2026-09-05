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

## Next executable step

Checkpoints 1 and 2 are verified offline. Continue process ingress and worker/training projections.
Existing `ProcessGenerationResult` still returns the complete generation and raw
artifact references; `ProcessStore.append_event` requires those references in the event; the PPRL
compiler serializes complete events into trajectory rows. Preserve their privileged forensic join
while removing automatic worker/training visibility in the following checkpoints. Process ingress,
ownership/GC coordination, and safe projections remain unfinished.

## Completion audit

The goal remains active until foundational gates and an authorized preregistered institutional
learning cycle are supported by retained evidence, including a real candidate update and independent
evaluation. Keep incomplete long-horizon and external-execution requirements explicit. A credible
null or regression is a valid research result; compilation or simulated workers alone are not.
