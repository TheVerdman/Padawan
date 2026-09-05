# Finite task ownership for the first foundation workflow

Boundary selected on 2026-09-05 after resumption. The accepted foundation gate permits completed-result
review **or terminal abandonment**. Use the implemented terminal branch for interrupted effects that
cannot safely resume. A general reviewed continuing successor remains in the full roadmap; it need
not become an additional prerequisite for a pilot that explicitly stops and excludes those samples.

## Contract and trust assumptions

Before creating any rollout under a funded Amber authorization, a trusted reviewer enrolls one
immutable finite task plan. Each entry binds an exact instance, declared comparison condition,
replication index, execution, rollout ID and initial-payload digest. The logical task coordinate is
the instance/condition/replication tuple within that authorization. Different labels, executions or
rollout IDs cannot supply a second owner for that coordinate. The plan binds the original resource
grant and authority history. No new budget is created. Maximum plan size is 128 tasks.

The broker, SQL/artifact stores, named reviewers and declared domain/condition identities are trust
assumptions. This does not recognize semantic equivalence between differently declared tasks or
attest a worker sandbox. Workers have only the existing addressed API; they cannot enroll plans,
create roots, change comparison conditions or mint authorizations. A separately authorized research
campaign may declare new independent samples; it cannot erase the old campaign's records or charges.

This is an explicit opt-in boundary for the finite workflow. Legacy authorizations retain their
existing contracts and do not acquire task-plan guarantees. Planned rollouts carry a private marker;
missing, changed or mismatched plans fail closed on create, claim, admission, reservation reads and
replay. No automatic backfill converts historical work into preregistered ownership. Task plans are
private control records, not worker observations or scientific preregistrations. The finite workflow
must also enroll the separate worker-identity scope: a task plan alone does not turn legacy callers
into authenticated workers.

## Invariants and failure behavior

The planned rollout remains the task owner through replacement and ordinary state transitions.
Committed state is the institutional frontier; worker context, caches and interrupted stacks are
not recovered. Unstarted work may resume only after the existing reviewed release/fencing checks.
Unknown and completed-but-uncommitted effects remain stopped; terminal abandonment preserves the
original costs/holds and excludes the sample. Recreating a task, changing a rollout ID, inventing a
replica, or forking cannot bypass this stop. Planned child enrollment and continuing successors are
unsupported rather than implicitly authorized. No external effect is repeated by enrollment/read.

Enrollment serializes on the existing authorization lock with root creation. It fails if any rollout
already exists. One transaction publishes the plan; one transaction creates a bound root and its
existing state/ownership/funding records. Failures roll back only the new transaction. Populated
history cannot be downgraded away. Private replay/compiler evidence retains the task plan at its
watermark; it neither grants training rights nor reclassifies an infrastructure interruption.

## Acceptance and retained evidence

Test duplicate logical coordinates, changed labels and execution/instance/initial-state identities,
wrong reviewer/grant/authority/time, late enrollment, unauthorized extra roots/forks, missing markers,
corrupt plans, rollback and exact retry. Check replacement keeps the same task and budget and that
terminal abandonment cannot be bypassed by another ID. Check private identifiers/digests are denied
by process content and compiler lineage stays private. Exercise enrollment/root-creation races on
PostgreSQL and reconstruct ownership from a fresh broker with no old Python objects.

Retain exact source/schema hashes, plan/review identities, root and resource journals, before/after
account/state snapshots, negative probes, test logs and fixture cleanup. These are offline engineering
results; the subsequent scripted continuity milestone and frozen repeated behavioral baseline remain
separate. No scheduler, heartbeats, live messaging, hierarchy, cloud/model execution, training,
semantic effect deduplication, authenticated reviewer identity or general continuing successor is
implemented by this ownership slice.
