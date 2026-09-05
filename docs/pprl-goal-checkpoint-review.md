# Padawan goal checkpoint review

Historical snapshot through generation checkpoint `0452cba`. The user has since manually resumed
the goal. Current authority, subsequent shared resource-accounting work and validation are recorded
in [the execution ledger](pprl-execution-ledger.md) and [resource boundary](pprl-resource-boundary.md).
The pause and remaining-work statements below describe this review's original checkpoint.

Review date: 2026-09-05. Comparison base: clean goal-start HEAD `9d58ceb4ad5cb0ceac77c2ddf30e0ef83c5e69d0`.
Branch: `codex/pprl-information-boundary`. This review includes the generation-workload checkpoint
being committed with it, following nine earlier local commits. Detailed commands, failures, fixes,
user decisions and historical validation are in `pprl-execution-ledger.md`.

The main result is an implemented, adversarially tested offline boundary between institutional
state, worker observations, privileged forensic evidence and projected learning data. The first
exact generation-input checkpoint is also implemented. Padawan has not yet demonstrated a live
persistent research institution, complete worker replacement, or parameter learning.

The user requested a pause for discussion after this checkpoint. Implementation was held
until the subsequent explicit resume. The goal API has no pause operation, and Computer Use denied access to the Codex
app; its goal progress-row Pause control requires the user. No goal has been marked complete to
simulate a pause.

## All implementation checkpoints since goal activation

| Local checkpoint | What changed and why it matters | Principal implementation |
| --- | --- | --- |
| `2222c97` authoritative artifact classification | Local storage now persists complete classification metadata and validates actual stored identity instead of trusting caller flags. Raw-only ordinary reads/exports and cached GCS relabeling are denied. Metadata publication and catalog validation fail closed. | `LocalArtifactStore.read_bytes` / `storage_metadata` in `padawan/artifacts/store.py`; `padawan/artifacts/gcs.py`; `ExportPolicy` in `padawan/governance/policy.py` |
| `11e9651` reviewed process evidence | Added immutable information classes and distinct process/forensic references. A separately reviewed admission binds exact candidate bytes, rights, scope, source set and policy. Reads recheck current authority and independently owned sources; private review/source IDs stay outside worker references. | `ArtifactInformationStore`, `ProcessArtifactRef`, `ForensicArtifactRef`; `ProcessEvidenceStore.admit/read` |
| `fe6761e` worker-output separation | PPRL returns public output and integer token counts. Raw traffic, private channels, arbitrary provider metadata and detailed failures remain privileged. Events join invocation evidence privately instead of publishing raw references. | `ProcessGenerationExecutor.execute`, `ProcessWorkerOutput`, `ProcessStore.append_event` |
| `b806b2f` reference ingress and transactional ownership | New initial states/events require reviewed references; claims refuse legacy or unretained references. State/event/fork ownership is atomic. A real SQLite savepoint bug was corrected so outer rollback removes successful nested writes too. | `ProcessStore.create_rollout/append_event/claim_next/fork_rollout`; `Database` transaction setup; `ProcessEvidenceStore.retain_for_process` |
| `096e8af` structural content admission | Closed core state/event shapes, registered bounded extension schemas, admitted memory/evidence links, forensic-identifier checks and immutable exact-policy receipts. Nested dictionaries and filenames/references no longer gain admission solely by being strings. | `ProcessContentBoundary`; `process_content_admissions` |
| `0b54532` exact worker observations | Added an allowlisted worker DTO and canonical-byte receipt with private source/lease/rights/authority lineage. Planner input and trusted effect execution are separate. Admitted and denied proposals retain observation linkage; failed binding cannot erase the original Amber decision. | `ProcessObservationStore.observe_claim/read/bind_decision`; `ProcessCoordinator`; `process_observations`, `process_observation_decisions` |
| `b2f3628` explicit learning projections | Privileged version-1 archives are reconstructed and compared to native sources before separate public JSONL is admitted. Checks include source rights, observation/content lineage, use-specific evidence and replication minima after exclusions. Private source/sample/fork lineage stays separate. | `ProcessTrainingProjectionStore.compile/read_product`; `process_training_projections` |
| `ea41ba7` Atlas forensic retention | Trial request/result and exploratory-proposal writes verify physical bytes, forensic classification and independent retention, including failed-call sources. Study block admission and sealing revalidate dependencies. | `AtlasArtifactBoundary`; `AtlasRegistry`; `AtlasFixedTrialStudyBridge` |
| `e486696` reviewed Atlas institutional evidence | Added a versioned boundary from exact native Atlas trials and context into a separately reviewed institutional derivative. It checks source/output rights, target scope, disclosure policy and private origin lineage. Retaining Atlas data grants no automatic institutional or training use. | `AtlasEvidenceSourceBoundary`; `ProcessEvidenceStore.admit_atlas`; version-2 evidence receipts |
| Generation-workload checkpoint in this commit | Exact observed input plus a reviewed fixed policy is bound to normalized request, prepared body, provider/model/protocol and destination before I/O. Single prepared dispatch excludes ambient client state, redirects and retries; unresolved effects do not resend. Current authority/deadline are rechecked before dispatch/output; replay preserves original evidence. | `ProcessGenerationBoundary`; `PreparedGeneration`; `OpenAICompatibleClient.generate_prepared`; `process_generation_workloads` |

Cross-cutting changes include additive migrations and generated schemas, independent retention
ownership, generic worker-facing denials without private exception chaining, historical-versus-new-use
distinctions, and proportional regression tests. The fixture clock now advances deterministically
inside its original short authorization; production expiry checks were preserved. The execution
ledger and canonical handoff were updated throughout. Existing legacy histories were not silently
backfilled or relabeled, and no production migration was run.

## Position against the initial audit

| Audit issue | Current evidence and remaining gap |
| --- | --- |
| Forensic records could be mistaken for shared memory or ordinary artifacts | Concrete storage, reference, content, result, observation and projection paths are now guarded and tested offline. Semantic redaction/source completeness and encoded channels remain unproven. |
| Shared writable namespaces create ambient communication | Known reference/metadata laundering paths and inherited HTTP client state are guarded. Generic filesystem/cache/service isolation and addressed live messaging are still absent. |
| Worker-visible state lacked an exact projection and causal observation record | Exact public observations and decision bindings now exist. New generation binds their bytes to a declared prompt policy and prepared transport body. Role-specific action masks and attested delivery remain absent. |
| Policy intent could be mistaken for execution evidence | Configured generation inputs/effects now have stronger private receipts. Amber still does not create or attest an OS/container/VM/network/accelerator/filesystem/secret sandbox. Adapter/runtime/reviewer identities remain trusted declarations. |
| Retention and database rollback could diverge | New owned references are transactional, including multi-child forks; SQLite outer rollback is fixed. Concurrent GC with a stale reference snapshot and a complete independent forensic root remain unresolved. |
| Training products could be confused with admitted model-learning data | Separate validated public projections now exist; old archives remain privileged. `parameter_training_ready=false` remains mandatory. No trainer, parameter update or independently evaluated candidate exists. |
| Repeated calls could duplicate unknown external work | New PPRL dispatch refuses unresolved automatic retry and retains uncertainty. Persisted completed results can be reused under current lineage. This is conservative denial/reuse, not an executable stale-worker/effect reconciler. |
| Atlas was disconnected from institutional memory | A reviewed native-Atlas-to-process derivative path now exists. Institutional Atlas subjects, the Atlas–Inkling MI interchange, campaign execution and Atlas learning materialization remain later work. |
| Process continuity through complete replacement was not established | Committed institutional state persists and fresh offline receipts can reproduce the same observation. There is still no PPRL worker launcher/registry/assignment/hydrator/recovery lifecycle proving 100% replacement. |

The four fabrics remain distinct. Persistent process state has been hardened. Communication remains
mostly declarative. Forensic retention and explicit disclosure are stronger but not independently
attested or complete. Recovery still lacks its executable PPRL runtime. No change turns worker-local
KV caches, stacks, conversation context or unfinished execution into institutionally persistent state.

## Position on the full roadmap

1. **Information boundary:** substantial offline implementation, with tested stores, admissions,
   projections and retention. Remaining trust, semantic and coordinated-GC limits are explicit;
   this is not full live isolation.
2. **Containment, identity, causal/resource integrity:** started. Exact configured generation inputs
   are bound; conserved accounts/reservations, authenticated runtime identities, independently
   enforced boundaries and forensic capture remain. Shared resource admission is the next proposed
   local slice after discussion, not work already implemented.
3. **Recovery and scheduling:** not implemented for PPRL. Requires assignment ownership, fencing,
   heartbeat, reconciliation, executable hydration, pause/resume and total replacement tests.
4. **Coordination, live tracking and economics:** not implemented as a live system. Durable goals,
   evidence, dependencies and budgets must feed deterministic dispatch and bounded escalation;
   stronger-model consultations must be attributable, costed, deduplicated and retained.
5. **Atlas and mechanistic measurement:** source retention and reviewed disclosure are implemented.
   Worker-versus-institution experimental subjects, exact serving identities and versioned Inkling
   interchange remain. Mechanistic records stay privileged; causal claims require interventions.
6. **Institutional science and long horizons:** no live campaign or long rollout has run. Distribution,
   replicate, fork, stopping-rule, regret and matched-budget methods still need operationalization.
   Resumable 10M-plus-token macro-rollouts remain an explicit milestone; billion-token research is
   a longer-term ambition. Token volume does not replace independent samples or useful progress.
7. **Parameter learning and promotion:** no underlying Nemotron or Inkling parameters changed.
   Requires attributable worker decisions, behavior-policy/action-mask/credit lineage, selected
   trainer, reproducible candidate parameters/adapters, serving validation, independent parent
   comparison, promotion criteria and rollback. Deployment is separately authorized.

The General/lieutenant/worker hierarchy is still a hypothesis. Local Nemotron, Inkling coordination
and selective frontier advice are candidate configurations, with direct escalation allowed when
evidence and total cost favor it. Every tier remains replaceable. None of the engineering above
establishes hierarchy value, epsilon-charity, emergence, scientific improvement or parameter learning.

## Validation and decision

Final suite: **604 passed, 6 deselected in 72.72 seconds** using
`PYTHONPATH=. .venv/bin/pytest -q -m 'not postgres and not live and not lean and not gcs' --tb=short`.
Ruff passed, 326 files passed formatting, mypy passed for 171 source files, and 158 schemas match.
Disposable SQLite migration checks and mock HTTP/fake-GCS tests are included. PostgreSQL, live
models/services, Lean and real GCS are not validated by these results. A passing synthetic suite
establishes engineering behavior within the stated trust assumptions, not experimental success.

Work remained local: no live model inference, model/worker launch, GPU/cloud activation, trainer,
parameter update, sibling edit, production migration, push or deployment. Per-checkpoint changed
and staged credential-pattern checks are retained in the ledger; these are not whole-history audits.

Verdict: the generation checkpoint is ready for local retention and discussion. Further implementation
is on the user's requested hold. A live swarm, training campaign or 10M-token experiment remains
no-go until its foundational gates and concrete execution authorization are satisfied.

The most consequential unresolved questions are:

1. What independently enforced identity/containment/forensic boundary will protect the eventual
   owned-hardware runtime, beyond trusted Python objects and declared configuration digests?
2. What conserved funding, reservation and reconciliation semantics will prevent workers, forks,
   retries and replacement from creating budget, particularly with unknown external effects?
3. Which first preregistered distribution, matched baselines, trainer and independent evaluation
   will distinguish institutional memory/coordination gains from stronger assistance, extra compute
   and actual parameter improvement?

No new cloud or model authorization is needed to discuss these questions or later resume local
engineering. The user must resume implementation after this pause; a concrete cloud/GPU campaign
still requires a separate manifest, ceilings, stop conditions, cleanup and explicit approval.
