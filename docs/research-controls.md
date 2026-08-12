# Research controls and comparability

Padawan treats the harness as part of the measured system. The governing measurement model is:

```text
observed performance = f(
  checkpoint,
  learned parent state,
  quantization and serving,
  task and corpus,
  harness,
  context policy,
  prompts and tools,
  budgets,
  environment,
  seed
)
```

A score is therefore not a checkpoint-only property. A result may be described without complete
controls, but a new comparative or causal claim fails closed until the relevant identities are
present and the changed axes are declared.

## Durable control objects

`HarnessProfile` is an immutable, versioned experimental object. Its `tier` and `purpose` are open
identifiers rather than a closed enum: `standardized` and `optimized` are current conventions, not
the only future designs. A profile binds:

- continuation, response-storage, and reasoning-retention semantics;
- private-reasoning capture separately from reuse of reasoning as future context;
- configured and effective context limits, history selection, token counting, truncation, and
  compaction policy;
- versioned prompt and tool identities;
- explicit action, input-token, output-token, latency, wall-time, retry, and cost budgets, each
  marked capped, unbounded, or not applicable; and
- typed but initially empty instrumentation references for capability-atlas, interactive
  trajectory, mechanistic telemetry, and checkpoint-evaluation schemas.

`ResearchExecutionManifest` binds one run configuration to the profile digest. It records the
student checkpoint, model, quantization, runtime, model-server and transport-edge artifacts, and
protocol; the exact learned parent-state ID and immutable state hash; auxiliary teacher or
adjudicator models; task and corpus digests; the effective workflow, condition, teacher-mode, and
sampling-policy parameters; source, Python/platform, and core dependency environment identity; and
seed.
Component evidence is explicitly `pinned`, endpoint-`verified`, `declared`, or `unknown`. A digest
of a declaration is not presented as a weight digest. A mutable transport edge must be pinned or
verified by the endpoint that will execute the run before it can support a causal claim.

Non-secret serving parameters (for example tensor-parallel size, batch size, protocol/storage
settings) and environment parameters (source revision, Python/platform, and core dependency
versions) are retained alongside their validating digests. The digest detects drift; it is not a
substitute for the configuration needed to interpret or reproduce the run.

Profiles and execution manifests are content-digested in `harness_profiles` and
`research_executions`. New live developmental runs bind one execution digest in `runs`; their
matched experiment binds the same digest in `experiments`; completed or failed episode records and
hash-chained workflow provenance repeat it. The binding cannot be silently replaced during run or
experiment replay.

Worker admission uses the same execution identity. The live composition root derives a non-secret
worker configuration from the clients and workflow it actually constructed. Admission compares the
complete harness profile—including continuation, context/compaction, prompts, tools, budgets, and
instrumentation—plus every effective harness parameter, provider/model, checkpoint/runtime,
auxiliary model, endpoint/transport identity, task manifest, current corpus digest, domain/workflow,
and environment fingerprint. Before the sampling action, the corpus digest is recomputed from active
or currently leased inventory in the same claim transaction; retirement and quarantine remove items
from that identity, while a transient active-to-leased ownership change does not rewrite it. Once a
controlled run has durably stored its episode and exact item leases, later actions use that frozen
sample and no longer require unrelated current inventory to remain unchanged. A worker with no
declared configuration, a changed harness, or a different task/model/transport identity may still
process legacy unbound work but cannot claim a controlled run.

Registration is not enough by itself: controlled run creation compares the durable domain, pool,
teacher mode, treatment/control conditions, and retry budget with the manifest before persistence.
Controlled experiment creation performs the same treatment/control check. Contradictory payloads
therefore fail before any worker or experiment can execute under the bound digest.

## Comparability rules

`ResearchControlRegistry.compare` compares two execution manifests along explicit axes:

- checkpoint;
- learned parent state;
- quantization;
- task/corpus;
- harness tier, purpose, and effective workflow parameters;
- continuation/reasoning retention;
- context policy;
- prompts;
- tools;
- budgets;
- student serving;
- auxiliary models;
- environment;
- instrumentation; and
- seed.

The result is `identical_controls`, `controlled_difference`, `not_comparable`, or
`insufficient_provenance`. A controlled difference is comparable only when every differing axis
was allowed in advance. An unknown required component—including an enabled compactor or a declared
instrumentation schema—an unverified transport artifact, or an unestablished effective context
limit is a provenance gap, not a default value.

`StudyManifest.comparison_axes` declares the axes intentionally varied by a study.
`StudyExperimentBinding.factor_values` supplies open-ended factor labels for each condition. This
allows a later reasoning-retention × compaction factorial to use, for example, factor names
`reasoning_retention` and `compaction` without changing the schema. The corresponding execution
profiles must still record the real continuation and context behavior; labels alone carry no
authority.

Profile IDs and release versions locate immutable records; they are not themselves performance
factors. Comparability is computed from the recorded semantics. Thus two profiles that differ only
in continuation and compaction report those two axes, rather than an additional synthetic
profile-label difference.

Study aggregation reports the observed differences, declared axes, blocking differences, and
provenance gaps. A study cannot permit a causal claim when its controls are missing or an observed
difference was not declared. A controlled study additionally requires every execution's task
manifest and environment fingerprint to be admitted by its registered suite. New checkpoint
evaluation requires that suite to be sealed and digest-valid, and the evaluated checkpoint must
participate in the named condition of the controlled study. A study cannot transition to complete
until every bound block has a persisted outcome. Completing it seals one content-addressed
`StudyResultRecord` per condition and checkpoint from that block evidence. A sealed result permits
a causal claim only when every block is analyzable and none is missing, contaminated, or excluded
for infrastructure. Every new checkpoint metric must cite exactly one such result and exactly
match its metric value and missingness. Every hard gate must independently bind the same immutable
result, study manifest, suite, condition, and checkpoint; a verifier from another evaluation cannot
be reused. Active studies, invented references, cross-condition/checkpoint references, partial
results, and results without causal-claim permission are rejected. Promotion revalidates this
lineage. The current paired-block aggregation emits only `treatment_success_rate`,
`control_success_rate`, and `paired_gain`; retention, non-interference, and efficiency metrics need
their own governed result producers rather than aliases. Legacy experiments and studies remain
readable and statistically analyzable, and an active legacy run may finish under its unchanged
persisted parameters; absence of a research-execution binding still prevents a new comparative
claim.

## Current standardized live profile

The live developmental CLI constructs a standardized boundary-mapping profile from the selected
domain, source revision, non-secret environment identity, current corpus inventory, runtime, and
provider configuration.

For validated Inkling serving, the profile records:

- the exact converted checkpoint, quantization profile/conversion manifest, serving image,
  runtime revision, separately verified Responses-edge image/deployment identity, Responses
  protocol, TP4/batch-one parameters, and configured context window;
- the narrower 240,000-token validated transport target as the effective input-limit evidence,
  without treating it as long-horizon reasoning evidence;
- response storage and `previous_response_id` continuation disabled;
- reasoning retention between requests disabled;
- model-emitted private-reasoning capture enabled as restricted evidence, but never reused as
  ordinary context; and
- truncation and `StateCompactor` execution disabled in the live developmental workflow.

These fields preserve the validated Inkling continuation guard. Provider continuation can later be
registered as a positive-control profile, but no provider-specific state mechanism is required by
the experiment architecture. Endpoint provenance stores only a sanitized scheme/host/port origin;
URL userinfo is never retained. Live Inkling composition requires an expected edge image digest or
deployment revision, reads the responding edge identity from authenticated capability preflight,
and refuses a mismatch before constructing an admissible worker. It repeats the check immediately
before every generation to detect redeployment during a worker's lifetime. The observed identity—not
the operator declaration—is bound to the execution, so redeploying edge code changes or blocks the
execution identity even at the same origin.

## Claim separation

Reports should keep these results distinct:

- standardized-harness performance;
- optimized-harness performance;
- harness uplift under a declared harness/context comparison;
- quantization delta under a declared quantization comparison;
- teaching delta within matched treatment/control blocks;
- training delta between frozen checkpoint N and an externally produced N+1; and
- retention or compaction effects only after a factorial design localizes them.

Changing both reasoning retention and compaction permits a joint harness comparison; it does not
identify which change caused the gain. Large context or successful exact retrieval likewise does
not establish usable long-horizon memory.

## Deferred seams, not implemented results

This slice does not ingest a capability atlas, execute interactive Magellan or ARC-AGI-3
trajectories, capture router/expert or activation telemetry, run a retention × compaction study,
train weights, or evaluate checkpoint N+1. The profile has typed references for those later
contracts, and studies have open factor values, but the default references remain absent. Missing
instrumentation must remain absent rather than being synthesized from private reasoning or serving
metadata.
