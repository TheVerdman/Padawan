# R2.7a training compiler verification

Date: 2026-08-01

## Outcome

Padawan now compiles a deterministic, internal-only bundle from one persisted evidence snapshot.
The bundle contains evidence, normalized episodes, SFT, preference, RLVR, negative/process,
continued-pretraining, sealed-evaluation, and reason-coded exclusion products. Compilation does not
call a trainer, update a checkpoint, publish a dataset, or make a live Inkling claim.

## Rights and role boundary

- `SourceRights` records a versioned basis, permitted uses, distribution scope, restrictions,
  reviewer evidence, and an immutable terms digest when terms are cited.
- A legacy free-text license remains available for migration audit but is not training authority.
  Non-project legacy labels migrate to `unknown` and remain review-required.
- New target attempts carry explicit output-rights evidence. Legacy target attempts without it are
  retained but excluded from every target-training lane.
- Baseline and teacher outputs remain in the evidence and normalized products while default role
  gates exclude them from target-training products.
- Directly teacher-influenced revision and treatment attempts are linked to the intervention and
  require a teacherless replay for default admission.
- A prohibited rights decision has no permitted uses and requires an attributable, timestamped
  review.

## Compiler and source guarantees

- Every compiled row binds its compiler version, source evidence references, source record
  digests, and applicable rights digests.
- Every product is canonical JSONL in a restricted raw-data artifact, including empty products.
- The bundle manifest binds the source watermark and snapshot digest, checkpoint/tokenizer
  identities, verifier and environment fingerprints, policy identities, artifacts, and counts.
- Verification rereads and validates the manifest and every product, including content identity,
  artifact-catalog metadata, row contracts, canonical order, counts, bundle identity, and the
  reproducible database snapshot.
- Continued-pretraining content has a separate append-only document and decision registry with
  quality evidence, rights gates, contamination status, supersession, and content deduplication.
- Sealed, shadow, quarantined, contaminated, unreviewed, ineligible, unregistered-checkpoint,
  baseline, teacher, and private-reasoning candidates cannot silently enter target products.

## Verification performed

`make check` passed:

- Ruff formatting and lint: passed across 167 files;
- generated JSON Schemas: 51 matched their Pydantic contracts;
- strict mypy: 96 source files passed;
- hermetic suite: 134 passed, 6 PostgreSQL/live/Lean tests deselected by the default gate.

The separately selected pinned Lean integration also passed: 1 real-kernel test passed, with 138
unselected tests. It was run outside Codex's outer filesystem sandbox so Padawan's required macOS
`sandbox-exec` isolation could initialize; both generated valid proof families were accepted and
the invalid proof was rejected by the kernel.

## External boundary

This slice made no OpenAI or Anthropic request, read no external secret file, performed no live GCS
mutation, and did not rerun the PostgreSQL concurrency gate. It did not modify or execute Magellan.
Inkling serving, an external training backend, an externally produced checkpoint, and the appellate
briefing pack remain separate future gates.
