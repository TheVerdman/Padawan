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
provenance, domain-verifier evidence, and consolidation proposals. Role is repeated on runs,
attempts, and episodes so replay and exports do not infer authority from provider names. Baseline
evidence is diagnostic and cannot silently enter target memory.

## Domain and reward contracts

`DomainSpec` identifies a versioned task-semantic package and its evidence hierarchy.
`EnvironmentSnapshot` fingerprints dependencies, tools, platform, network posture, and supporting
artifacts. `TaskManifest` carries split, lineage, source, a versioned rights manifest, and freshness
scope.
`VerifierResult` distinguishes `verified`, `rejected`, `unknown`, and `infrastructure_failure` while
retaining scoped evidence.

The appellate package adds content-addressed court-pack, closed-record, scenario, student-draft,
system-bound submission, adjudicator-draft, semantic-assessment, citator-assessment, and
verification-bundle contracts. Citation resolution, quotation fidelity, proposition support,
applicability, and currentness remain distinct results. The first court pack has no dependable
citator, so currentness is structurally `unknown` rather than inferred from the closed opinion
inventory.

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
`authored_demonstrations` stores verifier-backed project-authored gold separately from attempts. It
binds a training-visible curriculum item, exact messages and target events, final answer, verifier
result/digest, quality evidence, and source/output SFT rights.
`training_bundles` binds a deterministic source snapshot to one canonical manifest artifact and all
restricted product artifacts. Product rows and exclusions retain compiler version, source evidence
references, source record digests, and rights digests.

## Persistent-process rollouts and Amber

`process_distributions` and `process_programs` are immutable, content-digested definitions.
Distributions bind source rights, all four clean partitions, generator and contamination identity,
difficulty strata, seed namespace, and replication minima. Programs bind a distribution to
episodic or continual persistence, worker roles and tools, dynamic-capacity bounds, reward
authority, and an optional local-regret contract. `project_instances` retains every deterministic
sample and exact task/environment digest.

An immutable `process_executions` record binds one program, instance, process policy, worker-model
pool, output-rights declaration, environment, seed, and Amber authorization. `process_rollouts` is
the leased mutable head. Canonical content lives in immutable `process_states` and append-only
`process_events`; every event binds its parent/result states, rollout status, artifacts, optional
worker/research invocation, and one admitted Amber decision. `process_forks` and
`process_fork_children` create paired continuations from an identical persisted state.

Amber separates immutable authority from its transactional status head.
`amber_authorizations` stores the exact envelope; `amber_authorization_events` is its append-only
lifecycle; `amber_authorization_heads` supports atomic admission; and
`amber_admission_decisions` stores both the complete action request and its digest-protected
decision. `process_worker_invocations` binds model I/O to that decision and to the current rollout
lease. Pause, quarantine, expiry, revocation, checkpoint retention, and release authority are not
inferred from a successful outcome.

`process_outcomes` records raw verifiable, empirical, adjudicated, or hybrid assessments.
`process_training_eligibility` is a separate append-only policy decision that cites outcomes,
rights, evidence, and allowed learning lanes. This prevents assessment, scalarization, and training
admission from collapsing into one mutable label.

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

`harness_profiles` stores immutable `(profile_id, version)` contracts under a content digest. A
profile binds its open tier/purpose, continuation and reasoning-retention semantics, context /
truncation / compaction policy, versioned prompt and tool identities, explicit resource budgets,
and optional later instrumentation contracts. `research_executions` binds that profile digest to
the exact student and auxiliary model-serving identities, checkpoint, quantization, learned
parent-state ID/hash, model-server and transport-edge artifacts, task/corpus, effective
workflow/condition/sampling parameters, environment, and seed used by one run configuration.

`runs.research_execution_digest` and `experiments.research_execution_digest` are nullable only for
backward compatibility with evidence created before this contract. New live developmental work
sets both to the same registered execution. Completed or failed `DevelopmentalEpisode` records and
workflow provenance repeat the digest. A legacy null is preserved as missing provenance and cannot
authorize a new comparison.
Controlled run creation validates its domain, pool, teacher mode, treatment/control conditions,
and retry budget against that execution. Controlled experiment creation validates its design
conditions as well; the foreign key alone is not treated as evidence that payload and manifest
agree.

## Studies and scheduled evaluation

`studies` stores one immutable, versioned study manifest and suite digest. `study_experiments`
binds persisted experiments to conditions, frozen checkpoints, research roles, environment
fingerprints, research-execution digests, open factor values, and optional assignment propensities.
Study aggregation reads the original experiment blocks; it reports missingness, exclusion classes,
observed research-axis differences, declared comparison axes, and provenance gaps. It never
manufactures a paired outcome or treats a missing control as equality.

The active-to-complete transition first requires an outcome for every bound block. It then stores in
`study_results` one immutable, content-addressed aggregate per `(study, condition, checkpoint)`.
Each result binds the study/suite/policy identity, source experiment and execution IDs, digests of
every persisted block, exact metric values and missingness, control completeness, and causal-claim
eligibility. Missing, contaminated, or infrastructure-excluded blocks make the result ineligible;
legacy studies can still complete with explicitly ineligible results when their controls are absent.

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

`evaluation_suites` pins task and environment manifests. Controlled study bindings must use tasks
and environments admitted by that exact digest-valid manifest. `checkpoint_evaluations` can cite
only a sealed suite and completed registered study with the same suite digest, an exact
condition/checkpoint participation binding, metrics matching one eligible `study_results` row, and
hard gates backed by persisted verifier results scoped to that exact study manifest, suite,
condition, checkpoint, and result digest. `condition_id` is nullable in storage only so legacy
evaluation records remain readable; new evaluation admission requires it, and the uniqueness key
includes it so one checkpoint may be evaluated under multiple factorial conditions.
`checkpoint_promotion_policies` defines required metrics, regression tolerances, and minimum gains.
`checkpoint_comparisons` records metric deltas, explicit missingness, hard-gate failures, and the
lexicographic recommendation. `checkpoint_decisions` forms the immutable lifecycle audit trail.
Comparison recomputation verifies source evaluation, policy, and record digests; promotion decision
verification rechecks that the same candidate and recomputed recommendation support the action;
promotion re-runs that lineage verification before changing checkpoint state.

## Operations, artifacts, and provenance

`runs` and `run_transitions` are the recoverable state machine. `active_student_id` is unique while a
run is nonterminal. A sequence uniqueness constraint prevents duplicate transition positions.
`external_calls` provides the idempotency boundary. `workers` stores heartbeat and active run.
`review_queue` stores adjudication work.

`operation_spans` indexes the current state of a measured operation, while immutable
`operation_span_events` retains every queued/running/waiting/terminal transition.
`duration_profiles` records reproducible p50/p90/p95 duration and timeout estimates with exact
source span IDs and an evidence cutoff. The same typed duration profile can appear in a synthetic
`TemporalFrame`, but virtual scenario time never controls operational leases or real I/O.

`artifacts` holds immutable SHA-256 metadata plus backend-specific location evidence. Local storage
records a filesystem-relative blob identity; GCS records bucket, object, generation,
metageneration, ETag, CRC32C/MD5 when supplied, size, and project. `artifact_references` links blobs
to owners and is the source of truth for safe garbage collection. `provenance_heads` serializes
each stream while `provenance_events` stores the immutable cryptographic chain.

## Exploratory interactions

`interaction_sessions` and append-only `interaction_consent_events` own the deletable personal
conversation view. `interaction_messages` is immutable and self-parented, so a selected assistant
message identifies one exact ancestry. `interaction_turns` binds the user message, optional
assistant message, parent/source turns, mode, and trace without using an experiment episode.

`interaction_traces` is independently retained. It stores an immutable exploratory manifest,
serving/response metadata, usage, timing, event summary, restricted artifact links, and explicit
non-benchmark/memory/training classifications. Source session/turn IDs are attributable strings,
not cascading foreign keys, so a consented trace can remain after its personal conversation is
deleted. `interaction_feedback` stores positive/negative/note/correction records without mutating
messages. Like traces, feedback stores its source IDs without cascading foreign keys and carries a
retention classification, so consented feedback remains attributable after personal-row deletion.

`external_calls` has three mutually exclusive owner columns: a controlled developmental workflow
uses `run_id`, an exploratory invocation uses `interaction_trace_id`, and a governed process worker
uses `process_rollout_id`. A check constraint requires exactly one. All three reuse the same
request/response artifact ledger and operation-span telemetry; no synthetic run or episode is
created for the latter two.

The Alembic revision chain is authoritative for a new database. The Round 2 role migration adds
non-null role columns with a target-compatible default; the R2.3 revision adds verifier, reward,
study, scheduled-evaluation, suite, checkpoint, comparison, and decision tables. CI compares the
head revision with SQLAlchemy metadata and exercises downgrade/upgrade across every checked-in
revision. The temporal revision adds operation spans/events, duration profiles, and governed
authored demonstrations.
The research-control revision adds harness profiles, research executions with learned parent-state
and transport identity, nullable legacy-safe run/experiment bindings, study factor/control
bindings, condition-aware checkpoint evaluations, and immutable study results.
The Interaction Lab revision adds the six interaction tables and generalizes external-call
ownership without weakening legacy run ownership.
The PPRL/Amber revision adds project distributions, programs, instances, executions, rollouts,
immutable state/events, forks, outcomes, eligibility, worker invocations, authorization lifecycle,
per-action decisions, and a third external-call owner without weakening the other two.
