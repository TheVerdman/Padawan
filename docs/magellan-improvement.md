# Magellan Improvement integration

Magellan Improvement is an installed Padawan corpus/verifier domain for agentic logistics work. It
is not yet a live autonomous workflow. Padawan does not import Magellan modules, discover a sibling
checkout, or trust an uncommitted directory merely because its tests pass. A run becomes eligible
only through an explicit, versioned environment handshake and captured before/after evidence.

## Read-only audit on 2026-08-01

The designated Magellan worktree was inspected at commit
`782e5b41ecda9be7d9edfdbd5c5e7791a41c9ca1` on `my-new-branch`. It was dirty, with substantial
tracked and untracked Phase 2–5 work. Padawan did not alter or execute it during the audit.

The useful integration seams are real: the orchestrator exposes plans and revisions; the tool
registry declares read, propose, recoverable-commit, and regulated-commit tiers; validators,
approval records, waits, provenance, carrier/rate services, and Phase 5 scenarios already cover
intake, negotiated rates, outreach/waiting, and regulated actions.

The audited tree cannot yet produce authoritative Padawan evaluation evidence:

- both OpenAI planning paths still post to `/v1/chat/completions` rather than `/v1/responses`;
- no transactional reset/fork boundary creates independent matched worlds;
- tool handlers and repositories commit internally, preventing an enclosing evaluation transaction
  from reliably restoring the world;
- the tool-harness idempotency cache is process-local rather than durable;
- capability checks use tool name and tier but do not enforce their declared tenant, workflow, and
  user-role identity;
- approval bypass can be constructed by an ordinary caller;
- reviewer consultation currently returns no findings.

These are environment blockers, not low task scores. A plausible final answer or correct-looking
database state cannot compensate for them.

## Padawan contracts

`agent.magellan_improvement@1.0.0` supplies eight deterministic scenario families:

- intake and plan construction;
- negotiated-rate selection and quoting;
- carrier outreach with durable wait/resume state;
- regulated booking after recorded human approval;
- cross-tenant refusal;
- unavailable-tool refusal;
- invalid/forward/cyclic dependency rejection before execution;
- durable idempotent replay without a duplicate effect.

Each corpus item embeds a typed `MagellanScenarioManifest` with the tenant, user, authorization
snapshot, concrete task inputs, allowed and forbidden tools, approval requirements, deterministic
postconditions, forbidden invariants, resource limits, initial-state digest, and environment
fingerprint. Generated siblings share a governed instance group but have distinct seeds and worlds.
The negotiated-rate family binds the final quote to the exact seeded rate; regulated, tenant,
unknown-tool, and replay families bind exercised calls to the assigned shipment/request facts.

The external environment must emit:

- a repository snapshot containing the commit plus digests for the complete tracked diff,
  non-sensitive untracked sources, dependencies, and migrations;
- a handshake binding the repository and driver digests, PostgreSQL schema, authorization policy,
  content-addressed source materialization, allowlisted runtime secrets, world/reset protocol,
  network and side-effect policy, Responses protocol, durable idempotency, and typed tool surface;
- before/after observable world projections tied to one allocation, isolated world, database
  snapshot, tenant, user, and authorization digest;
- typed plans, tool calls, observations, approval decisions, failures, provenance references, and
  replay keys;
- model-side latency, cost, and optional paired token counts, separate from tool-side measurements;
- explicit pass/fail evidence for every declared validator on successful commit-tier calls.

Local repository and handshake paths are runtime-only settings. They are removed from command
manifests and replaced with configured/not-configured booleans. Sensitive untracked files are
never read into the snapshot manifest; only their relative names are recorded. A ready handshake
must record the exact environment-variable-name allowlist through which runtime secrets arrive,
rather than mounting those files. Volatile files are named but not hashed as source content.

`MagellanMatchedWorldManifest` structurally requires at least two conditions to begin from one
state/environment while using distinct world IDs and isolation-token digests. This prevents a
target, control, or baseline declaration from silently aliasing the same mutable world. The
handshake must additionally attest that reset and matched-world independence were exercised; the
real driver acceptance test remains pending upstream support.

## Deterministic verification and reward

`MagellanScenarioVerifier` reads captured evidence only. It does not call Magellan and does not use
the agent's final prose as authority. It emits independent results for:

1. environment integrity;
2. authorization and approval integrity;
3. trace, state-chain, provenance, plan, and replay integrity;
4. forbidden safety postconditions;
5. task completion.

The first four are lexicographic hard gates. Task completion, constraint satisfaction, recovery,
tool efficiency, and normalized cost remain a recomputable reward vector. A hard-gate failure
makes scalar utility unavailable in the durable reward engine even if task postconditions happen
to look successful. Infrastructure failures preserve missing task/recovery/efficiency observations
rather than converting them to zero. Measured model and tool cost remains observable even when the
task outcome is unavailable.

Successful, hard-gated target trajectories from non-sealed pools may be SFT, RLVR, process, and
matched-preference candidates. Valid unsuccessful target traces may be process/preference
candidates. Baseline, sealed, or hard-gate-failed traces remain evaluation-only. Continued
pretraining always requires a separate rights-cleared source corpus; episode traces are not silently
reclassified as mid-training data.

## Runtime gate

Configure paths only in the process environment:

```text
export PADAWAN_MAGELLAN_REPOSITORY_ROOT=/operator/selected/magellan
export PADAWAN_MAGELLAN_HANDSHAKE_PATH=/operator/generated/magellan-handshake.json
padawan --json verify magellan-environment
```

The inspection command is read-only. It emits repository-relative paths and digests, never the
configured root. If the handshake is absent or any hard requirement differs, `ready` is false and
the blockers are explicit. The runtime must execute a content-addressed copy or container image,
not the mutable selected worktree. Only a ready assessment may bind new Magellan corpus inventory:

```text
padawan corpus generate magellan --groups-per-family 1 --siblings-per-group 2
```

No live Inkling result exists yet. The target pilot remains separately blocked on the Inkling
Responses endpoint and on a Magellan driver that can produce the required isolated evidence.
