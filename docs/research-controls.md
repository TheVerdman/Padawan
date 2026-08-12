# Research controls and comparability

Padawan treats the harness as part of the measured system. The governing measurement model is:

```text
observed performance = f(
  checkpoint,
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
student checkpoint, model, quantization, runtime, serving artifact and protocol; auxiliary teacher
or adjudicator models; task and corpus digests; the effective workflow, condition, teacher-mode,
and sampling-policy parameters; source, Python/platform, and core dependency environment identity;
and seed.
Component evidence is explicitly `pinned`, `declared`, or `unknown`. A digest of a declaration is
not presented as a weight digest.

Non-secret serving parameters (for example tensor-parallel size, batch size, protocol/storage
settings) and environment parameters (source revision, Python/platform, and core dependency
versions) are retained alongside their validating digests. The digest detects drift; it is not a
substitute for the configuration needed to interpret or reproduce the run.

Profiles and execution manifests are content-digested in `harness_profiles` and
`research_executions`. New live developmental runs bind one execution digest in `runs`; their
matched experiment binds the same digest in `experiments`; completed or failed episode records and
hash-chained workflow provenance repeat it. The binding cannot be silently replaced during run or
experiment replay.

## Comparability rules

`ResearchControlRegistry.compare` compares two execution manifests along explicit axes:

- checkpoint;
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
was allowed in advance. An unknown required component or an unestablished effective context limit
is a provenance gap, not a default value.

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
difference was not declared. New checkpoint-evaluation admission applies the same fail-closed
gate. Legacy experiments and studies remain readable and statistically analyzable, and an active
legacy run may finish under its unchanged persisted parameters; absence of a research-execution
binding still prevents a new comparative claim.

## Current standardized live profile

The live developmental CLI constructs a standardized boundary-mapping profile from the selected
domain, source revision, non-secret environment identity, current corpus inventory, runtime, and
provider configuration.

For validated Inkling serving, the profile records:

- the exact converted checkpoint, quantization profile/conversion manifest, serving image,
  runtime revision, Responses protocol, TP4/batch-one parameters, and configured context window;
- the narrower 240,000-token validated transport target as the effective input-limit evidence,
  without treating it as long-horizon reasoning evidence;
- response storage and `previous_response_id` continuation disabled;
- reasoning retention between requests disabled;
- model-emitted private-reasoning capture enabled as restricted evidence, but never reused as
  ordinary context; and
- truncation and `StateCompactor` execution disabled in the live developmental workflow.

These fields preserve the validated Inkling continuation guard. Provider continuation can later be
registered as a positive-control profile, but no provider-specific state mechanism is required by
the experiment architecture.

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
