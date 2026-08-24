# ADR 0013: Persistent-process reinforcement learning is a project-scale layer

- Status: Accepted
- Date: 2026-08-23

## Context

Padawan's developmental runs are deliberately bounded teaching workflows. Their durable states,
episodes, and matched treatment/control forks describe a student learning from one competency
intervention. Very-long-horizon research instead needs a project to survive many model contexts,
worker replacements, tool calls, intermediate artifacts, failed hypotheses, and checkpointed
continuations. It also needs repeated stochastic macro-rollouts drawn from declared task
distributions; one historical run is not reinforcement-learning evidence.

Some project objectives have deterministic verification, while others are empirical, adjudicated,
or hybrid. Calling every such system RLVR would erase a material distinction in its evidence.

## Decision

Padawan adds persistent-process reinforcement learning (PPRL) as a separate orchestration layer.
The durable project process is the macro-agent. Model and tool workers are transient executors that
never own canonical project state.

PPRL uses an append-only event ledger and immutable state versions containing objectives, plans,
hypotheses, claims, evidence, artifacts, dependency edges, budget use, worker assignments, risks,
and memory references. A macro-rollout is one stochastic attempt on a sampled project instance.
Forks create multiple continuations from an identical intermediate state for paired intervention
evidence.

Every program binds a versioned project distribution, replication requirements, worker-role and
tool capability contracts, a persistence mode, and one of four reward-authority kinds:
`verifiable`, `empirical`, `adjudicated`, or `hybrid`. Only the first kind may be described as
PPRL-VR. Raw outcome evidence remains primary; scalar returns and preferences are versioned,
recomputable interpretations.

The optional bounded-prosocial mechanism is a local-regret contract. It defines an epsilon,
counterfactual local-utility estimator, uncertainty rule, and lexicographic feasible-set selector.
It is not required for all PPRL programs and is not reduced to an informal scalar bonus.

The initial executable environments may be modest, but the contracts support dynamic worker
rosters, long horizons, episodic and continual persistence, all four reward regimes, state forks,
and external process-policy training from their first version. Activation is staged; architectural
capability is not removed to make the first experiment smaller.

## Consequences

The existing developmental `RunState` and `EpisodeRow` remain unchanged and retain their present
meaning. PPRL has independent rollout, event, state, fork, outcome, and distribution records while
reusing Padawan's artifact, provenance, research-control, study, reward, training, and checkpoint
authorities.

Statistics must respect the hierarchy of events within trajectories, trajectories within project
instances, and instances within distributions. A single historical trajectory may be retained as
evidence or an authored demonstration but cannot alone enter an RL-claim path.

Project-local state and cross-project process memory remain distinct from student lesson memory.
Continual memory requires explicit authorization and clean evaluation boundaries.
