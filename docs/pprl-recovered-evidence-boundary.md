# Reviewed evidence from completed recovery records

Status: implemented for offline review after `9cb0501`; validation is recorded in the execution
ledger. This is the source-admission dependency in `pprl-effect-resolution-boundary.md`, not a
successor transition or automatic recovery scheduler.

## Boundary and assumptions

Add an explicitly composed recovery-source adapter to `ProcessEvidenceStore`. One separate reviewed
candidate binds one exact completed effect in a checked recovery receipt. Its private versioned
admission retains the disclosure policy, receipt/effect digests and full native source set. Only the
existing `ProcessArtifactRef` and reviewed candidate bytes may reach a process consumer. Construction
and inspection launch nothing; admission changes neither canonical state nor the effect barrier.

The broker, SQL/artifact stores, pinned policies and named Amber reviewers remain trusted. Review
attests the candidate's meaning and redaction; hashes establish byte identity and provenance, not
domain truth, authenticated authorship or a sandbox. A worker cannot configure this adapter, review
its own disclosure or invoke it through the bounded worker protocol.

## Invariants and paths

- Require a pinned disclosure policy naming the target execution, contamination scope, reviewer
  set, validity window and source inspection bounds. Same-execution provenance is mandatory; an
  origin cannot import another rollout execution's private results. The existing evidence policy
  and original Amber envelope independently constrain review, rights, size and current authority.
- Accept only an exact `completed_unadmitted` model/container effect with reconstructible original
  settlement. A recovery receipt's overall `review_required` status is not enough. Unknown effects,
  released reservations and a worker's assertion of completion are insufficient.
- Bound the whole recovery receipt's effect count and distinct source bytes before its checked
  reader loads source bytes. Separately enforce the candidate policy's selected source count/bytes.
  Reconstruct native identity, original/recovery/accounting owners and exact source bytes without
  dispatch, reconciliation, backfill or new discovery/listing authority.
- Review must follow the source receipt and bind the exact candidate and complete selected source
  set. Independently pin candidate and forensic dependencies under the existing admission owner in
  the same transaction as its versioned receipt. Existing receipts retain their original bytes.
- Ordinary version-1 admission/read rejects any declared source with a recovery-receipt owner.
  Omitting the prepared workload and citing only a completed invocation's request/response cannot
  erase recovery origin or enable training. This deliberately denies ambiguous content-addressed
  sources too. Previously admitted version-1 records remain immutable but lose new-use authority
  when their cited source acquires recovery ownership. Undeclared semantic provenance remains a
  reviewer trust assumption; this check cannot detect a reviewer concealing every source.
- Public reads revalidate current configuration/authority, original provenance and exact ownership.
  Missing pins or changed policy deny use; they are never repaired by a read. Errors/cancellation
  use the existing fixed worker-facing boundary, without private exception context.
- Reject literal private source, recovery, workload, account and origin identifiers in candidates,
  including bare digest forms. Version 1 accepts reviewed UTF-8 text/Markdown or JSON; parse JSON
  before bounded identifier checks, rejecting duplicate keys, so escapes or overwritten members do
  not hide known references. This does not prove
  semantic redaction or exclude steganography. Other media require a separately defined admission
  contract; this staging limit does not reduce the full program's evidence ambitions.
- Permit process use only. Training-use requests, projection ownership and public training reads
  remain denied. A reviewed source derivative is an intervention, not attributable worker behavior
  or automatic scientific success. Successor/task/credit decisions remain separate requirements.

## Acceptance and retained evidence

Use synthetic model callbacks and simulated container drivers. Admit reviewed derivatives from
complete model/container sources and from a late retained model result whose old invocation remains
fenced. Reopen the broker from durable stores and recover exactly the candidate bytes. Confirm that
state/lease/account/decision/effect identities are unchanged and that no model/tool method runs
during admission or read.

Reject unknown/released effects, wrong receipt/effect/execution/reviewer/scope/time/policy, omitted
or substituted sources, missing original/recovery/accounting/admission pins, corrupt bytes and
unauthorized training use. Test native identifiers in text and escaped/nested JSON. Inject retention
failure and authority expiry during source inspection; no partial receipt or pins may commit.
Exercise duplicate admission and configuration changes, and preserve existing model/Atlas receipt
compatibility outside the explicit recovery-origin restriction. Verify independent process-record
ownership without clearing the source rollout barrier. An explicitly created separate replica
still pays its normal initial-state cost from shared funding.

Retain baseline source identity, exact test commands/results, schemas, private review/origin/receipt
joins, artifact and account/state snapshots, reopened-client results, failures, cleanup and redacted
secret scans under ignored evidence paths. Label this as offline admission validation, not live
institutional continuity, domain verification, model improvement or learning-cycle completion.

## Failure, rollback and non-goals

Fail closed and roll back the new admission transaction on invalid evidence or changed authority.
Retain existing records and barriers. Rollback disables this adapter and denies unknown receipt
versions; preserve admission rows and ownership rather than rewriting them as legacy approvals.

Non-goals: resolving an effect, creating a successor, task/retry scheduling, hydrating a replacement
from raw output, cross-execution disclosure, binary-media admission, authenticated reviewer delivery,
unknown-effect cleanup, scientific outcome admission, training materialization, model calls, cloud
resources or deployment. These remain later contracts and authorization gates.

## Concrete entry and storage paths

`PPRLApplication.recovered_evidence(admission_policy=..., disclosure_policy=...)` constructs an inert
broker. A privileged caller prepares an exact source with `RecoveryEvidenceSourceBoundary.describe`,
reviews a separate classified candidate, and calls `ProcessEvidenceStore.admit_recovered`. The
factory does not install itself into existing process consumers or expose a worker endpoint.

`RecoveryEvidenceDisclosurePolicy`, `RecoveredEffectSource`, `RecoveredEvidenceOriginReview` and
`RecoveredProcessEvidenceAdmissionRecord` have generated schemas. Private receipt version `3.0.0`
reuses `process_evidence_admissions.record_json`; there is no SQL migration. The existing
`artifact_references` admission owner pins the candidate and complete native source set. Explicit
process consumers add independent state/event ownership through the existing evidence broker.
Only the existing process reference and candidate bytes cross that use boundary. Native forensic
owners, recovery receipt owners and original accounting owners must remain present independently.

`RecoveryEvidenceSourceBoundary.validate_review` reuses the checked historical reader and current
content policy. Its receipt preflight bounds declared effect count/distinct source bytes; this is
not a memory, disk or execution sandbox guarantee. No PostgreSQL race, physical container or live
model is exercised by this checkpoint's synthetic source-admission tests.
