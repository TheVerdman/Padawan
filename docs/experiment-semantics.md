# Experiment semantics

The primary causal unit is a matched sibling block inherited from one immutable student state. A
cold checkpoint is useful for description, but it is not the control for a continuous learner.
The measured system includes its harness:

```text
observed performance = f(checkpoint, learned parent state, quantization, task, harness,
                         context policy, tools, budget, environment, seed)
```

## Assignment

`ExperimentEngine.create` transactionally creates the experiment, treatment/control fork, and all
block assignments. Each block contains two different items from one instance group. A seeded random
bit chooses the first orientation; later blocks alternate orientation, preventing a fixed item-side
bias while remaining exactly replayable. Block identifiers are semantic hashes of seed, index, and
group.

A controlled experiment also cites one registered `ResearchExecutionManifest`. Its exact
parent-state ID and hash, checkpoint, runtime, role, and seed must agree before the experiment is
created. The manifest binds the versioned `HarnessProfile`, serving/quantization identity,
task/corpus, effective workflow and sampling parameters, environment, and seed. The run,
experiment, episode, and workflow provenance repeat the same digest. Parent state is its own
comparison axis: two continuously learning students with different inherited state are not
"identical controls" merely because their checkpoint and runtime match.

The manifest is checked against executed inputs, not merely attached as a label. Initial run
creation rejects a different domain, pool, teacher mode, treatment/control condition, or retry
budget. Experiment creation rejects treatment/control conditions that differ from the same
manifest, including on replay.

Treatment and control child states contain the same inherited cognition and different branch IDs.
The intervention description is stored on the fork. Branch-scoped memory rejects cross-branch
writes until the comparison closes.

The operational algebra episode uses one cold sibling, one unseen treatment sibling, and one unseen
control sibling. Treatment receives the validated teacher lesson/repair; the default control
receives no intervention. `generic_check_work` is also implemented. The experiment engine can
represent the broader control labels in its design, while the current workflow only executes these
two control behaviors.

## Validity and outcomes

Each transfer trial records different item IDs, shared instance group, freshness from the cold item,
and distinct branches. A false check marks the block contaminated. Missing paired outcomes are not
imputed. Infrastructure-failed and contaminated blocks are excluded and counted separately.

For valid paired binary outcomes the report includes treatment/control success rates, paired gain,
discordant McNemar counts, the exact two-sided binomial p-value, and a seeded 10,000-resample
bootstrap interval for paired differences. Floor is flagged when both rates are at most 0.1; ceiling
when both are at least 0.9. A report does not permit a causal claim with no analyzed blocks or any
recorded contamination. It also does not permit a new causal claim when the research execution is
missing, carries unknown required component identity, or has no established context limit. Legacy
blocks remain readable and analyzable; report-time inference does not manufacture their controls.

Revision gain compares the revised cold-item score with the initial cold score. Immediate transfer
gain compares treatment and control on unseen siblings. These quantities answer different
questions and remain separate. A lesson becomes eligible for memory only when treatment succeeds
and control does not; a revision-only improvement is insufficient. The treatment branch becomes
canonical only on a strict transfer score advantage. A tie retains control. Losing and unselected
states remain immutable evidence.

## Interpretation limits

A single block is descriptive, not a powered result. `experiment run --blocks N` still executes
bounded developmental episodes, while a versioned `StudyManifest` is the authority that binds
multiple persisted experiments, conditions, checkpoints, an identical suite digest, environment
fingerprints, assignment seed, and optional propensities. `report study` aggregates the original
paired blocks and reports missing, contaminated, and infrastructure attrition without imputation.

The study manifest declares its intentionally varying research axes. Each binding repeats the
execution digest and may attach arbitrary factor values. Aggregation computes the axes that actually
differ; an undeclared difference blocks causal-claim permission. This separates standardized and
optimized harness results, harness uplift, quantization delta, teaching delta, and checkpoint /
training delta rather than collapsing them into one score.

For a controlled study, each execution's task-manifest digest and environment fingerprint must
belong to the registered evaluation suite. Checkpoint evaluation further requires a digest-valid,
sealed suite and a controlled study binding for the checkpoint and condition being evaluated. The
study must be complete. The transition is rejected until every bound block has a persisted outcome.
It then seals content-addressed condition/checkpoint results from that evidence; causal eligibility
requires all blocks to be analyzable with zero missing, contamination, or infrastructure exclusion.
Checkpoint metrics must cite and exactly match one eligible result, and hard-gate verifier evidence
must bind that result's study, suite, condition, and checkpoint. A shared suite label, favorable
subset, or free-form evidence reference is not evidence that those tasks, environments, weights,
conditions, gates, or metric values were evaluated.

Reasoning retention and compaction are separate profile fields. A later 2×2 study may vary both via
factor values and declared context/continuation axes. A gain from changing both is joint harness
uplift; it cannot be attributed to either factor without the factorial ablation.

Retention and interference trials are now durably scheduled against immutable state snapshots and
fresh shadow or sealed item groups. Due-item leasing and expiry recovery are implemented. A result
may be claimed only after the corresponding trial has a persisted exposure, verifier evidence,
contamination checks, and explicit outcome; a scheduled or missing trial is not a failed student
outcome. Cost remains recorded only when an adapter supplies it, and missing cost is not zero.
