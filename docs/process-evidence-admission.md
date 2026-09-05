# Reviewed process evidence at the broker boundary

Status: offline information-boundary checkpoint. This service is usable by the trusted local
composition; it is not yet the enforcement path for all PPRL state or training ingress. Those
remaining paths are tracked in `pprl-execution-ledger.md`.

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

## Admission and read path

1. The broker stores and explicitly classifies a candidate. Classification is broker-only; no worker
   endpoint is installed. Successfully finalized PPRL calls classify requests/responses as forensic.
2. A declared reviewer reviews the exact candidate and source set under a pinned admission policy.
   The review must authorize retention and internal research. Process-training projection also
   requires process rights, a training split, and the execution/distribution/Amber training grants.
3. `ProcessEvidenceStore.admit` verifies current active Amber authority, the reviewer's membership in
   both the pinned policy and envelope, exact execution/distribution/instance identity, partition,
   chronology, candidate bytes, immutable classification, and finite candidate/provenance bounds.
4. Each forensic source must match its classified reference and a completed PPRL model invocation
   in the exact target execution, completed before review. An unbound source or cross-execution
   disclosure is denied. MI, security, tool, and environment origins need their own later explicit
   origin-binding adapters; this checkpoint does not manufacture that evidence.
5. Literal source identifiers in candidate bytes are rejected. Review remains responsible for
   semantic redaction, transformations, and completeness of the declared source set.
6. The broker pins the candidate and all forensic sources to the admission and persists the receipt
   inside a database savepoint. A caught failure cannot commit partial admission ownership. Exact
   retries are idempotent; changed review content under the same process ID fails.
7. `read` returns only admitted candidate bytes. It revalidates the reference, execution, use, policy,
   source bindings, current Amber authority, and ownership. Reads and retries cannot predate actual
   admission. Pause/expiry withholds worker use while privileged review history remains available.
   Every ordinary read failure is a uniform denial with no embedded privileged validation input,
   physical path, or chained exception. Privileged inspection is a separate broker operation.
   Cancellation keeps its control-flow meaning but discards any private exception payload.

Process-training projection permission is one input to a future safe compiler; it does not grant
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
now normalized separately; the remaining state and training interfaces still need validated
projections. See `pprl-worker-output-boundary.md`.

Admission ownership is atomic in the database and is included in the catalog's retention set.
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
