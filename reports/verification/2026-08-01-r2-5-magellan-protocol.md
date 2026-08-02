# R2.5 Magellan protocol verification

Date: 2026-08-01

## Outcome

The Padawan-side `agent.magellan_improvement@1.0.0` corpus, environment, trace, verifier, reward,
and training-eligibility protocol is implemented. It is installed as a verifier-only domain and
cannot be selected as an autonomous workflow.

No live Magellan or Inkling outcome is claimed. The external Magellan acceptance gate remains
blocked until an upstream driver supplies content-addressed source materialization, isolated and
independently reset PostgreSQL worlds, allowlisted runtime secrets/networking, recorded external
effects, enforced tenant/user authorization, protected human approval, durable idempotency, and a
Responses-protocol agent endpoint.

## Read-only external assessment

The designated Magellan worktree was inspected without imports, execution, or writes:

- commit: `782e5b41ecda9be7d9edfdbd5c5e7791a41c9ca1`;
- branch: `my-new-branch`;
- source snapshot: `magellan-source-b7526cbd48ceab2e2c35a073`;
- dirty: yes;
- tracked changed paths: 31;
- included non-sensitive untracked files: 45;
- excluded volatile paths: 4;
- excluded sensitive ignored paths: 1 (name recorded, content not read);
- assessment blocker: environment handshake missing.

The snapshot identity is a point-in-time fact about a dirty worktree and must be regenerated if the
tree changes. It is not a frozen-sandbox acceptance result.

The audit also found upstream hard blockers: both OpenAI planner paths use Chat Completions; no
matched-world reset/fork boundary exists; tool/repository code commits internally; idempotency is
process-local; declared tenant/workflow/user-role capability identity is not enforced; approval
bypass is caller-constructible; and reviewer consultation returns no findings.

## Implemented evidence boundary

- Repository snapshots bind commit, binary tracked diff, per-file untracked source hashes,
  dependencies, migrations, and excluded path classes without serializing a machine-local root.
- Environment handshakes bind source/driver/database/authorization identities, source and world
  isolation, reset protocol, matched-world independence, secret/network/effect policy, Responses,
  durable replay, and the typed tool surface.
- Eight matched scenario families cover intake, negotiated rates, outreach waiting, regulated
  booking, tenant refusal, unavailable tools, invalid dependencies, and idempotent replay. Scenario
  manifests include concrete task inputs, and task checks bind sensitive calls and final rates back
  to those inputs.
- World, allocation, plan, call, observation, approval, failure, and verification records have
  checked digests and JSON Schemas. Traces separately record model latency/cost/tokens and tool
  latency/cost, while successful commit calls require evidence from every declared validator.
- The verifier independently decides environment, authorization, trace, safety, and task evidence.
  The first four become non-compensable hard gates.
- Reward observations preserve task completion, constraint satisfaction, recovery opportunity,
  efficiency, cost, and infrastructure missingness separately.
- Eligibility binds the exact scenario and trace digests. Baselines, sealed/quarantined records,
  and hard-gate failures are evaluation-only; continued pretraining always needs a separate source
  corpus.

## Local verification

`make check` passed:

- Ruff formatting and lint;
- 35 generated JSON Schemas with no drift;
- strict mypy across 92 source files;
- 128 hermetic tests passed, with 6 PostgreSQL/live/Lean tests deselected by the default gate.

Protocol tests include a temporary Git repository, ignored-secret non-disclosure, worktree drift,
handshake drift/blockers, shared-world rejection, successful durable replay, approval-bypass
rejection, target/baseline/sealed/quarantine eligibility, tenant refusal, invalid dependency
rejection, regulated approval, missing-validator rejection, task-input binding, replay namespace
enforcement, and infrastructure missingness.

The real Lean sandbox, credentialed GCS integration, PostgreSQL concurrency suite, external
providers, Magellan execution, and Inkling serving were not rerun for this slice. This slice did not
change those implementations.
