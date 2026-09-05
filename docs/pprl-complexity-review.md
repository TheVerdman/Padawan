# Padawan complexity and roadmap review

Review checkpoint: 2026-09-05, after reviewed recovered-source admission. Comparison base:
`9d58ceb4ad5cb0ceac77c2ddf30e0ef83c5e69d0`, the verified clean goal-start HEAD.
The user explicitly chose **finish this checkpoint, then hold for review**. No further implementation
is authorized by the proposals below until the user resumes. This is an implementation hold;
the app's goal Pause control remains user-operated. The research goal is unfinished, not completed
or blocked to simulate a pause.

## Assessment

The growth deserves scrutiny. Generated schemas and adversarial tests explain much of it, but the
runtime grew substantially too. The current result is a stronger, tested control plane and a narrow
local CPU execution boundary. It is still not an operating research institution, a scientific
demonstration of complete replacement, or a learning system that has changed model parameters.

Engineering judgment: too much progress is currently expressed as additional contracts, guards and
checkpoint documentation. Those checks address real defects, but continuing that pattern without
a finite foundation exit gate risks building infrastructure faster than we can validate its value.
The next plan should be organized around observable institutional behavior and bounded complexity.

## Measured goal diff

These are gross added/deleted lines against the goal-start tree, including this checkpoint; they
are not net repository size, unique concepts, test coverage or a complexity score. Generated schema
definitions repeat nested contracts. The schema generator remains separately counted as tooling.

<!-- goal-diff-start -->
| Category | Added | Deleted | Files changed |
| --- | ---: | ---: | ---: |
| Runtime Python | 12,640 | 152 | 48 |
| Tests and fixtures | 12,322 | 95 | 48 |
| Generated JSON schemas | 14,916 | 4 | 66 |
| Documentation | 3,578 | 61 | 24 |
| Database migrations | 666 | 0 | 9 |
| Build and tooling | 151 | 1 | 3 |
| Total | 44,273 | 313 | 198 |
<!-- goal-diff-end -->

At the preceding commit `9cb0501`, the exact total was +41,874/-313 across 189 files: 12,262 runtime
additions, 11,571 test/fixture additions and 13,959 generated schema additions. The user's larger
live diff included the in-progress checkpoint. The measurement script/output and final source
manifest are retained in ignored `runs/pprl-recovered-evidence-validation-20260905/`.

## What the goal has delivered

- Institutional/forensic separation: authoritative artifact metadata, reviewed derivatives,
  reference/content admission, independently owned dependencies, exact public observations and
  separate offline learning projections. The initial raw-record and projection findings received
  executable checks; reviewer honesty, semantic provenance, covert channels and complete forensic
  capture remain trust/validation limits.
- Execution integrity: exact model request/transport binding, conserved authorization-wide funding,
  reviewed local CPU container execution, expiring worker capabilities and one-action lease
  ownership. These are different boundaries; Amber is still not an attested execution sandbox.
- Recovery primitives: reviewed fencing, uncertainty holds, native source/settlement reconstruction
  and now explicit process-only admission of reviewed recovered findings. Completed uncommitted
  effects remain stopped. Admission does not reconstruct the lost worker's intent or clear a task.

The prior checkpoint history is in `pprl-goal-checkpoint-review.md`; later implementation and exact
validation are in `pprl-execution-ledger.md`. This checkpoint adds no model calls, scheduler,
successor transition, trainer, parameter update, cloud campaign or deployment.

## Concrete complexity pressure

Code facts:

- `padawan/pprl/evidence.py:395` combines shared review checks with ordinary, Atlas and recovery
  provenance dispatch. This checkpoint reuses its SQL store and ownership path; it adds a private
  receipt version rather than a parallel admission database.
- `padawan/pprl/content.py:486` recognizes protected identifiers across multiple subsystem tables.
  `padawan/pprl/recovered_evidence.py:101` also checks exact source/origin identifiers and calls that
  content boundary. New private record families therefore require a coordinated boundary review.
- `padawan/pprl/resources.py:277` walks prior rollout admissions/reservations and checks their
  closing evidence. Integrity is stronger, but current code has no measured long-history cost
  bound. Repeating a history walk on each action is a scaling concern, not a measured slowdown.
- New tests import helpers from other integration-test modules. This checkpoint initially collided
  with an existing Amber fixture identity; the corrected container fixture uses a fresh database.
  Fixture coupling and the growing chronological documentation burden are concrete maintenance costs.

Recommendations for the review, not changes made now: identify duplicate authority/ownership
validation that can share a small audited helper; keep checks independent where the trust roots
differ; move reusable fixture setup out of test-case modules; replace repeated status prose with
one current gate table and linked historical evidence. Do not start a generic plugin framework or
a repository-wide rewrite to solve this. Benchmark history-sensitive paths before choosing an
incremental verification design; do not remove integrity checks to improve a benchmark.

## Proposed foundation exit gate

The next implementation agreement should have a finite boundary:

1. One logical task/effect lifecycle: completed-result review or terminal abandonment, immutable
   intervention lineage, explicit unknown-effect stops and replay/compiler exclusions. New leases,
   replicas or task IDs cannot erase an unresolved logical effect or reset its budget.
2. One deterministic institutional workflow using the existing stores and addressed worker API:
   assign, observe, act, commit, stop, recover and resume with bounded queues and retained costs.
   Replace 100% of disposable scripted workers, restart the broker, discard local context/caches,
   and verify surviving admitted knowledge and unfinished task ownership. Repeat churn and crash
   probes at named boundaries. This establishes engineering continuity, not a scientific effect.
3. Explicit pilot trust assumptions and measured bounds: broker/database/reviewer/model-server
   trust, credential delivery, admitted tools, wall-time/usage/retention limits and no implicit
   forensic access. Measure action/restart cost as history grows. Prepare a concrete small local
   Nemotron campaign with an independent verifier, preregistered samples and matched baselines.

Each item needs an agreed review checkpoint; discovery of another substantial prerequisite returns
to the review before expanding scope. Prefer one existing verifier-backed domain over inventing a
new benchmark family. The first pilot should test persistence and replacement with simple routing;
three-tier hierarchy and frontier escalation value need later controlled comparisons.

This is proposed sequencing, not reduced program scope or permission to claim the goal complete.
Capability Atlas still needs distinct worker/institution subjects and protected behavioral/MI
interchange. Live coordination/economics, independent distributions and repeated stochastic
rollouts, epsilon-charity/regret analysis, resumable **10M+ tokens**, the billion-token ambition,
actual trainer/candidate parameters, independent evaluation, promotion and rollback all remain.
No amount of generated text or passing fixture tests substitutes for those results.

## Decisions for discussion

1. Accept the finite foundation gate above, or revise its boundary before resuming; avoid another
   open-ended sequence of infrastructure slices.
2. Select the first verifier-backed domain and minimum pilot comparisons, with local Nemotron as
   the initial worker candidate. Review its trust perimeter and intervention/credit exclusions.
3. Agree how much consolidation is justified before that pilot and where to stop for another review.

No new cloud/GPU authority is needed for this review. Any GCP campaign still requires a concrete
manifest, budget ceiling, stop/cleanup evidence and the separately promised authorization question.
No training or promotion has been authorized by finishing this checkpoint.
