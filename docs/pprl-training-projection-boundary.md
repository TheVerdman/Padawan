# Explicit PPRL institutional learning projections

Status: bounded offline information-boundary implementation. This is an implementation stage of
the full PPRL program, not a trainer, parameter update, live-worker experiment, or demonstration of
scientific improvement. `parameter_training_ready` is always false.

## Boundary and read/write paths

Version-1 `TrainingCompiler` products remain privileged research archives. Their row IDs, complete
states/events/outcomes, authority, evidence links, and source lineage are preserved without rewriting
historical hashes or changing the original compiler format. Restricted/raw storage is not admission.

`padawan/training/process_projection.py` provides the trusted-broker
`ProcessTrainingProjectionStore`. It is explicitly composed with the archive compiler, the same
owned evidence backend, `ProcessStore`, and any registered task schemas. No worker endpoint, CLI
training command, trainer, or automatic archive consumer is installed.

| Surface | Content and authority |
| --- | --- |
| `compile(session, source_bundle_id, now)` | Broker-only. Reconstructs the source archive and returns a private immutable receipt; this return value must not be handed to a learner. |
| `inspect_receipt(session, projection_id)` | Privileged historical reconstruction of a stored receipt; grants no current use. |
| `read_product(session, projection_id, kind, now)` | Reconstructs and revalidates current use, then returns only canonical UTF-8 JSONL bytes. |
| `process_training_projections` | Private receipt JSON, source bundle, policy and record digests, creation time. ORM mutation is denied; migration `c3e746d2a9f1` performs no backfill. |
| `artifact_information` | Source archive artifacts receive immutable forensic classification. This is retention/classification, not admission of their contents. |
| `artifact_references` | Independent `process_training_archive`, `process_training_projection`, and `process_training_forensic` ownership for archive artifacts, admitted derivatives and all their sources, and direct invocation traffic. |

Compilation checks the archive manifest and every dependent product, bounds the declared source
artifact bytes, and runs historical archive verification. It additionally reconstructs and compares
the exact canonical PPRL product rows against archived bytes. The original verifier's schema/hash/
snapshot checks alone do not establish product-to-source equivalence; this extra comparison is a
requirement of the new projection path. The older verifier remains unchanged and is not a training
admission API.

The broker requires a train partition, current unexpired Amber training permission, source/output
research and retention rights, admitted state/event content, exact observation/decision lineage,
and independently retained original sources. The observation must match its source state, content
receipt, worker, lease digest, rights, Amber sequence, policy, and chronology. Every process artifact
must independently authorize `training_projection` use. The new receipt includes its reviewed
admission and rights digests; process visibility alone does not grant training use.

For new model invocations, the prepared transport envelope is a third private source alongside the
normalized request and full result. Projection retention requires original workload and invocation
ownership and independently pins the envelope under `process_training_forensic`. A missing receipt
or mismatched invocation workload marker denies projection rather than reverting to legacy source
accounting. Exact generation input is described in `pprl-generation-workload-boundary.md`; retaining
it does not yet supply a complete behavior policy, action mask, credit model or training authority.

Current training reads can follow active, paused, or release-approved authority when the envelope
and all existing rights checks permit training. New evidence admission and live process observations
still require active authority. An Amber sequence or projection-policy change invalidates reads of
the old projection; a newly compiled receipt may carry identical public bytes under fresh authority.
Quarantine, revocation, expiry, missing classification, missing ownership, and changed source or
receipt bytes withhold prior projected data. Privileged historical inspection remains distinct.

## Public payloads and private sample lineage

The three independently versioned payloads in `process_projection_contracts.py` are:

- `ProcessTrajectoryPayload`: allowlisted initial state, exact public observations, structurally
  admitted public actions, successor states, explicit process references, and projected outcome labels.
- `ProcessVerifiablePayload`: a task validated against an explicitly registered closed schema bound
  to its generator digest, plus observations/actions/states and genuinely verifiable outcome labels.
- `ProcessForkPreferencePayload`: chosen/rejected public continuations with the exact outcomes cited
  by the archive's preference selection. Trajectory-lane outcomes cannot substitute for those labels.

Public JSONL omits private row and source identifiers, archive headers, authorization and lease
metadata, observation receipts, outcome evidence links, and forensic references. Explicitly admitted
`ProcessArtifactRef` values retain their existing scoped public fields, including execution and
content digests; this is not a general removal of all identifiers from admitted process content.
Fork actions omit private fork IDs and carry the admitted intervention. Public labels preserve
component ID, disposition, deterministic flag, finite value/uncertainty, scalar return, and preference
rank; they do not assert causal credit for individual worker decisions.

Each private product receipt binds every line offset and public-row digest to its distinct source
rollout(s) and original archive-row digest. Equal public examples remain separate lines, sorted on
public bytes first; private IDs do not reorder unequal public examples. The receipt retains archive
and source digests, source scope, current authority sequence, content/observation/binding/admission
receipts, rights, policy, exclusions, and direct forensic dependencies. Source archive artifacts are
pinned in full because replay verification depends on every product. All publication and new pins
share an outer transaction and a savepoint; a caught error cannot commit a partial projection.

Replication minima are reapplied separately to each product after observation, evidence, task,
outcome, and pair exclusions. Only sufficiently repeated instances in sufficiently populated
distributions survive. Count distinct source rollouts, not public hashes or appearances in pairs.
Private fork relationships and source identities remain available for hierarchical analysis; these
checks do not prove stochastic independence, justify flattening fork siblings into independent
samples, or replace preregistration. Token volume is not an independent-sample count.

## Bounds, failures, retained evidence, and rollback

The default pinned projection policy allows at most 1,024 steps per rollout, 4,096 examples per
product, 16 MiB of public JSONL per product, and 256 MiB of declared dependent archive artifacts.
It also pins source content, observation, public payload, and task-schema identities. These are
software content bounds, not attested CPU, memory, I/O, deadline, or dataset-wide resource quotas.
Longer resumable macro-rollouts require an explicitly evaluated projection/chunking policy; the
10M-plus-token milestone and billion-token research ambition remain in the program.

Candidate failures retain bounded private exclusions. Missing historical observations/content
receipts or training-use admission cannot be backfilled implicitly. Empty projected products are
retained outcomes and confer no new eligibility. Whole-archive integrity, policy, retention, byte/
count bound, or publication failures return a uniform denial with no private payload or chained
exception; cancellation preserves cancellation semantics with a fresh generic exception. They
publish no partial receipt. A durable per-attempt operational failure ledger remains a later runner
integration requirement; this service does not silently claim complete failure telemetry.

Retain the original archive and source database records, exact public bytes, private line joins,
policies and schema identities, rights/admission/authority decisions, exclusions, content and
observation receipts, bindings, all artifact dependencies, and the validation output. The source
receipt's `parameter_training_ready: false` must remain visible to the trusted broker deciding
whether any downstream trainer could consume these data.

Rollback disables new projection compilation/consumption while retaining existing archives,
receipts, and ownership. Do not restore full-source model inputs, delete evidence, relabel forensic
artifacts, or downgrade a populated database to erase this boundary. Migration downgrade is tested
only in disposable databases.

## Threat assumptions, acceptance evidence, and unfinished gates

The broker, database authority, artifact backend, clock, review identities, schema registry, and
adapter composition are trusted. Tests challenge malformed/stale records, forged public data,
unadmitted references, legacy records, incomplete retention, authority changes, and caught failures.
Neither a Python interface nor an Amber record authenticates a runtime principal or attests a
sandbox. Consumers must receive public bytes through a trusted composition; a private receipt or
artifact listing is not a worker capability.

`tests/integration/test_process_training_projection.py` retains synthetic repeated campaigns,
forks with different trajectory/preference outcomes, exact duplicate public examples, task-schema
and forensic-reference exclusions, separately reviewed training-use derivatives, synthetic model
traffic with independent private ownership, authority/policy changes, corrupt/missing lineage,
hash-valid but source-divergent archives, resigned public JSONL, bounds, cancellation, caught-error
and outer-transaction rollback, and immutable receipts. Migration tests check model/schema agreement
and all-revision upgrade/downgrade. Exact results belong in `pprl-execution-ledger.md`.

Literal identifier and closed-shape checks do not establish semantic provenance, honest complete
review, or absence of encoded/covert channels. Actual model inputs, loaded policy identity, action
masks, interference-aware credit, role-specific reader authorization, audited dereferences,
coordinated GC, complete forensic failure capture, Atlas import/export projections, a trainer, and
parameter updates remain unfinished. Simulated callbacks provide no Metal/CUDA, performance,
long-horizon, full-replacement, or scientific improvement evidence. Those are required later stages,
not reductions of the research program.
