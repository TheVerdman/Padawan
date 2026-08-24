# ADR 0014: Amber is an actor-neutral execution and release protocol

- Status: Accepted
- Date: 2026-08-23

## Context

Long-lived model-directed projects can combine large context, branching, dynamic workers, tools,
persistent memory, and substantial compute. Informal prompt restrictions cannot establish what
actually executed, prevent authority drift, or govern artifacts and checkpoints after a run.
Controls must apply equally to hosted and self-hosted models and must remain independent of the
process policy being evaluated.

At the same time, a control protocol that deliberately removes the context, horizon, tools, or
reasoning competence under study would invalidate the experiment rather than make it safer.

## Decision

Amber is an actor-neutral authorization protocol around project admission, execution, and release.
An immutable authorization envelope binds the exact PPRL program and distribution, allowed splits
and target classes, worker-model identities, digest-pinned role-specific tool grants, sandbox and
environment fingerprints, network and egress policy, compute and artifact budgets, persistence
permissions, checkpoint policy, reviewers, expiry, and stop conditions.

Authorization has append-only states: `prepared`, `authorized`, `active`, `paused`, `quarantined`,
`release_approved`, `expired`, and `revoked`. Every process action is admitted independently against
the active envelope and projected cumulative resource use. Missing, expired, drifted, or ambiguous
authority fails closed. A watchdog and reviewers are outside the process policy's control.

Inside an active envelope, workers receive the actual capabilities declared by the experiment.
Amber constrains the environment boundary, targets, budgets, persistence, egress, and release path;
it does not silently weaken scientific reasoning or substitute a toy task for the authorized one.

Outcome verification, training eligibility, checkpoint retention, checkpoint export, and public
release are separate decisions. A successful rollout does not itself authorize any later step.

## Consequences

Every admitted action produces a durable decision tied to the exact request, authorization digest
and lifecycle sequence, rollout sequence, state digest, and hashed lease token. A lifecycle
transition, state advance, or lease replacement invalidates an unused earlier admission.
Environment identity drift, unsupported tool or target use, exhausted budgets, triggered stop
conditions, sandbox failure, or unauthorized egress prevents the action before execution.

Training compilation independently replays the immutable envelope, lifecycle, requests, decisions,
and state transitions. A stored eligibility flag cannot override a later quarantine, revocation, or
explicit expiry. Historical bundles remain reproducible at their original evidence watermark.

Restricted artifacts remain inspectable after pause or quarantine. Release requires the envelope's
declared independent review and cannot be granted by the project process.

Amber can govern science, mathematics, software, or other target classes without provider-based or
actor-based exceptions. Expanding a target class requires a new immutable envelope and review; it
does not require redesigning the PPRL substrate.
