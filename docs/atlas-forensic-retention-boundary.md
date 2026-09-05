# Atlas forensic retention boundary

Status: offline retention prerequisite within stage 1 of the full PPRL program. This does not
implement Atlas-to-worker admission, corpus materialization, training, or a live campaign.

## Boundary and paths

The trusted broker explicitly constructs `AtlasArtifactBoundary(ArtifactCatalog(backend))` and
passes it as `artifacts=` to `CapabilityAtlasRegistry` and `AtlasFixedTrialStudyBridge`.
`StudyEngine(artifacts=catalog)` configures the same boundary for its built-in Atlas aggregation
policy. Missing configuration rejects the three writes below and new Atlas block admission/result
sealing. Metadata-only registration and other Study policies remain available.

| Source operation and owner | Explicit dependencies retained |
| --- | --- |
| `record_trial_request`, `atlas_trial_request:<request_id>` | Edge preflight and optional effort-mapping/tool preflights, resolved from their digests and scope metadata. |
| `record_trial_result`, `atlas_trial_result:<result_id>` | All request preflights again; optional response, generation envelope, captured request, grader artifacts, and every `OutcomeEvidence.artifact_refs` entry. Failed calls retain their captured request and any error response named by the executor's `response_artifact_id`/`response_digest` fields. |
| `register_exploratory_proposal`, `atlas_exploratory_proposal:<proposal_id>` | The proposal's restricted raw source trace. Consent and excerpt digests remain declared provenance, not independently verified artifacts. |

`padawan/atlas/artifacts.py` implements retention and privileged validation. Each explicit reference
must match the catalog and configured backend, including its identity, digest, URI, media type,
size, and protected raw flags. Backend verification checks actual stored bytes; a catalog row alone
does not establish retention. `ArtifactInformationStore` records `FORENSIC` in
`artifact_information`; `ArtifactCatalog.reference` records ownership in `artifact_references`.
Classification uses `padawan.atlas.forensics/v1` and the source record's declared timestamp. An
existing class must already be forensic and cannot postdate that source. Declared timestamps are
not an independently attested capture clock.

The three source writes use a savepoint around classification, exact ownership, and source-row
publication. Catching an error or cancellation in an outer transaction cannot commit part of that
operation. Result ownership includes preflights independently of request ownership. Existing
owners are preserved. Validation checks both required request and result owners; an independent
result pin prevents collection but does not authorize ignoring missing original ownership.

`CapabilityAtlasRegistry.validate_trial_artifacts` checks the source record digest, request/result
column identity and timestamps, request lineage, required captured-call/verifier metadata, physical
sources, forensic class, and exact owner sets. It returns no worker payload. Exact retries perform
these checks again. `AtlasFixedTrialStudyBridge.record_result` checks before copying a trial into a
Study block; `AtlasFixedTrialsStudyPolicy.seal_results` checks before constructing new Study results.
Existing Study authority, suite, outcome, and fixed-denominator checks continue to apply. A read or
same-status retry of a previously completed Study is not a fresh retention validation.

## Invariants, bounds, and retained evidence

- Raw/restricted storage and forensic classification grant no worker, memory, or training admission.
  A source artifact or discoverable URI still fails the PPRL content boundary.
- Missing backend, bytes, classification, or original/exact ownership fails closed. A retry cannot
  silently classify or pin legacy records. New records establish new ownership only after their
  existing source dependencies validate.
- One operation accepts at most 64 supplied references, including duplicates, and 64 MiB of unique
  declared source bytes. Duplicate IDs must have identical metadata. These are content bounds,
  not sandboxed CPU, memory, I/O, concurrency, or campaign-budget guarantees.
- Keep original Atlas/source records, native formats and digests, catalog and backend metadata,
  physical artifacts, information-class records, exact ownership, and validation output. This
  slice adds no table, generated schema, migration, historical rewrite, or data backfill.
- A failed write publishes no new source record or partial ownership. Errors remain privileged
  and may contain backend details; this API must not be passed to a worker. A durable operational
  failure/read-audit ledger is not implemented here.

Rollback disables new consumers/writes while preserving source history, classification, and pins.
Do not delete retained evidence, relabel it as process content, restore metadata-only sealing, or
silently repair records. Any historical admission or repair requires a separately reviewed path.

## Threat assumptions and evidence limits

The broker, database, configured backend, timestamps, and composition are trusted. Tests challenge
malformed/stale references, missing storage, inconsistent declared metadata, nested explicit
artifacts, missing/excess ownership, caught errors, cancellation, and rollback. They use real
disposable local storage and synthetic research/call/verifier records. Fixture envelopes are not
actual provider captures; passing these tests cannot prove workload, consent, redaction, verifier
execution, model identity, or experiment validity.

This boundary does not parse provider envelopes or arbitrary artifact-like strings inside tool,
preflight, or verifier payloads. It retains the explicit dependencies listed above and validates
the required source metadata. Preflight `passed` and scope metadata remain broker declarations.
An upstream claim's optional extraction artifact still uses the older catalog-only path. Historical
reports, comparisons, clusters, probes, reproduction, and eligibility operations do not all perform
fresh physical retention checks. Those surfaces require separate source/use-boundary review before
they can drive a new worker or training consumer.

`TrainingFailureEligibility.direct_compiler_ingestion_permitted` remains fixed to false and requires
governed corpus materialization. `MemoryInterventionEligibility.direct_memory_write_permitted`
remains false and requires a developmental episode. No compiler/memory consumer of those eligibility
records is added. Eligibility, retention, and the separate PPRL learning projector do not establish
Atlas training permission or a completed Atlas admission adapter.

Principal authentication, per-purpose reader authorization, append-only forensic services, actual
workload/prompt binding, coordinated database/GC fencing, causal mechanistic interchange, and
institutional Atlas subjects remain later gates. The disposable GC test establishes pin behavior
for a captured reference snapshot; it does not establish safety against a concurrent DB/GC race.
Amber remains a control-plane policy boundary, with no execution-sandbox attestation.

## Acceptance evidence

`tests/integration/test_atlas_artifact_boundary.py` covers independent exact owners, default-read
denial, missing files/classes/pins, excess pins, catalog-flag substitution, source timestamp and
verifier-identity drift, failed-call request/error retention, nested evidence protection, missing
configuration, retry without repair, reference bounds, cancellation, caught-error and outer
rollback, real disposable GC, exploratory traces, and rejection at PPRL state ingress.

`tests/integration/test_atlas_registry.py` now supplies real protected preflight/response/envelope
artifacts instead of catalog-only evidence in positive fixtures. `test_atlas_study_bridge.py`
does the same for Study fixtures, including captured failed requests, and tests rejection of new
sealing when its backend, owner, or source file is missing. Exact check results and the remaining
dependency sequence are retained in [the execution ledger](pprl-execution-ledger.md).
