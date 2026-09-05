# Assignment recovery and unresolved-effect barriers

Status: implemented local recovery checkpoint from `ffe3195`, specified before implementation.
This is a prerequisite to automatic replacement, not a reduction of the full lifecycle, scientific
program or 10M-plus-token milestone. No model, worker service, cloud resource or trainer is authorized
by this specification.

## Revalidated problem

At `ffe3195`, the prior checkpoint authenticates a scoped capability and one leased action. The resource journal
distinguishes reserved, started, settled and released capacity. Model and container records retain
intent and independent results. They do not yet decide whether a rollout whose lease expired may
resume. `ProcessStore.claim_next` selects by lease/time/Amber status without an unresolved-action
barrier. A conserved hold prevents new work only when a shared capacity limit happens to be binding;
it is not an exact per-rollout recovery decision. A known, billed result can also remain uncommitted.

Recovery must distinguish a committed transition, an action proved unstarted, a retained completed
effect still awaiting process admission, and an unknown effect. Worker death or lease expiry is not
evidence that an external effect did not occur. Private forensic findings and references must not be
placed in replacement observations to explain the stop.

## Boundary, assumptions and authority

Implement a per-rollout barrier against a new lease/admission while any earlier admitted action
lacks either a committed event or a valid unstarted release. Add a privileged, explicitly reviewed
recovery operation with immutable request/assessment/source lineage. It inspects retained source
records, fences the prior lease, reconciles only independently reconstructible known usage and
permits retry only when no unresolved effect remains. A completed external result is not automatic
permission to reconstruct or admit the lost worker's intended process transition.

The broker, its SQL database, artifact storage and existing effect boundaries remain trusted.
Reviewers are the names authorized by the original Amber envelope, not cryptographically attested
people. Workers cannot request recovery, release funding, supply a success assertion, resolve a
forensic reference, or select a different authority through the bounded worker interface.
The caller must name the expected current state and lease digest so a stale review cannot affect a
successor. No new network authority, transport or default background service is added.

## Invariants

- A resource hold is a budget fact, not permission to retry. Uncommitted reserved, started or
  settled actions block a replacement claim even if other capacity is available. Missing or corrupt
  source/head records fail closed. Denied proposals do not create an effect barrier.
- A committed event or a properly reviewed unstarted release resolves its exact admission only.
  Changing worker, lease, request, action ID, role or model cannot bypass the barrier.
- Recovery takes the rollout lock before worker/Amber/resource locks. It rechecks original scope,
  current state, lease and reviewer authority while locked. It fences the previous lease before
  permitting any successor; an old worker cannot dispatch or commit afterward.
- A still-valid, active lease is not taken over silently. Review after expiry, revocation or an
  explicit pause may fence it. Recovery never reactivates a paused, quarantined, revoked, expired
  or stopped authority, or resets the shared funding account.
- Reserved capacity may be released only when retained evidence establishes no dispatched effect;
  ambiguous prepared/partial container execution requires review and cleanup evidence. Unknown
  effects retain holds. Known complete model/container results may settle usage through their
  existing independent evidence readers without rerunning a model or command.
- Known effects without an admitted successor remain review-required. Recovery does not synthesize
  a lost state update, transform raw output into process evidence, or mark an infrastructure outcome
  scientifically successful. Only canonical admitted process state can hydrate a replacement.
- Recovery records and their discoverable identifiers remain private. Independently retain the
  source artifacts used in a recovery receipt. Replays require exact source identity and ownership;
  an idempotency key cannot change reviewed intent or repeat settlement/refund.
- Receipt, accounting and rollout changes are atomic; failed evidence validation leaves the old
  lease/state/account intact. Interrupted or unknown physical effects are never converted to an
  automatic retry by rollback.

## Acceptance and retained evidence

First reproduce replacement claims across uncommitted reserved, started and settled actions at the
current HEAD, including a case with spare global capacity. Verify exact reviewed release and
committed-event paths allow progress. Cover legacy trusted calls and enrolled worker requests.

Exercise fresh broker instances, clean release, expired/revoked credentials, stale recovery input,
simultaneous claim/recovery/dispatch/commit, delayed completed results, missing source bytes or pins,
receipt rollback, duplicate recovery requests and failed cleanup. Verify prior workers stay fenced,
refund/settlement happens once, unknown holds persist, and new observations contain neither raw
records nor recovery identifiers. Use disposable simulated workers and synthetic model callbacks;
claim no live replacement or actual model improvement.

Retain failing probes, source/schema/migration identities, exact commands/results, private recovery
requests and source joins, state/lease/account before-and-after identities, independence of artifact
owners, concurrency outcomes, cleanup and pre-commit secret scans outside Git where restricted.

## Failure, rollback and non-goals

Stop recovery on corruption or unexpected source changes. Preserve an unresolved barrier and its
existing resource hold; retain an explicit review-required assessment when source evidence is valid
but incomplete. Disable new recovery operations on rollback, preserve populated history and keep
the barrier enforced. Never use downgrade or legacy APIs to restore permissive retry.

Non-goals: automatic provider-side reconciliation, guessing whether a timed-out request executed,
model/host attestation, credential distribution, heartbeat/placement/launcher implementation,
multi-action task assignments, peer delegation, domain result admission, child-scope enrollment,
full fleet pause/resume, a scientific replacement claim, training, or long-horizon execution.
These remain required later gates; this slice makes the recovery decision explicit first.

## Implemented paths and limits

| Surface | Writer / checked reader | Retained meaning |
| --- | --- | --- |
| `recovery_contracts.py` and four JSON schema exports | `ProcessRecoveryRequest`, `ProcessRecoveryEffect`, `ProcessRecoverySource`, `ProcessRecoveryReceipt` | Strict private review, phase assessment, source ownership and immutable outcome; no worker DTO |
| `process_recoveries` / migration `f4d63b18a920` | `ProcessRecoveryStore.recover/read` | Original state/lease/reviewer and Amber lifecycle identity, prior assignment/retirement, effect and account snapshots; populated downgrade denied |
| `resources.unresolved_action_query` | Claim selection, new admission and recovery selection | Admitted decisions anchor the barrier; missing reservations cannot erase pending effects |
| `ProcessResourceStore.assert_rollout_recoverable` | Selected claims, admissions and clear recovery outcomes | Reconstructs reservations, reviewed no-intent releases and exact committed transitions; head fields alone cannot clear the barrier |
| `ProcessResourceStore.release_unstarted` | Explicit reviewed API / existing operator CLI | Rollout-before-account serialization; no refund when model/container intent exists, even in reserved phase |
| `RecoveryEvidenceReader` | Privileged recovery only | Bounded original model request/response/prepared bytes and container input/capture/result evidence; no dispatch and no process admission |
| `process_recovery_receipt` artifact owners | Recovery transaction / `read` | Independent pins for every cited artifact; original owners remain mandatory on replay |
| `PPRLApplication.recovery()` | Explicit trusted composition | Constructs the API; starts no listener, background loop, worker or execution |
| `ProcessContentBoundary` | State, event and training projection checks | Rejects literal recovery identifiers, receipt digests and request digests; no recovery fields added to public observations |

The query function is `padawan.pprl.resources.unresolved_action_query`; it is not a worker method.
Amber's internal reservation writer excludes only the just-inserted decision in its own atomic
admission transaction from the historical-gap check. Public admission and claims have no exemption.
Denied decisions create no barrier. Previously released admissions do not consume the new receipt's
effect quota, but their original evidence is checked before a clear rollout may progress.

Defaults are 64 unresolved effects and 16 MiB of distinct source bytes per reviewed request; strict
limits permit at most 1,024 effects and 128 MiB. Count and byte limits fail before recovery publishes
changes. Retained partial model requests may have been interrupted before PPRL classification;
this explicitly reviewed operation may classify attributable, originally owned raw/restricted call
artifacts **forensic only**. It cannot grant process use. Container sources require their existing
classification. A replay requires the classification and both original/recovery ownership that the
receipt recorded; it does not demand that a mutable external-call status remain frozen forever.

Recovery permits no takeover of an active unexpired lease unless a rollout/Amber pause, expired
authority or worker revocation/expiry supplies a stop precondition. `resume` is explicit for paused
rollouts and never changes Amber status or a stopped account. Authority time is rechecked after
source validation. Terminal scientific rollout states are not reopened. An exact repeated review
returns its historical receipt and cannot fence a successor or restore historical readiness.

A prepared container, even with a `not_started` final label, is conservatively unresolved through
this API. A future explicit cleanup-resolution contract must establish physical nonexecution and
removal before refunding such intent. Complete results without a committed successor also need a
separate governed domain admission/resolution; this checkpoint does not provide that path. It never
marks an unknown model invocation completed or rewrites an old invocation's mutable lifecycle to
hide uncertainty.

The current integrity check reconstructs a rollout's earlier admissions and reservations before a
clear claim/admission. Its cost grows with history; no incremental verification cache or 10M-token
performance claim is made. Indexed frontier tracking, bounded archival/replay policy and endurance
measurement remain prerequisites for long-horizon operation. No model or real container workload
was launched for this recovery checkpoint; PostgreSQL was an explicitly owned local CPU fixture.
Exact test outcomes, prior failures, cleanup and the next dependency are in `pprl-execution-ledger.md`.

The subsequent source-integrity checkpoint requires exact receipt-wide source ownership, native
model request/prepared/result joins, complete container source/capture sets, recorded resource
phases and original settlement sources/pins. Reads reconstruct historical bytes; mutable call status
or a later capture cannot rewrite an earlier snapshot. Fresh recovery publication uses this checked
reader before its transaction commits and rechecks expiry before publishing readiness. A malformed
snapshot or expiry during final validation rolls back the new receipt, fencing,
accounting and pins. This adds no worker fields, source admission, retry authority or schema changes.
The dependency-ordered successor/abandonment design is in `pprl-effect-resolution-boundary.md`.
