# Data model

Padawan has two validation layers. Pydantic v2 records define immutable serialized contracts;
SQLAlchemy rows provide transactional indexing and ownership. JSON Schemas generated from the
durable public records are checked into `schemas/` with SHA-256 fingerprints in `schemas/index.json`.
All durable records use contract version `1.0.0`; incompatible literal versions are rejected.

## Corpus and exposure

`competencies` stores versioned titles, prerequisites, grader requirements, teacher modes, and
difficulty calibration. `template_families`, `instance_groups`, and `corpus_items` enforce the
hierarchy `competency → template family → instance group → item`. Composite foreign keys and pool /
visibility checks prevent a lineage from crossing training, evaluation, and sealed visibility.

An item carries its generator seed/version, difficulty, expected answer or verifier, pool, status,
source, content-digested rights manifest, optional legacy license label, contamination scope, and
retirement data. Leases include owner, opaque token,
expiry, and attempt count. `exposures` records which surface was shown to which student state. The
registry writes answer-bearing exposure and closure retirement in one transaction; student-aware
leasing excludes previously exposed instance groups.

## Student state

`students` points to one canonical immutable `student_states` record. A state contains checkpoint
and runtime identity, its `target`/`baseline` research role, parent and branch, compacted working
state, lesson references, hypotheses,
competency estimates, experiment identity, lifecycle status, and a semantic state hash. ORM hooks
reject update or deletion of a state row.

`state_forks` references one parent plus exactly one treatment and one control child. Creation is a
single transaction with deterministic idempotency when a fork ID is reused. Branch-scoped memory
writes reject a state from the other side. Parent content is inherited identically; divergence is
recorded separately as the intervention.

## Episodes and evidence

`episodes` is the canonical research envelope. Child tables index `attempts`, `grades`,
`teacher_interventions`, `revisions`, and `transfer_trials`; every child also stores its full typed
record. A failed or review-required episode is valid even when later-stage child identifiers are
absent.

An `AttemptRecord` retains rendered messages and prompt, sampling configuration, request/response
identity, runtime identity, raw artifacts, parsed public derivation, final answer, calls and
observations, timing, optional GPU telemetry, token/logprob arrays, optional channel spans, and an
explicit capability matrix. Validators require aligned token/logprob arrays, bounded nonoverlapping
spans, and a restricted artifact when private reasoning exists.

A `GradeRecord` distinguishes deterministic outcome from infrastructure failure. It carries parsed
answers, per-step validation, first invalid public step, controlled outcome, and evidence objects.
Teacher records cite evidence IDs, public steps, private spans, or prior lessons and record the
comment validator result. No rejected teacher output is eligible for memory.

`DevelopmentalEpisode` links state before/after, attempts, grade, intervention, revision, transfer,
memory writes, exposures, retirement, controlled student/teaching/system outcomes, metrics,
provenance, and consolidation proposals. Role is repeated on runs, attempts, and episodes so replay
and exports do not infer authority from provider names. Baseline evidence is diagnostic and cannot
silently enter target memory.

## Domain and reward contracts

`DomainSpec` identifies a versioned task-semantic package and its evidence hierarchy.
`EnvironmentSnapshot` fingerprints dependencies, tools, platform, network posture, and supporting
artifacts. `TaskManifest` carries split, lineage, source, a versioned rights manifest, and freshness
scope.
`VerifierResult` distinguishes `verified`, `rejected`, `unknown`, and `infrastructure_failure` while
retaining scoped evidence.

`verifier_results` stores append-only, digest-protected objective observations before any reward is
computed. `reward_policies` is immutable by `(policy_id, version)` and retains every component
normalization, coefficient, and missing-data action. `rewards` stores hard gates, raw and normalized
components, policy/input/record digests, and optional derived utility. Recomputation reloads the
policy and evidence-shaped inputs and verifies the complete stored record. A failed hard gate or a
required missing component makes scalar utility unavailable rather than zero.

`training_eligibility_decisions` cites a source reward and remains distinct from reward calculation.
It maintains disjoint allowed and excluded lanes across continued pretraining, SFT, preference,
RLVR, process, and evaluation-only uses. A hard-gate failure can remain available for evaluation but
cannot be admitted to a training lane.

`training_source_documents` stores immutable content, source/version, artifact, rights, and quality
evidence for separately admitted continued-pretraining material. Append-only
`training_source_decisions` governs active, review-required, quarantined, and retired states.
`training_bundles` binds a deterministic source snapshot to one canonical manifest artifact and all
restricted product artifacts. Product rows and exclusions retain compiler version, source evidence
references, source record digests, and rights digests.

## Memory and experiments

`lesson_versions` is append-only by `(lesson_id, version)`. A lesson records competency, error class,
general rule, applicability, exclusions, evidence, source episodes, teacher, transfer history,
harmful retrieval count, branch/lineage, confidence, and status. Retrieval decisions persist both
selected and rejected candidates. `memory_snapshots` support rollback by creating new restoration
versions and invalidating additions made after the snapshot; history is not erased.

`experiments` stores the parent state, seed, design, and lifecycle. `experiment_blocks` stores the
counterbalanced assignment, treatment/control outcomes, contamination flag, and infrastructure
attrition. Analysis excludes unmatched, contaminated, or infrastructure-failed blocks and reports
the exclusion counts.

## Studies and scheduled evaluation

`studies` stores one immutable, versioned study manifest and suite digest. `study_experiments`
binds persisted experiments to conditions, frozen checkpoints, research roles, environment
fingerprints, and optional assignment propensities. Study aggregation reads the original experiment
blocks; it reports missingness and exclusion classes and never manufactures a paired outcome.

`evaluation_trials` is a due-time queue for delayed retention and interference probes. Each row
binds a source episode, exact immutable state snapshot, source competency, fresh evaluation-only
item, assignment seed/propensity, and environment fingerprint. Interference trials additionally
bind a complete episode in another competency and the post-interference snapshot. Trial and item
leases share an opaque token and recover together. Completed outcomes retain explicit missing
reasons, contamination checks, verifier result IDs, and the matching prompt exposure.

## External checkpoints

`checkpoints` is a registry for externally produced frozen model artifacts. It retains parent
lineage, model/tokenizer digests, optional training-bundle digest, compatibility metadata, and one
of candidate, evaluating, promoted, rejected, quarantined, or revoked status. It is not a trainer.

`evaluation_suites` pins task and environment manifests. `checkpoint_evaluations` can cite only a
registered study with the same suite digest and hard gates backed by persisted verifier results.
`checkpoint_promotion_policies` defines required metrics, regression tolerances, and minimum gains.
`checkpoint_comparisons` records metric deltas, explicit missingness, hard-gate failures, and the
lexicographic recommendation. `checkpoint_decisions` forms the immutable lifecycle audit trail.
Comparison recomputation verifies source evaluation, policy, and record digests; promotion decision
verification rechecks that the same candidate and recomputed recommendation support the action.

## Operations, artifacts, and provenance

`runs` and `run_transitions` are the recoverable state machine. `active_student_id` is unique while a
run is nonterminal. A sequence uniqueness constraint prevents duplicate transition positions.
`external_calls` provides the idempotency boundary. `workers` stores heartbeat and active run.
`review_queue` stores adjudication work.

`artifacts` holds immutable SHA-256 metadata plus backend-specific location evidence. Local storage
records a filesystem-relative blob identity; GCS records bucket, object, generation,
metageneration, ETag, CRC32C/MD5 when supplied, size, and project. `artifact_references` links blobs
to owners and is the source of truth for safe garbage collection. `provenance_heads` serializes
each stream while `provenance_events` stores the immutable cryptographic chain.

The Alembic revision chain is authoritative for a new database. The Round 2 role migration adds
non-null role columns with a target-compatible default; the R2.3 revision adds verifier, reward,
study, scheduled-evaluation, suite, checkpoint, comparison, and decision tables. CI compares the
head revision with SQLAlchemy metadata and exercises downgrade/upgrade across every checked-in
revision.
