# Reviewed Atlas evidence in persistent institutions

Status: offline stage-1 source/admission boundary. Separately reviewed Atlas findings can now become
execution-scoped process evidence. Raw traffic, source identifiers, context snapshots, and review
records remain privileged. This does not implement Atlas training materialization, developmental
memory interventions, institutional Atlas subjects, a live campaign, or a scientific result.

## Explicit composition and admission

The trusted broker constructs `AtlasEvidenceSourceBoundary(catalog=..., policy=...)` and supplies it
as `atlas=` to `ProcessEvidenceStore`. Both must share the configured backend. No default disclosure
policy is installed. `AtlasEvidenceDisclosurePolicy` names one target process execution and its
contamination scope, a declared intervention, allowed reviewers, exact source campaign/condition/
suite/research-execution scopes, reviewed output rights for each scope, and a validity period.

`describe(result_ids=...)` prepares privileged source descriptions for review. It is not a worker
API or an admission. A reviewer separately reviews the candidate bytes, their complete declared
source set, candidate rights, target scope, and disclosure policy. `admit_atlas(review=..., origin=...)`
then validates that exact review through the existing process-evidence broker. An Atlas eligibility
flag, report, corpus label, or source-storage flag cannot invoke this step automatically.

| Store or contract | Read/write boundary |
| --- | --- |
| Native Atlas request/result, run, campaign, suite, item, allocation, execution, harness, binding, and governance records | Read as privileged sources with their native schemas/identities. Source-record hashes and matching query columns are checked. Required joins and partition membership must agree. |
| `AtlasTrialEvidenceSource` | Pins result ID/digest/full-record digest, request ID/full-record digest, and a context digest covering the source manifests, indexed memberships, captured-call columns, and explicit artifact metadata. |
| `AtlasEvidenceOriginReview` | Binds the exact existing `ProcessEvidenceAdmission` digest to the complete selected trial set and pinned disclosure policy. Remains private. |
| `AtlasProcessEvidenceAdmissionRecord` | Version-2 private receipt in existing `process_evidence_admissions`, containing the unchanged candidate review, explicit Atlas origin, actual admission time, and target Amber sequence. |
| `artifact_information` and `artifact_references` | Existing classification and original Atlas owners must validate. Admission and downstream process owners independently retain the candidate and every explicit forensic dependency. |
| `ProcessArtifactRef` and evidence `read` | Worker-visible opaque execution/content reference and separately reviewed candidate bytes only. Native source IDs, URIs, reviewer details, and origin receipts are excluded. |

Version-1 review/receipt models and schemas are unchanged. The receipt reader dispatches explicitly
on version; missing/unknown version-2 origin data does not fall back to a version-1 interpretation.
An existing process artifact ID cannot be reused for another review or origin. No table, migration,
historical rewrite, or backfill is introduced. Five additional generated schemas describe the new
Atlas contracts; they do not replace native Atlas or prior process schemas.

## Authority, partition, and information invariants

The existing broker still requires exact target execution/distribution/instance identity,
contamination scope, current active Amber authority for admission, declared reviewer membership,
candidate classification/bytes, reviewed research and retention rights, chronology, and bounds.
The Atlas extension adds:

- Source scopes must appear in the explicitly composed disclosure policy. Dataset governance must
  permit evaluation, retention, and internal research; source output rights must separately permit
  retention and internal research. Rights reviews precede the disclosure policy. Atlas currently
  has no native per-result provider-output-rights receipt, so this explicit reviewed scope grant is
  required; provider/model naming cannot manufacture output rights.
- Sources must be ready development/adaptive suites with consistent campaign/run/allocation and
  execution identities and no declared contamination. Challenge/sealed sources and registered
  item/prompt overlaps with those lanes are denied. Indexed suite membership is checked against its
  frozen manifest; campaign promotion memberships are also excluded.
- Targets are train or adaptive-development process executions. Any adaptive source or indexed
  adaptive overlap requires an adaptive-development target. Validation/sealed destinations require
  a later controlled mature-memory protocol and are not admitted by this policy.
- Source result times precede review. Native record/context digests, all explicit classified source
  artifacts, original exact ownership, source scope/rights, and current disclosure policy are
  revalidated on admission, retry, read, and downstream process ownership. Changing the configured
  policy or reaching its expiry withholds previously admitted bytes.
- Reviewed derivatives are process-use-only. `training_projection` reads and reviews requesting
  that use are denied, including when the ordinary PPRL projection broker encounters these refs.
  Atlas training eligibility still requires a separate governed materialization path. No
  `AtlasTrainingEligibilityRow` or `AtlasMemoryEligibilityRow` consumer is added, and developmental
  memory still requires its own governed episode. This path admits an institutional evidence
  artifact through a separate review and normal PPRL state transition; it does not write that
  developmental memory store or certify a memory intervention.
- Literal native record IDs, source/context/policy digests, physical source references, and other
  enumerated source identifiers cannot appear in candidate bytes. The source set must match the
  selected trials exactly; omitting or adding an unrelated forensic dependency denies admission.

Multiple selected trials retain distinct origin identities even when their artifact bytes are
identical. This proves neither independent sampling nor causal attribution. A reviewed factual
finding does not become a comparative distribution, verified model improvement, or parameter update.

## Bounds, failure, retained evidence, and rollback

One origin selects 1–16 distinct canonically ordered trials. A disclosure policy allows 1–16 exact
source scopes and at most 16 declared reviewers. Each reconstructed trial context has a 4 MiB
canonical-content bound; the existing evidence policy independently bounds total candidate bytes,
forensic-reference count, and forensic-source bytes. Atlas's original per-record retention bounds
also apply. These are software content bounds, not enforced process-memory, CPU, I/O, deadline,
network, or campaign-resource limits. Source inspection and metadata queries may do work before a
content bound is reached; resource conservation remains a later runtime gate.

Admission pins and the version-2 receipt share the existing outer savepoint. Caught exceptions,
cancellation, and outer rollback cannot publish a partial admission. Retries never repair missing
classification, origin data, native source context, or original ownership. Privileged inspection
may reconstruct old receipts independently of current worker-use authority. Worker reads return
only candidate bytes or the existing uniform denial/cancellation without privileged chained errors.
Privileged source-preparation/admission errors remain broker-only. A durable denial/read-audit
ledger is not implemented by this slice.

Retain native Atlas/database records and digests, actual source and candidate bytes, classifications,
full source/output and candidate rights, disclosure/admission policies, review and origin receipt,
target Amber lineage, original and independent owner sets, fixtures, and exact validation output.
Rollback disables new admission/consumers while preserving those records and pins; never relabel
forensic artifacts, strip origins, restore metadata-only admission, or delete evidence to downgrade.

## Threat assumptions and remaining limits

Broker composition, configured policies, database, backend, clock, and declared reviewer provenance
are trusted. Reviewer names and policy strings are control-plane declarations; this service does
not authenticate a principal, attest producer/workload identity, enforce a sandbox, prove consent,
or independently verify the declared intervention. Preflight/captured-call/verifier checks retain
their [existing storage/metadata limits](atlas-forensic-retention-boundary.md).

Candidate review remains responsible for semantic redaction, truthful findings, completeness of
the declared source set, and compliant interpretation of rights. Literal checks do not detect
encoded/covert channels, undocumented sources, semantic near-duplicates, or all possible aliases.
Indexed item/prompt overlap checks do not prove absence of exposure in external or unregistered
datasets. Process-use-only references do not automatically decontaminate later outputs influenced
by those findings; causal influence tracking and training-materialization rules remain required.
Version-1 source-free reviews retain their existing explicit-review trust assumption and do not
gain an automatic Atlas-origin detector.

Source context is reconstructed from trusted retained database records rather than independently
attested execution. Complete forensic capture, authenticated readers, a separate forensic service,
coordinated DB/GC fencing, conserved resources, executable hydration/recovery, full worker replacement,
institutional Atlas subjects, MI interchange, trainer integration, and the 10M+ token milestone
remain required later gates of the full program.

`tests/integration/test_atlas_process_evidence.py` uses real disposable local storage with synthetic
Atlas calls/verifiers and process authority. It covers exact scope/source/rights/origin joins,
worker payload separation, process ownership, multiple origins with shared bytes, protected and
adaptive overlap, compatibility, policy expiry/change, damaged source context, training denial,
limits, and transactional failures. It establishes no actual model, Metal/CUDA, cloud, long-horizon,
or scientific result. Exact results are retained in [the execution ledger](pprl-execution-ledger.md).
