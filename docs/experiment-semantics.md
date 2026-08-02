# Experiment semantics

The primary causal unit is a matched sibling block inherited from one immutable student state. A
cold checkpoint is useful for description, but it is not the control for a continuous learner.

## Assignment

`ExperimentEngine.create` transactionally creates the experiment, treatment/control fork, and all
block assignments. Each block contains two different items from one instance group. A seeded random
bit chooses the first orientation; later blocks alternate orientation, preventing a fixed item-side
bias while remaining exactly replayable. Block identifiers are semantic hashes of seed, index, and
group.

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
recorded contamination.

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

Retention and interference trials are now durably scheduled against immutable state snapshots and
fresh shadow or sealed item groups. Due-item leasing and expiry recovery are implemented. A result
may be claimed only after the corresponding trial has a persisted exposure, verifier evidence,
contamination checks, and explicit outcome; a scheduled or missing trial is not a failed student
outcome. Cost remains recorded only when an adapter supplies it, and missing cost is not zero.
