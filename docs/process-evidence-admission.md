# Reviewed process evidence at the broker boundary

Status: offline information-boundary checkpoint. The trusted local composition now uses this
service for new state/event references, worker observations, and the explicit PPRL training
projection. Legacy archives and Atlas paths do not gain implicit admission. Remaining paths are
tracked in `pprl-execution-ledger.md`.
An explicitly configured Atlas source boundary now supports separately reviewed institutional
findings. See [Atlas process evidence](atlas-process-evidence-boundary.md); raw records and
eligibility flags still have no implicit admission.

## Contracts and storage

`ArtifactInformationStore` in `padawan/artifacts/information.py` records an immutable information
class independently of the artifact's storage flags. `artifact_information` binds a complete
`ArtifactRef`, classification, classifier identity, reason, and timestamp by digest. Existing
artifacts receive no classification during migration. Missing classification denies process use.

`forensic` requires protected raw storage. `process_candidate` prohibits raw storage but confers no
admission. Classification cannot change in place; repeated declarations retain the first record.
Catalog operations continue to verify authoritative backend metadata and actual bytes.

`ForensicArtifactRef` contains a raw reference and its classification digest and is privileged.
`ProcessArtifactRef` instead identifies an admitted derivative by an opaque process identifier,
exact execution, content digest, media type, and byte count. It carries no forensic reference,
physical URI, review details, source count, or source-classification digest.

`ProcessEvidenceAdmission` is a reviewed request that binds exact candidate bytes, classification,
the complete declared forensic source set, reviewer, rationale, contamination partition, rights,
and allowed uses. It retains the complete pinned `EvidenceAdmissionPolicy`. A
`ProcessEvidenceAdmissionRecord` additionally binds actual admission time and Amber lifecycle
sequence. `process_evidence_admissions` stores the immutable receipt and digest. Its timestamps and
queryable identity columns are checked against the receipt when read.
Version-2 `AtlasProcessEvidenceAdmissionRecord` adds a private, explicitly reviewed Atlas origin to
the unchanged candidate review. It retains exact source/context identities and a pinned disclosure
policy in the same table. Version-1 contracts/bytes are unchanged, and version-2 data cannot fall
back to a legacy interpretation when the origin is missing. Only the existing process reference and
candidate bytes reach worker consumers.

## Admission and read path

1. The broker stores and explicitly classifies a candidate. Classification is broker-only; no worker
   endpoint is installed. Successfully finalized PPRL calls classify requests/responses as forensic.
2. A declared reviewer reviews the exact candidate and source set under a pinned admission policy.
   The review must authorize retention and internal research. Process-training projection also
   requires process rights, a training split, and the execution/distribution/Amber training grants.
3. `ProcessEvidenceStore.admit` verifies current active Amber authority, the reviewer's membership in
   both the pinned policy and envelope, exact execution/distribution/instance identity, partition,
   chronology, candidate bytes, immutable classification, and finite candidate/provenance bounds.
4. In version 1, each forensic source must match its classified reference and a completed PPRL model invocation
   in the exact target execution, completed before review. An unbound source or cross-execution
   disclosure is denied. MI, security, tool, and environment origins need their own later explicit
   origin-binding adapters; this checkpoint does not manufacture that evidence. Explicit version-2
   Atlas reviews instead require a configured source/target disclosure policy, exact retained
   native/context identities, complete forensic dependencies, source/output rights, and partition
   isolation. These Atlas derivatives are process-use-only pending separate training materialization.
5. Literal source identifiers in candidate bytes are rejected. Review remains responsible for
   semantic redaction, transformations, and completeness of the declared source set.
6. The broker pins the candidate and all forensic sources to the admission and persists the receipt
   inside a database savepoint. A caught failure cannot commit partial admission ownership. Exact
   retries are idempotent; changed review content under the same process ID fails.
7. `read` returns only admitted candidate bytes. It revalidates the reference, execution, use, policy,
   source bindings, current Amber authority, and ownership. Reads and retries cannot predate actual
   admission. Pause/expiry withholds worker use while privileged review history remains available.
   Explicit `training_projection` reads may also use paused or release-approved Amber authority,
   provided all existing training/split/rights checks still pass; expiry, quarantine, and revocation
   deny use. New evidence admission still requires active authority. Training retention has its own
   owner and explicit use; it cannot grant process/hydration access during pause.
   Every ordinary read failure is a uniform denial with no embedded privileged validation input,
   physical path, or chained exception. Privileged inspection is a separate broker operation.
   Cancellation keeps its control-flow meaning but discards any private exception payload.

Process-training projection permission is one input to the explicit projection broker; it does not grant
training eligibility to a rollout, authorize a trainer, or imply that any parameters were updated.
Execution-scoped references require explicit admission for another execution, including a fork with
a different execution manifest. No cross-project memory disclosure is implicit.

## Threat assumptions and limitations

The broker composition, configured policy, declared review provenance, database administration, and
broker-owned filesystem are trusted. Supplied references, receipt bodies, and damaged persisted
records are checked. Worker code must not receive the broker object, raw backend, or inspection
methods. A Python object boundary is not containment. Reviewer-name validation is declared
control-plane policy; authenticated principals, independent review identity, service credentials,
and an attested sandbox remain separate requirements.

Digest matching proves byte identity, not semantic safety, honest review, source completeness, or
causal influence. Literal-reference checks do not prove removal of encoded references or covert
channels. No process-state hydration, arbitrary nested payload, public export, or existing training
row is automatically safe because these new contracts exist. The PPRL model-result interface is
normalized separately; allowlisted worker and institutional learning projections now have their
own policies and private receipts. See `pprl-worker-output-boundary.md`,
`pprl-worker-observation-boundary.md`, and `pprl-training-projection-boundary.md`. These remain
structural broker boundaries, not semantic provenance or execution isolation.

New state/event content now also requires structural admission and exact policy receipts, described
in `pprl-content-admission.md`. This does not change the explicit review requirement for evidence
or turn literal-reference checks into a semantic-redaction guarantee.

Admission ownership is atomic in the database and is included in the catalog's retention set.
New process state/event ownership also pins the candidate and declared source set. SQLite now
explicitly begins outer transactions so a successful savepoint cannot escape a later rollback;
the original checkpoint's validation had not exercised that legacy-driver failure. See
`pprl-reference-ingress-boundary.md` for ingress, claim, retry, fork, and rollback behavior.
Concurrent GC using a stale externally gathered set remains unresolved; the local file lock is not
a coordinated database/GC transaction. Raw sources may stay pinned for the lifetime of their
admission evidence. A later retention policy must preserve required lineage and distinguish content
deletion from authority tombstones.

## Validation, failure, and rollback

`tests/integration/test_process_evidence.py` retains synthetic classified sources, a simulated model
call, reviewed derivatives, worker projection assertions, forged metadata/authority/scope probes,
rights and partition rejection, pause/expiry, immutable/corrupted receipts, temporal rejection,
missing ownership, GC retention, provenance quotas, and injected failure after pin creation.
Malformed privileged-record tests also verify that worker errors cannot echo forensic values.
Migration tests cover schema matching and upgrade/downgrade through every revision. Exact check
results are recorded in the execution ledger; no real inference is needed for these tests.

Failures deny admission or worker reads and preserve privileged evidence already committed. A
rollback must first disable new admission callers and preserve the database, artifact metadata,
and bytes. Reverting code or dropping the new tables never justifies treating legacy artifacts as
process-safe. The migration's destructive downgrade is exercised only on disposable test databases;
no production migration or downgrade is performed by this checkpoint.
