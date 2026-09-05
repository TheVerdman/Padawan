# PPRL worker observation boundary

Status: the observation portion of information-boundary checkpoint 3, exercised with offline
fixtures. This is a public input contract and trusted-broker projection service. It is not a live
hydrator, worker runtime, model-prompt attestation, or evidence of institutional learning.

## Boundary and threat assumptions

`ProcessObservationStore` owns projection, current-scope checks, retention, and private receipts.
The database, artifact backend, broker clock, content registry, and adapter composition are trusted.
Stale lease holders, corrupted records, forged references, missing ownership, accidental full-record
serialization, and mutation of a planner's nested values are in scope. Arbitrary code with direct
access to the broker's Python process or database is not isolated by these interfaces.

Only `ProcessWorkerObservation` is passed to `ProcessPlanner.propose`. `ProcessActionExecutor` is
an explicitly trusted effect adapter; it also receives broker state and admission credentials, which
it must not forward to a worker. A protocol signature does not authenticate either object or attest
an execution sandbox. Capability or organizational rank grants no extra authority.

## Public and privileged stores and paths

| Surface | Contents | Producer and consumer |
| --- | --- | --- |
| `ProcessWorkerObservation.state` | Objective, plan, hypotheses, claims, admitted process artifact references, dependencies, budget usage, declarative assignments, risks, admitted memory links, registered extensions | Explicit projection of a content-admitted `ProjectStatePayload`; planner receives a detached DTO |
| `process_states`, `process_content_admissions` | Source state and immutable content-policy receipt | `ProcessStore` and `ProcessContentBoundary`; read and revalidated by the observation broker |
| `process_observations` | Exact canonical UTF-8 observation JSON and digest, source state/digest, execution, content receipt, full projection policy, declared worker, lease-token digest, Amber sequence, research/retention rights digests, creation time | `observe_claim`; privileged `inspect_receipt` for reconstruction, current-scoped `read` for public delivery |
| `artifact_references`, owner `process_observation` | Independent pins for each admitted candidate and its full private source set | Evidence broker resolves each process reference and pins dependencies in the observation transaction |
| `process_observation_decisions` | Observation/receipt/digest joined to exact Amber request and decision digests, disposition, declared role/model, binding time | `bind_decision`; privileged `inspect_decision_binding` after completion, release, or pause |
| `amber_admission_decisions` | Original request and admission/denial | Amber writes before observation binding; a binding failure preserves the decision but prevents coordinator execution |

The public DTO deliberately does not inherit the state envelope. It has no source state ID/digest,
parent/triggering-event linkage, lease credential, authorization record, content receipt, error, or
forensic source metadata. Public `ProcessArtifactRef` values retain their existing process ID,
execution scope, and admitted content digest; they do not expose the private artifact ID/URI,
classification or review identifier, or source set. Declared assignments are process content;
they remain declarative and do not become executable assignments here.

Extension values have an open JSON representation in the transport DTO, but the only authorized
producer first verifies the exact closed registered schema and content-admission receipt. Parsing a
DTO directly is not admission. Scientific prose is not proven free of encoded channels by structural
validation; reviewed source provenance remains necessary.

## Invariants and lifecycle

1. Claim and initial observation are one outer coordinator transaction. Projection failure rolls
   back the newly acquired claim, receipt, and pins. No planner runs without a retained observation.
2. Delivery requires the active, unpaused rollout's current owned unexpired lease and current Amber
   authority. It validates execution/program/distribution/instance/environment/split identities,
   research and retention rights, source state/head, content policy/receipt, and all process evidence.
   Observation use does not authorize training, remote transmission, or cross-project memory.
3. An explicit field mapping creates canonical public JSON, bounded by the pinned policy (default
   1,100,000 bytes). Full state serialization is not a fallback. The immutable receipt retains those
   bytes and their digest separately from the public DTO.
4. One source/lease-owner/Amber-sequence/projection-policy context has one deterministic private
   observation ID. An identical retry returns the original receipt time and bytes; altered content,
   policy, or ownership fails. A new worker/lease may receive identical public bytes under a different
   private receipt. This is an offline replacement property, not full recovery validation.
5. Reads reconstruct the expected public bytes from the currently admitted source and recheck the
   receipt, scope, and independent observation ownership. Missing source pins cannot be replaced by
   admission or state ownership. A fresh DTO prevents nested mutation from changing the receipt or
   broker state. The trusted executor receives a fresh, revalidated observation after admission.
6. Decision bindings preserve admitted, denied, and review-required dispositions for the same
   observation context. They bind the exact request chronology, state/lease/experiment scope,
   declared role/model, and current Amber sequence. Historical Amber request hashes are unchanged.
   A denied decision never reaches the coordinator executor; a missing binding also prevents it.

Receipt creation and pins use savepoints inside the caller's transaction. Binding creation is also
atomic. A caught error cannot commit a partial observation. Broker delivery errors use one class and
message with no private exception cause/context. Cancellation remains cancellation with a generic
message. Privileged inspection is a separate interface, not a current-use authorization.

The receipt records the canonical representation supplied to the in-process planner. There is no
attestation that a provider received these bytes, no complete account of additional instructions,
and no action mask or proof of actual loaded-model identity. Those missing bindings prevent treating
this receipt alone as an attributable parameter-learning example.

## Retained acceptance evidence

`tests/integration/test_process_observations.py` retains synthetic evidence and checks exact public
keys/bytes, forensic identifier exclusion, reconstruction, independent private-source ownership,
idempotency, replacement, stale/wrong scope, time/rights bounds, policy drift, corrupt and legacy
records, byte limits, ordinary/cancellation errors, partial pin and post-receipt failure, outer
rollback, and immutable positive/negative decision lineage.

`tests/system/test_pprl_coordinator.py` checks separate planner/executor calls, mutation isolation,
retained planning failure, claim rollback before planning, preserved decisions when binding fails,
and execution only after both admission and observation binding. Migration `b7f418d6a0c5` and five
generated contracts retain the new formats; the migration performs no legacy backfill. Exact commands
and outcomes are in `pprl-execution-ledger.md`. No real model inference or cloud resources are needed.

## Failure, rollback, and remaining gates

Fail closed before returning an observation or executing an unbound action. Keep the committed
observation and Amber decision as separate historical evidence if later planning/binding/execution
fails. A later authority or state change may make binding impossible; do not synthesize a valid
binding, silently relabel the example, or erase the earlier decision. Complete crash/cancellation
capture and reconciliation of unknown external effects require the later recovery runtime.

Rollback disables these consumers and preserves receipts, source records, and ownership. Do not
downgrade a populated database or restore the old full-state planner interface as a permissive
fallback. Data migration or a revised projection policy requires its own explicit review.

Offline training projections and explicit reviewed Atlas process evidence now have separate
boundaries. New generation receipts bind exact observation/policy input and prepared transport as
described in `pprl-generation-workload-boundary.md`. Remaining gates include Atlas learning
materialization and coordinated GC; authenticated identities, independent containment and forensic
capture, full environment-effect attribution, and conserved resources;
role-specific observation/action authority; executable hydration/recovery and 100% roster replacement;
and preregistered long-horizon training/evaluation. A held SQL lock does not fence an external process
after commit, and this service does not claim to close that execution race.
