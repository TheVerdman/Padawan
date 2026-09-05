# Conserved PPRL resources

Status: implemented offline stage-2 accounting boundary; exact validation is recorded in the
execution ledger. No model execution, independent resource measurement, sandbox attestation, or
parameter-learning result is established by these accounting checks.

## Boundary and threat assumptions

An explicitly reviewed, immutable funding grant supplies one authorization-wide account. Every
rollout and fork under that authorization uses the same account. Creating a worker, replacement,
execution, invocation, or child does not fund it. Initial grants require a named Amber reviewer and
retained evidence before any rollout exists. There is no implicit grant, top-up, transfer, or legacy
backfill. Another authorization requires another explicit funding decision. Project-state budget
snapshots retain their historical meaning and never determine the account's available balance.

The broker, named-reviewer configuration, SQL database, artifact store and configured model adapter
are trusted. Declaration forgery, duplicate delivery, competing brokers, stale leases, crashes,
missing evidence, corrupt records, pause/revocation and numeric edge cases are in scope. This ledger
does not authenticate the reviewer, attest a worker, meter an accelerator, cap a provider invoice,
or prevent privileged code from bypassing its APIs.

## Units and conservation

Use bounded nonnegative integers for actions, input/output tokens, logical artifact bytes, reserved
action microseconds and micro-USD. Convert existing decimal-string budget declarations conservatively:
floor grant ceilings and ceil incremental reservations. Time is accumulated action allowance, not
calendar duration or GPU time; bytes are admitted logical retention allowances, not physical disk
occupancy or byte-days. Rates are immutable per-model identities, explicitly denominated in
micro-USD per million input/output tokens plus a per-call amount. Rates and provider counts are
declared accounting assumptions, never an independently verified bill.

For each dimension, available capacity is funding minus charged minus held. Admission requires
nonnegative availability and a free reservation slot. An immutable reservation binds the exact Amber
decision/request, authorization sequence, rollout/state, lease and worker model. Alternative decision
IDs cannot reserve the same leased action twice. Admission and reservation must commit together.

Before external effects, persist the dispatch phase. Completion reconciles once using retained,
identity-checked model results and the pinned rate. Only evidenced token/cost differences can return
capacity; time and artifact allowances remain conservatively charged. Generic committed actions
charge their full reservation. Missing or malformed usage, cancellation, timeout and unknown effects
keep capacity and the slot held. Expiry, worker replacement and rollout failure do not refund them.
Known overages remain charged and stop new admission; they must not be rolled back merely because
worker output is denied. Explicit cancellation of an unstarted reservation requires reviewed
evidence and must race safely with dispatch. No cancellation refunds a started/unknown effect.
Usage too large for the bounded integer representation preserves its full raw source, stops the
account and leaves its reservation unresolved. A zero charged counter in that state is not zero
actual usage. There is no administrative reset that erases an overage or reopens the account.

Initial root-state declarations debit the account once. Fork children inherit an already charged
parent state through validated fork lineage; they do not receive new funds or duplicate a historical
charge. Account changes have immutable, hash-linked journal records and a checked mutable head.
Normal reads check the head and latest record; privileged replay verifies the full chain.
Journal sequence defines ordering. Its timestamp is a monotone lower bound across independently
submitted requests; each original request timestamp remains unchanged in its own retained record.
Existing Amber cumulative projection limits remain an additional conservative restriction. Neither
those historical snapshots nor the logical artifact/time charges are billed or physically metered
usage. An explicit USD grant supplies the currency interpretation absent from older generic costs.

## Implemented read and write paths

| Surface | Contract and role |
| --- | --- |
| `resource_contracts.py` | Strict integer vectors, pinned model rates, reviewed grant, immutable reservation and hash-linked journal. |
| `process_resource_grants` | One immutable grant per authorization; named review and explicit evidence before any rollout. |
| `process_resource_accounts` | Locked mutable sequence/digest pointing to the last immutable balance; no worker-local balance. |
| `process_resource_reservations` / `process_resource_reservation_heads` | Immutable admission binding and checked reserved/started/settled/released phase. |
| `process_resource_events` | Immutable grant, opening debit, reservation, dispatch, settlement, reviewed release and overflow-stop history. |
| `AmberStore.admit` | Publishes decision and reservation in one savepoint/transaction; duplicate request receipts never reserve again. |
| `ProcessStore.create_rollout` / `fork_rollout` | Debits root declarations once; validates an exact committed fork state before inheriting prior history. |
| `ProcessCoordinator.run` / `ProcessResourceStore.start` | Persists a started phase before calling a trusted effect executor; checks current authority, lease, state and original deadline. |
| `ProcessGenerationExecutor` / `ProcessGenerationResources` | Checks cost at reserved token ceilings before dispatch; reconstructs completion from original owned private request/result/prepared artifacts and pinned rate. |
| `ProcessStore.append_event` / `settle_event` | Requires an actual retained event to settle a generic action; model events require prior model reconciliation. |
| `artifact_information` / `artifact_references` | Forensic classification and independent `process_resource_event` ownership of the three original model artifacts. Receipt reuse refuses missing original or independent pins. |
| `ProcessContentBoundary` | Refuses presented resource grant/reservation/journal digests in process content. No new budget record or reference is added to worker observations or training JSONL. |
| `pprl resource fund/inspect/release-unstarted/reconcile-model` | Privileged operator CLI; no provider adapter, model dispatch or automatic funding is installed. |

Migration `f6a8c2d4e913` adds five tables without backfill. Resource reconciliation is separate from
permission to deliver output: a retained complete model result can be accounted after pause,
revocation or caller failure. Unknown effects cannot be retried or refunded through this interface.
The generic settlement primitive is internal and cannot refund unmetered action allowances.

The recovery checkpoint tightens `release_unstarted`: it locks the rollout before the account and
rejects any retained model or container intent, even when the resource head is still `reserved`.
Container creation may precede start, so that phase alone is not proof of no external effect. Claims
and new admissions also require every prior action to have a valid reviewed no-intent release or an
exact committed event. Spare global capacity cannot bypass this per-rollout barrier. The separate
`ProcessRecoveryStore` records reviewed fencing and source-backed assessments without admitting a
lost process update. See `pprl-assignment-recovery-boundary.md`.

The subsequent CPU container checkpoint adds `container_evidence` settlement through
`ProcessContainerStore.reconcile`. It reconstructs owned intent, exact input, runtime/create/terminal/
cleanup captures and the final receipt before charging the full reservation plus any retained-byte
overage. It never refunds CPU/tool allowances based on elapsed time. Truncation, incomplete capture
or unknown removal keeps the reservation unresolved. A complete terminal receipt can settle after
pause or cancellation; that accounting grants no authority to deliver output or commit an event.
`ProcessStore.append_event` rechecks independent container evidence and its ownership even after an
earlier settlement. A generic event cannot release an unresolved container hold. These paths are
explicit broker APIs, not a new automatic CLI execution path. See `pprl-container-execution-boundary.md`.

## Transactions, evidence, tests and rollback

Lock order is rollout, enrolled scope/worker when needed, Amber authorization head, resource account,
reservation head. Funding and
reconciliation that need no rollout lock must never acquire one after the account. PostgreSQL row
locks serialize contenders; SQLite lock conflicts fail before effects and never justify retrying a
possibly dispatched operation. Caught exceptions and outer rollback must not leave partial funding,
reservations, reconciliation, initial-state charges or fork children.

Retain grants, rate identities, immutable reservations, dispatch/settlement/release records, exact
source call/workload/result identities, private artifact ownership, and journal/balance snapshots.
These privileged records and discoverable references stay outside worker observations, process
state and automatic training projections.

Acceptance tests cover aggregate exhaustion across rollouts, duplicate IDs and changed content,
competing reservations/settlements, forks and fresh worker identities, explicit unstarted cancellation,
pause/revocation, completed-result reuse, uncertain effects, corrupt/missing evidence, overages,
fractional rounding and integer limits, independent evidence retention, and transaction rollback.
Run schema/migration checks, relevant generation/coordinator/training regressions and the offline
suite; retain exact outcomes. PostgreSQL concurrency needs actual PostgreSQL evidence. The current
checkpoint uses a cached PostgreSQL image in a disposable CPU container; this establishes SQL
concurrency behavior, not a worker sandbox. Tests instantiate synthetic brokers and fake model
clients, not live research workers.

Failure denies new effects or output and preserves uncertainty. Operational rollback disables new
dispatch while retaining funded balances, holds, receipts and pins; it never restores unbudgeted
dispatch or downgrades a populated database. Non-goals are physical resource enforcement, top-ups or
cross-account transfers, authenticated runtime identity, generic tool metering, a live scheduler,
model stress tests, cloud activation, training and scientific continuity/improvement claims.
