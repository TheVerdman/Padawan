# Reviewed resolution of completed effects

Status: resolution design specified at `ebe1000`; exact historical recovery-source validation and
explicit reviewed recovered-source admission are now implemented prerequisites. Successor,
abandonment and task disposition remain unimplemented. This is a stage toward executable task/retry
scheduling, not a reduction of PPRL or its long-horizon program. Further implementation is held for
the user-requested complexity and roadmap review.

## Revalidated dependencies

- `ProcessStore.append_event` requires the original current lease, worker capability, assignment,
  admission and authority sequence. Recovery fences that lease. Bypassing these checks would
  impersonate the lost worker; its intended successor was not durably recorded.
- `ProcessEvidenceStore.admit_recovered` now supplies the explicit adapter for completed container
  effects and late model results whose old invocation remains fenced. It binds one exact effect's
  complete source set in a private version-3 origin; process use requires separate review and
  training is denied. Ordinary source admission/read now rejects recovery-owned source artifacts.
  See `pprl-recovered-evidence-boundary.md`. Recovery retention alone grants no admission.
- `ProcessRecoveryStore.read` must establish the exact retained request/result relationships before
  a future consumer may rely on a completed-effect assessment. At the design HEAD, the model reader
  checks source bytes/pins and workload digest but not their complete native ownership/result joins;
  the container reader does not require the snapshot's complete declared source set.
- `compile_pprl_snapshot` derives completion from process events and cites selected outcomes and
  eligibility decisions. It has no recovered-transition, reviewed-intervention or abandonment
  record type. `record_outcome` and `record_training_eligibility` cannot establish that an external
  effect has been reconciled or that a reviewed replacement transition was the behavior policy's
  action. New resolution cannot silently reuse those meanings.

These are code findings, not evidence of an observed live incident. Current claim/admission barriers
continue to stop unresolved effects. No resolution bypass exists in the bounded worker interface.

## Dependency-correct implementation order

1. Validate historical recovery snapshots against exact immutable workloads and retained source
   bytes, owner sets, response identities and dispositions. Preserve earlier partial snapshots when
   a later result arrives. Reads must not dispatch, reconcile, backfill ownership or change state.
2. Add an explicitly composed recovered-source adapter to the existing evidence admission broker.
   A separate reviewed derivative must bind one exact completed recovery effect, its source receipt,
   all attributable forensic dependencies, the target execution/contamination scope and rights.
   Keep native origins private. Initially permit process use only; training requires a subsequent
   materialization/credit contract. This validates provenance and review, not domain truth.
3. Add a privileged resolution transition with its own author and immutable private lineage. It
   must not reuse the lost lease or pretend to be the original worker event. A reviewed successor
   needs separately admitted domain evidence and an explicit account of the intervention. A task
   disposition must prevent the same logical effect from being silently scheduled again under a
   new lease/action ID. Define and test that ownership before enabling continuation.
4. Connect the resolution timeline to historical replay, outcomes, compiler exclusions and later
   attribution. Then add retry/task lifecycle and automatic replacement scheduling.

Items 1 and 2 have offline implementation evidence. Items 3 and 4 are the remaining dependencies;
they are proposals for the review, not authorization to continue past the current hold.

## Proposed resolution boundary and invariants

The trusted broker, SQL/artifact stores, control-plane reviewers and existing effect adapters remain
trust assumptions. Reviewer names are declarations, not authenticated human identities. No new
tool, model, environment, network or credential authority follows from a resolution.

One review names the expected current state, recovery receipt, exact effect, disposition and
candidate successor or terminal abandonment. Serialize against recovery, dispatch, event commit,
claim and authority transitions. A stale review cannot affect a successor. Exact retries return
historical receipts; they cannot repeat effects, charges, refunds or state advancement.

An admitted successor is a reviewed institutional intervention, not reconstructed worker-local
context or proof of scientific success. Only separately admitted process content reaches hydration.
Preserve the original effect, reservation, actual debit, source owners, worker/policy/observation
identity and intervention provenance. Resolution must neither replenish capacity nor manufacture
a new budget action to conceal the original one. Unknown effects remain stopped with their holds.

For an initial abandonment path, terminate the rollout and retain an explicit infrastructure or
unresolved exclusion; do not continue from unchanged state as though the action never happened.
A later fresh replicate needs a distinct declared identity and inclusion/exclusion accounting.
Partial credit, continuation after abandonment and cross-task effect deduplication require their
own preregistered and executable contracts. A terminal status is not proof of physical cleanup.

The initial successor path should exclude the intervened trajectory from parameter learning until
the compiler can retain the intervention and attribute observations/actions/credit explicitly.
Retain failed and abandoned samples in experiment accounting. Do not erase them from distribution
denominators, call an infrastructure interruption a domain failure, or infer training eligibility
from a scalar outcome selected later.

## Acceptance tests and retained evidence

For the source-reader prerequisite, reproduce omitted/swapped sources and forged result
or status joins with self-consistent outer record digests. Check exact native owner sets and bytes,
complete and partial model/container snapshots, late results, missing pins, bounded reads and
unchanged state/lease/account/owners. Use synthetic callbacks and simulated container drivers only.

For subsequent admission/resolution, test wrong execution/split/reviewer/authority sequence,
incomplete source sets, stale head, forensic identifiers in candidates, missing original and new
pins, expired authority during review, duplicate delivery and transaction rollback. Demonstrate
that no provider/tool method is invoked. Test all resolution/claim/dispatch races on PostgreSQL.
Restart every broker and simulated worker from durable stores; hydrate byte-exact admitted state
without old credentials, partial responses, recovery identifiers or worker-local stacks.

Retain exact source/schema identities, review and resolution requests, before/after state and
account identities, private effect/source joins, artifact ownership, compiler inclusion/exclusion
watermarks, adversarial failures, passing logs and cleanup outside Git where restricted. A future
preregistered experiment additionally needs task/instance/replicate/fork identities and explicit
intervention and infrastructure accounting. Offline fixtures establish none of its outcome claims.

## Failure, rollback and non-goals

Fail closed on missing, substituted or ambiguous evidence. Roll back only the new transaction;
never undo a real external effect or erase its charge. Keep unknown effects stopped. Rollback must
disable new admission/resolution while preserving populated history, pins and claim barriers;
do not downgrade to permissive legacy retry.

Non-goals of the implemented prerequisites: successor or abandonment,
clearing a barrier, physical cleanup of unknown effects, background scheduling, provider retries,
authenticated reviewers, model/host attestation, semantic redaction proofs, scientific competence,
training or actual model execution. The later steps above remain required and unfinished.

Decision: source validation and the offline admission checkpoint can be retained for review;
no-go for automatic continuation until task disposition, reviewed-transition lineage and compiler
handling are implemented and validated. The user explicitly requested a hold after this checkpoint;
resume local implementation only after that review and a new resume instruction.
