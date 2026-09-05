# Exact PPRL generation workload boundary

Status: first offline checkpoint of stage 2, workload/identity/resource integrity. This binds a
trusted broker's exact model input and configured transport effect. It does not attest a running
model, authenticated worker, execution sandbox, complete forensic sink, or conserved fleet budget.
The full institutional-learning and long-horizon program remains unchanged.

## Boundary and trust assumptions

`ProcessGenerationExecutor` now requires an explicitly composed `ProcessGenerationBoundary`.
The default application does not install a generation policy or launch a model. Existing calls
without a boundary fail closed. The boundary, shared executor and observation evidence must use
the same owned backend; the outer executor and external-call executor share the same database
object and adapter instance.

The broker, database administrator, artifact backend, configured adapter implementation, prompt
reviewer and HTTP transport remain trusted. Adversarial or stale inputs, mutation of caller-owned
nested values, configuration substitution, damaged receipts/pins, pause, duplicate delivery and
interruption are covered. A privileged caller can bypass Python interfaces or rewrite database
rows directly; hashes and ORM immutability do not defend against that trust-root compromise.

## Contracts, stores and read/write paths

| Surface | Contract and use |
| --- | --- |
| `ProcessGenerationPolicy` in `padawan/pprl/generation_contracts.py` | Explicit fixed instructions, source rights, sampling, optional paired schema name/content, role, worker-model digest, provider and transport-configuration digest. Its declared reviewer must be an Amber required reviewer; the review must precede the action. |
| `ProcessGenerationBoundary.request` | Constructs a fresh `GenerationRequest` whose input is the exact canonical `ProcessWorkerObservation` and whose other inputs come from the policy. This method alone admits no dispatch. |
| `ProcessGenerationBoundary.admit` | Reconstructs current observation/decision/rights/scope/lease identity, checks exact request equality and configured destination, and publishes private workload intent inside a savepoint and caller transaction. |
| `ProcessGenerationWorkload` / `process_generation_workloads` | Immutable private receipt for invocation, request, worker/role/model, execution, purpose, lease digest, observation/decision digests, policy, canonical normalized request and prepared transport artifact. Request and decision joins are unique. |
| `ProcessWorkerInvocationRow.workload_digest` | Distinguishes a new exact-admission invocation from legacy history. A missing new receipt cannot fall back to legacy two-artifact accounting. No legacy receipt is synthesized. |
| `PreparedGeneration` in `padawan/adapters/prepared.py` | Canonical UTF-8 JSON body and digest, normalized-request digest, provider/model/protocol, transport, explicit destination and configuration digest. HTTP destinations exclude embedded credentials, queries and fragments. |
| `artifact_information`, `artifact_references` | The prepared envelope is restricted/raw and classified forensic before I/O, independently pinned by `process_generation_workload`. Successful finalization also pins it with the normalized request and full result under `process_worker_invocation`. |
| `IdempotentGenerationExecutor.execute` | Retains normalized request and external-call/operation intent before dispatch. PPRL requires a prepared effect and current-admission callback; provider and purpose are checked on request-ID reuse. This remains a privileged broker interface. |
| `OpenAICompatibleClient.prepare_generation` / `generate_prepared` | Prepare without I/O, recheck configuration, send exactly the retained body once, and preserve that body as `GenerationResult.raw_request`. |
| `ProcessStore.append_event` | Uses `_invocation_forensic_bytes` to verify ownership/classification and count all three artifacts against the admitted transition allowance. Private references never enter shared event/state artifact fields. |
| `ProcessTrainingProjectionStore` | Independently retains prepared envelopes as private invocation source dependencies. Public learning JSONL excludes those references and remains `parameter_training_ready=false`. |

Migration `d8b541c9e2a6` adds the table and nullable historical marker without backfill. Generated
schemas cover the three new records. The private receipt and normalized/wire artifacts are retained
research evidence, never worker hydration or automatic learning input. Only `ProcessWorkerOutput`
returns through the worker-result interface; even the invocation ID belongs to the trusted broker.

## Dispatch and replay invariants

The wrapper detaches and revalidates the complete normalized request before its first await.
Observation reads recheck the committed state, owned lease, declared worker, active Amber sequence,
rights and retention. Fixed instructions, schema and sampling also pass the structural identifier
boundary. This catches discoverable forensic references; it does not prove semantic source
completeness or exclude encoded communication.

Exact policy equality excludes caller-added messages, metadata, tools, tool choice, provider-hosted
state and previous response IDs. The configured worker-model identity must match the admitted
execution pool and role. Prepared destination equality is stricter than merely sharing a host.
The default normalized request and prepared-envelope bounds are respectively 2,000,000 and
4,000,000 UTF-8 bytes. Both input artifacts must fit the action allowance before I/O; all three
completed artifacts must fit before output admission and event commit. This does not reserve
physical storage or bound an untrusted provider's response allocation.

The prepared HTTP path requires one attempt, a fixed protocol and disabled compatibility fallback.
It builds an explicit `httpx.Request`, bypassing inherited client cookies, headers, authentication
and query parameters, and calls `send` with redirects and ambient client authentication disabled.
Responses, chat completions and completions are tested with `MockTransport`. The adapter's explicit
API key is still privileged configuration. Credentials, HTTP transport/hooks/proxy/DNS behavior,
loaded checkpoint/tokenizer, runtime processes and effective network identity are not attested by
the configuration digest. Application-level single dispatch is not an independently enforced
network sandbox or an exactly-once provider guarantee.

Current observation/authority and physical prepared bytes are checked again after durable external
intent, immediately before calling the adapter, and before admitting output. These checks withhold
new output if the authority pauses during a call; they cannot undo an already issued effect or fence
a process after releasing the SQL transaction. Provider input/output usage must be explicit
nonnegative integers; malformed, missing or coerced accounting is denied rather than normalized to
zero. These counts remain provider declarations, not independent compute measurement.

A matching completed persisted result can be reused under current authority and original retained
lineage. Reuse preserves its completion timestamp and refuses to repair missing receipt or
invocation ownership. Existing pending, cancelled or failed process effects are never resent
automatically. An invocation left running after a persisted response may finish local admission;
an invocation with no completed external response requires later explicit reconciliation. Reusing
an old receipt under a different policy, binding, lease, owner or prepared configuration is denied.

## Failure, retained evidence and rollback

Ordinary worker-facing failure has one generic error without private exception chaining. After
intent, detailed call/invocation records and available request/response artifacts stay privileged.
Timeout, cancellation and ambiguous failures carry unknown external effects; a persisted complete
response can be recorded as a completed external effect even when process output admission fails.
These statuses describe retained knowledge, not proof of every provider action or actual cost.

Provider HTTP/JSON/usage failures retain available response bytes; a response-storage failure can
leave an external call pending with a failed invocation. There is no complete independent capture
or stale-invocation reconciler. A pre-dispatch failure can conservatively remain unknown. The later
[resource boundary](pprl-resource-boundary.md) reconciles retained complete results and permits
reviewed release of unstarted reservations; started unknown effects remain held.

Workload publication and new database pins roll back with the invocation intent, including caught
errors and outer rollback. A backend object may remain unreferenced after transaction failure.
Coordinated database/GC snapshots and complete capture of every failure remain subsequent work.
Operational rollback disables the dispatcher while preserving receipts, marker columns, original
and independent pins, and private bytes. Do not downgrade a populated database or restore unbound
PPRL dispatch to recover service. The migration downgrade is only exercised on disposable fixtures.

Retain the policy and rights, exact observation and decision, canonical normalized request,
prepared body/configuration/destination, private invocation and external-call/operation records,
classification/ownership, result and failure bytes, migration identity and validation evidence.
`tests/unit/test_prepared_generation.py` covers exact mock HTTP effects, inherited client state,
configuration drift, redirects/retries and malformed provider data.
`tests/integration/test_process_generation_workloads.py` covers exact current admission, substitution,
caller mutation, duplicate pending delivery, pauses before/during I/O, cancellation, persistence
failure, replay damage, policy drift, byte accounting and transactional rollback. Projection tests
cover independent prepared-source ownership and refusal of missing workload lineage without
disclosing private references. Exact full-suite results are recorded in the execution ledger.

## Remaining dependency

The subsequent [resource boundary](pprl-resource-boundary.md) adds explicit shared funding,
reservation and evidence-based reconciliation across attempts, workers and forks. Existing
per-action projected totals remain historical declarations alongside the shared account. Neither
checkpoint completes stage 2: authenticated runtime identity, independently enforced containment,
independent capture/metering and external-effect reconciliation still require their own evidence.

Non-goals include authenticated assignments/runtime, an attested sandbox or forensic trust root,
generic tools and their effects, executable scheduling/recovery, message transport, Atlas campaign
activation or training materialization, a trainer, parameter updates, model stress tests, cloud
resources and scientific continuity/improvement claims. Long-horizon and full-replacement evidence
remain required; fresh broker replay is only an offline engineering test.
