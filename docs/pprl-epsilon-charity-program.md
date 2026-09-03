# Persistent-process reinforcement learning and epsilon-charity

Status: canonical research-program record. The PPRL and Amber control-plane substrate is
implemented; a live multi-agent swarm, recovery runtime, and parameter-update backend are not.

## Purpose and scope

This program asks whether the useful unit of long-horizon reinforcement learning can be a durable
institution rather than one model invocation or one context window.

The institution owns canonical state, accumulated evidence, artifacts, roles, resources, and
unfinished work. Individual model and tool workers are replaceable executors. A complete macro-
rollout can therefore span many contexts, worker replacements, branches, failures, and recovery
events while remaining one coherent learning trajectory.

The second motivating line is very-long-rollout RL and RLVR: sustained search over many contexts,
with durable checkpoints, branching, replay, and repeated stochastic macro-rollouts. The general
architecture applies to scientific, mathematical, software, and other authorized environments.
This document does not authorize a cybersecurity target, internet access, a live swarm, or a
weaker sandbox. Target capability and execution authority remain separate decisions under Amber.

That staging rule is not a categorical prohibition on self-hosted or open-weight research. Amber is
actor- and provider-neutral: hosted and self-hosted models should face the same capability-based
authorization standard, with equivalent isolation, observability, disclosure, and incident-response
claims established by evidence rather than affiliation.

The formulation does not presume novelty in relaxed lexicographic optimization, cooperative
multi-agent RL, team rewards, policy churn, multi-agent credit assignment, durable blackboards, or
organizational learning. The research contribution, if any, is the composition of these ingredients
and the evidence that the composition creates a distinct, useful, and governable learning regime.

The concise thesis is:

> Do not merely train agents to succeed. Train persistent organizations of agents to succeed.

## Terminology

### Persistent-process RL

Persistent-process reinforcement learning, or PPRL, treats the durable process as the macro-agent.
Its trajectory contains worker actions, state transitions, tool use, artifacts, communications,
delegations, failures, recovery, and stopping decisions.

PPRL does not imply that the outcome is mechanically verifiable. Padawan recognizes verifiable,
empirical, adjudicated, and hybrid reward authorities. Use **PPRL-VR** only when the declared
authority is genuinely verifiable. Otherwise use **PPRL**.

Reinforcement learning claims require a distribution, not one memorable run. Comparative evidence
must include multiple sampled project instances and multiple stochastic rollouts per instance,
with the hierarchy of events within trajectories, trajectories within instances, and instances
within distributions respected in the analysis.

### Epsilon-charity

For worker or policy `i`, let `J_i` be its primary objective and let `Q_i(s, a)` be the estimated
primary value of action `a` in state `s`. Define the epsilon-optimal action set:

```text
A_i^epsilon(s) = {a : Q_i(s, a) >= max_a' Q_i(s, a') - epsilon}
```

Among actions inside that feasible set, select for a secondary collective objective:

```text
a_i* = argmax_{a in A_i^epsilon(s)} Q_collective(s, a)
```

Interpretation:

> Optimize strongly for the assigned objective. Where behaviors are equivalent or nearly
> equivalent within an explicit regret bound, direct the available behavioral slack toward the
> durable institution or other workers.

At `epsilon = 0`, this is collective tie-breaking among exactly optimal actions. For positive
epsilon it is a bounded primary-objective regret budget. Padawan calls the durable form a
**local-regret contract**.

This is a lexicographic constraint, not an informal weighted reward bonus. A weighted scalar can
silently trade away arbitrary primary utility when scales drift. The epsilon feasible set makes the
maximum permitted local sacrifice explicit, while its counterfactual estimator and uncertainty
rule determine whether an action is actually inside that set.

### Policy churn and behavioral slack

Learned policies can differ substantially in behavior while producing little meaningful change in
primary-task return. Let the approximate near-optimal policy set be:

```text
Pi_epsilon = {pi : J(pi) >= J* - epsilon}
```

Policy churn is not itself desirable. Its relevance is that near-indifference may expose a region
of behavioral freedom. Epsilon-charity asks whether selection inside that region can be made
deliberate:

```text
pi_EC = argmax_{pi in Pi_epsilon} J_collective(pi)
```

Policy churn exposes possible slack; epsilon-charity gives that slack a direction. Whether real
models expose a stable, estimable near-indifference region is an empirical question, not an
assumption.

## Institution-level environment

One abstract institutional state is:

```text
I_t = (M_t, P_t, G_t, K_t, R_t, A_t, H_t)
```

where:

- `M_t` is governed persistent process memory;
- `P_t` is the active worker population, identities, health, and roles;
- `G_t` is the task and dependency graph;
- `K_t` is accumulated claims, evidence, artifacts, and negative results;
- `R_t` is remaining compute, time, token, tool, and artifact capacity;
- `A_t` is the active authorization and policy state; and
- `H_t` is recovery state, including leases, assignments, retries, and checkpoints.

The transition includes a set of admitted worker and runtime actions:

```text
I_t --E_t--> I_(t+1)
```

`E_t` may contain model calls, tool calls, messages, artifact admissions, assignments, delegation,
worker creation or termination, state checkpoints, verifier observations, and stop decisions. Raw
forensic telemetry is not part of `I_t`: it is joined to the transition for researchers but is not
agent-readable process memory.

The institutional trajectory is:

```text
tau_I = (I_0, E_0, I_1, E_1, ..., I_T)
```

and outcome authority evaluates the trajectory or a declared projection of it:

```text
R_I = F(tau_I)
```

An individual worker may fail to produce a terminal answer yet contribute positively by preserving
a negative result, falsifying a hypothesis, improving a tool, detecting a safety issue, or handing
off a tractable subproblem. From the worker boundary such an action can look mildly sacrificial;
from the institutional boundary it may be ordinary instrumental optimization.

This motivates the central hypothesis:

> Epsilon-charitable behavior at the worker level may be a local behavioral signature of
> institution-level optimization.

## Persistent state and emergent coordination

Workers may leave governed artifacts such as discoveries, failed approaches, hypotheses, reusable
code, tool outputs, task claims, requests, critiques, summaries, conventions, and role information.
A locally inexpensive action can prevent many future workers from repeating the same work.

Repeated positive externalities could produce:

```text
local helpfulness
  -> persistent knowledge
  -> less duplicated work
  -> specialization
  -> division of labor
  -> coordination conventions
  -> collective capability
```

Candidate learned behaviors include documentation norms, negative-result reporting, endogenous
specialization, voluntary reassignment, peer verification, reusable infrastructure, succession and
handoff, information routing, avoidance of redundant work, memory maintenance, productive
disagreement, exploration/exploitation allocation across workers, and new organizational
conventions. Their presence must be measured rather than inferred from an appealing transcript.

The research question is not whether multiple agents can be explicitly orchestrated. That is an
important baseline. It is whether durable state plus locally bounded collective selection produces
useful organization beyond what the initial orchestrator scripts.

The central question is:

> Can a reinforcement-learning environment make a persistent multi-agent institution the unit of
> rollout and reward, and can bounded locally prosocial policies plus institution-level credit
> assignment produce useful emergent organization beyond explicitly scripted orchestration?

Communication is not synonymous with process memory. Messages, delegations, and live coordination
belong to a governed communication fabric even when retained durably. Only information explicitly
admitted into project state or cross-project process memory may hydrate replacement workers.

## Credit assignment

Giving the same terminal institutional reward to every action will generally have poor variance and
weak causal meaning. Credit may be needed at several levels:

```text
token -> action -> worker trajectory -> subteam -> institutional trajectory
```

One conceptual counterfactual contribution for worker `i` at time `t` is:

```text
A_i,t^I = Q_I(I_t, a_t) - E_a~i[Q_I(I_t, (a_t^-i, a~_i))]
```

The useful counterfactual is difficult because replacing one action can change later assignments,
communications, information availability, and worker populations. Candidate estimators include
paired checkpoint forks, randomized message or artifact admission, difference rewards, learned
institutional critics, Shapley-style approximations, and delayed ablations. None should be treated
as identified causal credit without its assumptions and interference structure being explicit.

Credit records must retain the primary outcome evidence, estimator identity, uncertainty, and the
counterfactual or matched comparison used. A later scalarization may not erase raw outcome evidence
or convert unknown and infrastructure-failed outcomes into success.

## Research hypotheses

The primary falsifiable hypotheses are:

1. Epsilon-charitable selection improves institutional outcomes relative to exact-greedy and random
   near-optimal selection without exceeding the declared local-regret bound.
2. Persistent shared process state produces qualitatively different organization, not merely
   retrieval gains.
3. Useful reporting of negative results, peer verification, specialization, and succession emerge
   more often under institutional reward than under isolated worker reward.
4. Organizational conventions and task competence survive replacement of every original worker.
5. Institution-level reward may learn epsilon-charitable behavior without an explicit auxiliary
   objective; an explicit contract may nevertheless improve sample efficiency or safety.
6. Benefits survive unseen project instances, fresh worker identities, and sealed evaluation rather
   than depending on memorized tasks or stable personalities.
7. The same substrate can also amplify reward hacking, unsafe goal adoption, and covert
   coordination; governed communication and containment should measurably reduce these failures.

Important null results include no improvement over a strong scripted coordinator, improvements
fully explained by additional compute or context, apparent specialization caused only by assigned
roles, and coordination metrics that do not predict held-out institutional outcomes.

## Baselines and ablations

At minimum, compare:

- one worker with the same aggregate compute and context budget;
- independent workers with no shared state;
- independent workers with read-only shared retrieval;
- a strong scripted central coordinator with explicit roles and handoffs;
- persistent state with exact-greedy action selection;
- persistent state with random tie-breaking inside the epsilon set;
- persistent state with a weighted scalar prosocial bonus;
- explicit epsilon-charity with several preregistered epsilon values;
- institution-level reward without explicit epsilon-charity; and
- institution-level reward plus the explicit local-regret constraint.

Ablate messages, artifact admission, negative-result storage, role persistence, worker identity,
replacement rate, horizon, task difficulty, memory compaction, communication topology, and reward
visibility separately. Equalize total compute and tool budgets wherever the scientific comparison
requires it.

## Measurement

Primary measurements should include:

- institutional task outcome under its declared authority;
- cumulative primary regret and the rate of epsilon-bound violations;
- useful externality per admitted action or artifact;
- duplicated-work rate and time saved by negative-result reuse;
- dependency-graph throughput and critical-path latency;
- spontaneous versus assigned specialization;
- handoff completeness and recovery latency;
- performance before and after 100 percent worker replacement;
- communication efficiency, concentration, reciprocity, and peer influence;
- convention persistence under fresh workers and unseen tasks;
- calibration of empirical or adjudicated outcomes;
- reward-hacking, evaluator-probing, unauthorized-channel, and policy-violation rates; and
- uncertainty intervals clustered at the project-instance and rollout levels.

Emergent organization should require more than message volume. A candidate must exhibit persistent
role or convention structure, causal benefit on held-out outcomes, adaptation not fully specified by
the orchestrator, and survival under worker replacement. Pre-registering the operational definition
is preferable to recognizing an interesting narrative after the fact.

## Failure modes and safety requirements

Institution-level optimization can amplify capability and failure together. Principal risks are:

- collective reward hacking or manipulation of graders and evidence;
- workers adopting peer goals outside their assignment;
- covert communication through caches, filenames, object metadata, logs, or public services;
- uncontrolled accumulation of credentials, access, or reusable offensive capability;
- groupthink, suppression of dissent, deceptive consensus, or strategic evidence omission;
- contamination across train, adaptive, validation, and sealed partitions;
- persistence on impossible tasks without a safe exit;
- compute pooling that exceeds the intended experimental boundary;
- poisoned process memory that survives worker replacement;
- forensic or mechanistic records becoming an accidental worker communication channel; and
- researcher surveillance data leaking into training or worker hydration.

Consequently, a live experiment requires an independently enforced sandbox and network boundary,
attested worker identity, capability-scoped credentials, explicit communication channels, an
off-worker forensic source of truth, sealed reward authority, safe abstention and escalation,
fleet-wide pause and revocation, and adversarial testing for ambient shared channels. Amber records
and admits authority but does not itself instantiate the OS, container, VM, network, or accelerator
sandbox.

## Relationship to Padawan

Padawan already implements:

- versioned project distributions and replication requirements;
- episodic and continual persistence declarations;
- process programs, worker-role and tool contracts;
- the epsilon/local-regret contract;
- immutable project-state versions and append-only events;
- rollout leases, forks, outcomes, and replay;
- verifiable, empirical, adjudicated, and hybrid authority;
- separate PPRL, fork-preference, and PPRL-VR training products;
- admission- and lease-bound model calls; and
- Amber authorization, action admission, quarantine, revocation, and release controls.

Padawan does not yet implement:

- a governed live worker-to-worker communication fabric;
- executable worker assignments or delegation;
- a PPRL worker scheduler, launcher, heartbeat, or replacement runtime;
- a hydration compiler with a hard forensic-data exclusion boundary;
- complete PPRL tool, observation, environment-action, and read auditing;
- an integrated Inkling mechanistic telemetry path;
- a generic sandboxed project environment;
- a process-policy trainer or parameter-update backend; or
- evidence that a live institution or learned process policy has improved.

The exact current-state map and dependency order are maintained in
[PPRL four-fabric architecture](pprl-four-fabric-architecture.md). The implemented control-plane
contract is documented in [Persistent-process reinforcement learning](persistent-process-rl.md),
with decisions in [ADR 0013](adr/0013-persistent-process-reinforcement-learning.md) and
[ADR 0014](adr/0014-amber-protocol.md).

## Program sequencing

The scientific program should progress from bounded single-worker and replacement fixtures to
explicitly governed communication, then to small repeated multi-worker experiments. Scale, horizon,
target breadth, mechanistic capture, and learning should increase only after the preceding
containment, reconstruction, and causal-evidence gates pass.

Initial scientific and mathematical fixtures are useful for validating the architecture but are
not the definition of the full program. Conversely, the full architectural ambition does not
authorize skipping the staged safety and evidence gates.
