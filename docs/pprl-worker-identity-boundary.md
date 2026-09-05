# Worker credentials and leased assignment ownership

Status: implemented within the local trusted-broker boundary from `2f884ab`; exact validation is
recorded in `pprl-execution-ledger.md`. The boundary and acceptance criteria below were written before
implementation. This preserves the full research program and does not authorize a live worker,
model, network endpoint, cloud resource or trainer.

## Revalidated audit

At `2f884ab`, `ProcessCoordinator` took a caller-supplied `worker_id`, and `ProcessStore.claim_next` recorded it
without registration. `ProcessObservationStore._context` compares that string and a lease token;
it authenticates neither their issuer nor the proposing process. Proposal role/model fields are
checked against the program/execution, but are not assigned to that worker. Model and container
execution bind an admitted observation and request without authenticating the worker behind them.
`append_event` checked lease possession without binding a new event's actor to its lease owner.
`WorkerAssignment` in project state remains authored process content, with no executable ownership.

The CPU runner establishes a separate local effect boundary. It does not solve this caller or
assignment identity gap. Hashes and issuer/reviewer names alone must not be presented as attestation.

## Selected boundary and trust assumptions

Add an explicitly enrolled execution scope, broker-issued short-lived worker credentials and an
immutable binding between a registered worker, one role/model, and one leased action. A bounded
worker request interface must use those credentials and return only an allowlisted observation or
proposal disposition. Its local stream adapter accepts already connected streams and opens no
listener. Exercise it with disposable clients; do not install a service or start model inference.

The operator/broker, its process memory, SQL database and artifact storage remain trusted. The
operator approves enrollment and creates credentials; a worker cannot enroll itself, choose another
execution/role/model, mint an assignment or acquire the broker's storage/transport credentials.
Use cryptographically random 256-bit bearer credentials, store only their digest, compare in constant
time, deliver them once through the explicit runtime control channel and never put them in model
prompts, process state, raw request captures, exception text or Git. There is no persistent signing
key and no claim that a worker-created signature establishes governance.

Authentication proves possession of the broker-issued capability for an exact scope and audience.
It does not attest model weights, OS process identity, host integrity, physical placement or an
honest operator. Compromised credentials, a malicious administrator and arbitrary code executing
inside the trusted broker remain outside this checkpoint. Real credential delivery/isolation for
model-serving workers requires its own contained runtime evidence before a live experiment.

## Invariants and required consumers

- Enrollment is explicit and immutable, before the execution's first rollout; there is no legacy
  backfill or implicit enrollment. Worker interfaces refuse unenrolled executions. Existing trusted
  offline APIs retain their legacy meaning, while every enrolled execution requires the new proof.
- Registration binds execution, authorization, broker audience, role, declared model/capabilities,
  issuance review, credential verifier and expiry. Worker IDs identify one incarnation and are never
  reused. Registration creates no funds and confers no additional tools, data, network or model rights.
- Revocation has immutable history and a checked current head. Every protected use rechecks current
  credential, expiry, Amber authority and exact assignment; a digest of an old authorization is not
  continuing permission. An expired/revoked worker cannot revive itself through a retry.
- A claim atomically creates a private assignment binding to the current state, worker, role/model,
  lease digest and deadline. Concurrent claimers cannot share ownership. An assignment refers to
  one leased action, not the complete future task/delegation lifecycle. Authored project-state
  assignments remain separate and cannot create runtime authority.
- Thread an explicit, non-serializable worker access object through protected broker calls; do not
  install ambient authority in globals, context variables, default adapters or worker-visible state.
  Old calls without that proof must fail for enrolled executions, even with a copied lease token.
- Apply the boundary at claim/observation, proposal admission/binding, resource dispatch, model and
  container execution, event commit and claim release. Bind the event actor and admitted role/model
  to assignment ownership. Independent retrospective evidence/accounting remains available after
  revocation; it cannot grant new execution or worker delivery.
- Worker requests cannot select a role/model or supply a full state, lease, database session,
  privileged receipt or adapter. Exact bounded proposal content crosses admission, then the trusted
  executor receives the admitted action separately. No worker API exposes forensic inspection,
  registry listing, credential issuance, resource funding, release authority or raw execution output.
- Retain immutable issuance/revocation/assignment and authenticated request-to-observation/decision
  lineage. Credentials themselves are never retained. Repeated request IDs must not change content
  or create a second admission/effect. No automatic retry of an uncertain external effect is added.
- Missing/corrupt records, wrong audience, wrong credential, stale lease/state, revocation, pause,
  expired scope or failed evidence transaction deny access with a generic worker error. Partial
  writes roll back; already dispatched unknown effects retain their existing resource holds.

## Acceptance and retained evidence

Required acceptance: a complete disposable-client path through credential authentication, leased assignment,
exact allowlisted observation, admitted proposal and a bounded synthetic effect/commit. Reject
forged credentials, another audience/worker/assignment/execution, role/model substitution, copied
leases, stale state, expiry, pause/revocation, duplicate delivery and old-API bypass. Verify
credential strings and private identity/assignment/request references never enter public payloads.
Cover issuer/role capacity rules, transactional rollback, competing SQL claimers/revokers and
dispatch races. Test local framed-stream size, timeout, malformed input and error boundaries.

Retain exact code/schema/migration identities, fixture issuance policy, hashed credentials only,
scope/assignment/lifecycle/request lineage, observations and effect evidence, concurrency outcomes,
failed probes, cleanup and secret-scan reports. Native stream tests prove the request protocol and
capability checks; they do not prove a model sandbox or 100% institutional replacement.

## Failure, rollback and non-goals

Disable new worker requests and retain registrations, revocations, assignments and pending effects
on rollback. Never downgrade an enrolled execution to unauthenticated access or delete a populated
identity ledger. Lost credentials require a new reviewed incarnation; a lost reply cannot justify
repeating an external effect. Revocation and external execution are not one atomic transaction;
existing stop/recheck/watchdog evidence remains necessary.

Non-goals: loaded-model or hardware attestation, general service identity/PKI, remote listeners,
automatic credential distribution, forensic service separation, a scheduler/heartbeat/replacement
runtime, multi-action task assignments, delegation/mailboxes, role-specific action masks or hydration,
automatic raw-result admission, actual models, GPUs, cloud jobs, learning or scientific claims.

## Concrete implementation and retained paths

| Store | Writer and checked readers | Visibility |
| --- | --- | --- |
| `process_worker_scopes` | `ProcessWorkerIdentityStore.enroll/scope`; `create_rollout` shares the enrollment lock | Private enrollment policy and review, immutable before the first rollout |
| `process_worker_registrations` | `issue/registration/identify` | Private scope, declared role/model/capabilities, issuance and verifier digest; credential delivered once as `ProcessWorkerAccess` |
| `process_worker_revocations`, `process_worker_heads` | `revoke/registration/identify` | Immutable revocation with mutable checked head; individual revocation neither releases a lease nor refunds a hold |
| `process_worker_lease_assignments` | `bind_claim/assignment/require` through `claim_next` | Private original state, sequence, role/model, lease hash and deadline; assignment ID only in the addressed runtime envelope |
| `process_worker_decision_bindings` | `AmberStore.admit` through `bind_decision`; `require_decision/inspect_decision` | Private exact assignment and Amber request/decision joins; retrospective observation/resource consumers retain this requirement after revocation |
| `process_worker_requests` | `ProcessWorkerBroker.request/inspect_request` | Sanitized canonical request, observation/decision/assignment joins and reply digest; no credential retained |
| `process_observations`, `process_observation_decisions`, artifact owners | Existing observation boundary plus `process_worker_request` ownership | Only the DTO enters a model; each request independently retains its observation's admitted evidence dependencies |

`worker_contracts.py` and `worker_protocol_contracts.py` define the strict records and transient
access object. `ProcessWorkerBroker` requires an explicit database, store and audience. It never
installs authority in ambient globals or exposes privileged issuance/inspection through its stream
adapter. There is no new automatic application factory, CLI enrollment, listener or scheduler.
Trusted direct APIs also accept the explicit access object; they retain assignment-to-admission
lineage without requiring an RPC receipt. RPC receipts add their own original-source and independent
ownership checks. They are not substitutes for effect, observation or resource records.

`serve_worker_stream` consumes one 4-byte-length-prefixed JSON request on an already connected
channel, with a 65,536-byte input cap, 1,200,000-byte reply cap, 10-second processing deadline and
bounded write/close. Credentials are excluded from retained request serialization and exception
context. Invalid requests receive one generic error. No unauthenticated-attempt audit stream,
network ingress rate limiting or transport-level peer attestation is claimed.

Per-incarnation control-history quotas count request records and canonical receipt plus observation
bytes conservatively, even on repeated observation sources. They are separate from SQL physical
storage metering and the authorization-wide effect account. Exact current retries add no history
or admission. Concurrent proposals recheck after acquiring the rollout lock; competing claims may
receive a denial and explicitly retry their original request. The adapter performs no automatic
retry. SQL locking follows rollout, worker head, Amber head and resource account; issuance/revocation
serialize on the enrolled scope and worker head without taking rollout locks.

`ProcessContentBoundary` rejects literal private worker/assignment/request identifiers, retained
identity record digests, credential verifiers and presented full credential bytes. This is the
existing structural boundary, not a guarantee against arbitrary encoded semantic leakage or timing
channels. Runtime control identifiers must never be prepended to model prompts. Training source
inspection checks assignment lineage through the observation boundary.

Migration `e1c87a63d942` adds seven tables with no backfill. It refuses to downgrade a populated scope.
Enrolled forks are explicitly denied until reviewed child enrollment and ownership can be preserved;
the old fork API cannot create an unauthenticated child as a fallback. A clean-release/new-credential
fixture reproduces identical public observation bytes. The subsequent
`pprl-assignment-recovery-boundary.md` adds explicit reviewed lease fencing, unknown-effect barriers,
retained-result accounting and native broker/client replacement fixtures. Heartbeat, ongoing task
ownership, automatic scheduling, unknown physical-effect resolution and scientific institutional
replacement remain later gates.
