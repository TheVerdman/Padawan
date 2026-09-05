# PPRL reviewed references and transactional ownership

Status: the reference/ownership portion of information-boundary checkpoint 3, validated offline.
The subsequent structural content layer is described in `pprl-content-admission.md`. Offline
observation and training projections now exist; executable replacement hydration remains unfinished.

## New use and historical reconstruction

`ProjectStatePayload` and `ProcessEventRecord` accept `StoredProcessArtifactRef` for historical
parsing. Its legacy `ArtifactRef` alternative grants no new admission. Historical wire envelopes,
fields, and digest calculations remain unchanged; generated schema hashes identify the additive
process-reference alternative. Schema versions are not information-class or safety assertions.

New `ProcessStore.create_rollout` and `append_event` operations require `ProcessArtifactRef` in
top-level artifact fields. Generic references are rejected regardless of caller flags. Exact
duplicate references are canonicalized; conflicting declarations for one process ID are denied.
The broker must supply `ProcessEvidenceStore` when using references. Missing configuration denies.

Admission rechecks exact scope, declared authority, review, classification, backend bytes, source
provenance, and admission ownership. Initial referenced bytes must fit the initial declared usage
and Amber artifact cap. Transition bytes use the action reservation, including privileged invocation
accounting. These checks do not yet conserve every resource across workers, retries, and forks.

`get_state` and `replay` remain privileged reconstruction APIs. Claims, transitions, and idempotent
create/event retries revalidate relevant references and ownership. A legacy raw state can round-trip
with its original JSON and digest while being refused for a claim. Missing dependencies deny a claim
instead of producing a partially usable state.

## Retention and atomicity

The broker-only `ProcessEvidenceStore.retain_for_process` pins the candidate and all declared
forensic sources with state/event ownership. Source IDs remain in private catalog ownership, never
in the process reference. `validate_process_ownership` verifies the complete set. Admission ownership
is independently required; state ownership cannot manufacture admission.

Initial creation, event append, claim, and the whole multi-child fork use database savepoints. A
failure rolls back mutations and new ownership even when a caller catches the error and commits
unrelated outer work. The outer transaction retains final commit authority.

A regression exposed sqlite3 legacy transaction behavior: releasing a first savepoint could commit
writes before an outer transaction had begun. `Database` now explicitly lets SQLAlchemy issue SQLite
`BEGIN`, including before reads and savepoints. Tests cover outer rollback after successful process
creation/evidence admission, partial ownership failure, and a failing second fork child. PostgreSQL
execution was not included in this offline validation.

Each fork child independently passes initial-state admission. Parent-scoped references are not
silently transferred into a different child execution. If a later child fails, earlier children,
new execution registrations, parent event/state, and new ownership roll back. General reviewed
evidence transfer across executions remains a separate policy requirement.

## Limits and rollback

The broker, review configuration, database, and owned backend remain trusted. These are control-plane
interfaces, not authenticated worker endpoints or an attested sandbox. Privileged inspection and
ownership methods must not be handed to workers. State/event shapes, extensions, and memory/evidence
strings now have structural admission and receipts. The subsequent worker projection is described in
`pprl-worker-observation-boundary.md`; the separately admitted learning interface is described in
`pprl-training-projection-boundary.md`. None of these boundaries makes arbitrary broker inputs,
exports, or historical training products worker-safe,
proves semantic redaction, or excludes encoded communication channels.

Concurrent GC using a stale externally collected reference set remains unresolved. A savepoint and
backend file lock are not a joint database/GC transaction. No production data migration, live worker,
model call, sibling edit, or cloud operation was performed.

Failures preserve the committed head and evidence. Before rollback, disable new reference consumers
and preserve the database, private ownership, classifications, and bytes. Do not restore permissive
legacy ingress or revert SQLite transaction control while relying on nested rollback guarantees.
Keep unavailable dependencies distinguishable from usable state.

`tests/integration/test_process_reference_ingress.py` retains synthetic adversarial references,
private ownership assertions, historical JSON/digest comparisons, and partial-write/second-child
failures. Shared fixtures are in `tests/pprl_evidence_helpers.py`. Exact validation is in the ledger.
