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
source, license, contamination scope, and retirement data. Leases include owner, opaque token,
expiry, and attempt count. `exposures` records which surface was shown to which student state. The
registry writes answer-bearing exposure and closure retirement in one transaction; student-aware
leasing excludes previously exposed instance groups.

## Student state

`students` points to one canonical immutable `student_states` record. A state contains checkpoint
and runtime identity, parent and branch, compacted working state, lesson references, hypotheses,
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
provenance, and consolidation proposals.

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

## Operations, artifacts, and provenance

`runs` and `run_transitions` are the recoverable state machine. `active_student_id` is unique while a
run is nonterminal. A sequence uniqueness constraint prevents duplicate transition positions.
`external_calls` provides the idempotency boundary. `workers` stores heartbeat and active run.
`review_queue` stores adjudication work.

`artifacts` holds immutable SHA-256 metadata; `artifact_references` links blobs to owners and is the
source of truth for safe garbage collection. `provenance_heads` serializes each stream while
`provenance_events` stores the immutable cryptographic chain.

The initial Alembic migration is authoritative for a new database. CI compares it with SQLAlchemy
metadata and exercises downgrade/upgrade from every checked-in revision.
