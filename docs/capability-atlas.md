# Capability Atlas v0

Capability Atlas is Padawan's model-neutral behavioral-science subsystem. It characterizes an exact
checkpoint, quantization, serving runtime, harness, task/corpus, environment, and seed identity;
searches for uncertain boundaries; and preserves the evidence needed to compare harnesses,
artifacts, and later checkpoints. Inkling-Small-Ampere is the first campaign target, not Padawan's
product identity.

The executable catalog and first campaign live in:

- `padawan.atlas.catalog`: upstream claims and dataset governance;
- `padawan.atlas.campaigns`: deterministic ontology, suite, condition, partition, offline-check,
  and externally gated campaign builders;
- `padawan.atlas.reporting`: one machine/human reporting projection that keeps evidence classes
  separate.

All no-argument builders are deterministic at the v0 catalog timestamp:

```python
from padawan.atlas.campaigns import (
    build_capability_ontology,
    build_first_inkling_campaign,
    build_first_inkling_campaign_bundle,
    offline_verification,
)
from padawan.atlas.catalog import dataset_governance_records, source_claims
from padawan.atlas.reporting import machine_report_json, render_first_inkling_report
```

They generate local manifests only. They never call an endpoint, deploy or wake a GPU, accept
provider terms, download benchmark content, mutate an external system, or spend money.

## Four evidence lanes

Reports always use four disjoint lanes:

1. `reported_upstream_claims`: primary-source priors, including vendor caveats;
2. `locally_reproduced_observations`: immutable Padawan results under one exact
   `ResearchExecutionManifest`;
3. `extrapolations`: explicitly labeled estimates with a declared method;
4. `unknowns`: missing endpoint, modality, authority, suite, or execution evidence.

An upstream score cannot enter the local lane. A benchmark name, configured modality, model card,
or content-free suite registration cannot create an observation.

### Inkling primary-source claims

The scores in the Capability Atlas mission brief are the July 15 **Inkling-Small preview**, not the
released checkpoint. Atlas registers preview and release as different model revisions; release
scores do not supersede preview scores as corrections.

| Benchmark | July 15 preview | July 30 release |
| --- | ---: | ---: |
| GPQA Diamond | 88.3% | 89.5% |
| AIME 2026 | 95.1% | 95.5% |
| SWE-bench Verified | 77.4% | 80.2% |
| Humanity's Last Exam with tools | 46.6% | 47.8% |
| IFBench | 83.4% | 82.2% |
| MMMU-Pro Standard 10 | 73.1% | 74.0% |
| MMAU | 77.5% | 77.0% |

Primary sources are the [official preview publication](https://thinkingmachines.ai/news/introducing-inkling/),
[official release publication](https://thinkingmachines.ai/news/inkling-small/), and
[official model card](https://huggingface.co/thinkingmachines/Inkling-Small). The v0 catalog records
pre-community-metadata model-card revision
`b2d4f225a02032c5d154bff748ab5a00c5ca26e4`.

The publications label the benchmark table `effort=0.99`; the release also reports temperature 1,
a 256K maximum sequence length for coding evaluations, and Bash-only SWE-bench tools. Exact suite,
item, prompt, evaluator, retry, seed, token, action, and cost manifests remain incompletely bound.
GPQA, HLE, and MMMU-Pro release evaluations are reported as provided by Artificial Analysis.
Therefore every score is a prior, not proof that Padawan's W8A16 Ampere artifact reproduces source
BF16 behavior.

## Rights and suite admission

`DatasetGovernance` binds an exact dataset revision to rights, license/terms evidence, access,
redistribution, contamination, evaluation class, and sealed handling. Execution requires local
access plus confirmed evaluation permission. These decisions fail closed:

- GPQA and ARC-AGI-2 are open registrations, but no content is vendored or frozen.
- MathArena/AIME data carries CC-BY-NC-SA-4.0 terms and remains registration-only.
- SWE-bench framework code does not resolve each target repository and container artifact's rights.
- IFBench data/code declarations do not remove Ai2 responsible-use or third-party output review.
- HLE is gated and prohibits public redistribution of restricted content.
- MMMU-Pro and MMAU require item/media review; MMAU also lacks a locally authoritative full-answer
  evaluator.
- ARC-AGI-3's public toolkit does not grant private competition evaluation access.

Atlas v0 copies no public benchmark question, answer, or media. The corresponding suite records are
blocked with exact admission requirements.

## First W8A16 campaign

The exact student identity is the validated Padawan serving contract:

- checkpoint `conversion-e747e8121d5cd12c54c9`;
- quantization/served model `w8a16-balanced-v1`;
- profile `responses-256k-candidate-v1`;
- runtime revision `aa2e7dd0f8f5fd1be0e4449f802ae5b72ffc534a`;
- TP4, configured 262,144-token window;
- promoted evidence limited to batch-one exact retrieval through a 240,000-token target.

That 240k record is transport/exact-retrieval evidence. Atlas deliberately represents it as a
blocked registration and never converts it into long-horizon reasoning or usable-working-memory
evidence.

### Locally generated suites

V0 freezes 100 content-addressed, project-authored items:

- 32 algebra adaptive items across eight symbolic families;
- 30 temporal state/freshness/calibration items;
- 8 disjoint algebra curriculum/training-candidate items;
- 8 Lean challenge items, blocked until the pinned kernel environment is active;
- 6 appellate items, blocked until semantic/currentness authority is registered;
- 16 Magellan strategy/recovery items, blocked until the isolated environment is bound.

Every item binds prompt, family, difficulty, adapter, verifier version/payload, generator seed,
matched-pair ID, rights, contamination scope, and source metadata. Every suite binds sorted item,
task-manifest, corpus, modality-gate, and environment digests. The offline builder structurally
checks 45 matched neighborhoods and executes 70 deterministic oracle payloads. It does not label
those structural checks as independently verified metamorphic relations.

The algebra and temporal adaptive suites and disjoint algebra training-candidate suite are content
ready. "Ready" describes content/verifier admission, not endpoint authorization or observed model
capability.

### Promotion lifecycle

The N versus N+1 promotion suite is intentionally unmaterialized and blocked. It has neither
exposed items nor a core `EvaluationSuiteManifest` digest. Atlas never labels it sealed until that
bridge exists. Adaptive-search, training-candidate, and promotion suite digests are pairwise
disjoint; training and adaptive item digests are also disjoint.

### Boundary design

The campaign predeclares:

- difficulty ladders, matched minimal differences, metamorphic neighborhoods, and adaptive
  allocation near 50% success;
- effort labels 0.0/0.5/0.99, standardized versus optimized harnesses, tools off/Bash-only,
  deterministic/repeated stochastic sampling, and 32k/128k/240k context controls;
- a full retained-public-reasoning × deterministic-explicit-history-compaction factorial;
- fixed-before-results confidence-width, infrastructure-failure, stability, and promotion rules;
- calibration, selective answering, consistency, self-correction, and repeated-action analyses;
- a quantization-only source-BF16 versus W8A16 comparison, permitted only when all other controls
  match.

The retention × compaction design is motivated by [OpenAI's official ARC-AGI-3 analysis](https://openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores/),
which showed that retained reasoning and compaction can change elicited performance materially. That
publication is design context, not evidence about Inkling or this W8A16 artifact.

When trials exist, reports will preserve fixed denominators and separate pass@k from item-level
accuracy; count missingness, timeouts, infrastructure failures, and contamination; estimate
capability curves and uncertainty; measure calibration and selective answering; compute tokens,
actions, wall time, and cost per verified success; summarize reviewable failure clusters; and render
standardized/optimized, source-BF16/W8A16, and checkpoint N/N+1 regression matrices. Empty evidence
lanes stay empty rather than being filled from priors.

The effort labels require special care. Sibling serving validation exercised only
`reasoning.effort='none'`, and that does not establish behavioral performance. Numeric vendor effort
labels are not assumed to be Responses wire values. Every non-`none` condition remains blocked until
a registered adapter mapping and live edge preflight bind a supported value. Likewise, the
`GenerationRequest` tool surface exists, but every tool structure and loop needs behavioral
validation.

Retention/compaction conditions preserve the continuation guard: explicit history only, response
storage disabled, `previous_response_id` disabled, and private reasoning never reused as context.
The deterministic compactor emits a content-addressed authoritative temporal-frame summary and
fails the condition closed unless that representation is shorter than the frozen source history;
it never wraps the full source transcript and calls that compaction.

## Failure ontology and authority

The versioned ontology supports overlapping assignments for knowledge, mathematical/scientific
reasoning, coding/agency, planning/tools/recovery, instruction/structure, epistemics/calibration,
temporal/freshness, retrieval versus state use, media grounding, robustness, stochasticity,
self-correction, and repeated action. It keeps parser, verifier, harness, infrastructure, and
contamination failures separate from model failures.

Automated clustering may propose assignments, but admission requires review provenance. A model
grader never outranks deterministic, kernel, environment, or declared human authority.

## Interaction Lab seam

Interaction Lab owns human chat, consent lanes, exploratory sessions/turns, explicit-history
interaction, trace inspection, and feedback. Atlas consumes only a governed proposal:

1. declared consent evidence and a restricted raw trace produce a privileged
   `ExploratoryFailureProposal` carrying a separate redacted-excerpt digest;
2. `raw_chat_promoted` remains false and the proposal is deduplicated;
3. Atlas creates an independently generated reproduction item under an exact execution manifest;
4. at least two result digests establish or reject reproduction/stability;
5. human review may admit a new item digest to a challenge suite.

Raw chat is never benchmark evidence and never enters promotion or training automatically.
The registry checks declared provenance; it does not itself attest consent or perform/verify the
excerpt's semantic redaction.

## Forensic source retention

New trial requests, results, and exploratory proposals require an explicitly configured
`AtlasArtifactBoundary`. The broker verifies their explicit artifacts against actual backend bytes,
classifies them as forensic, and pins them atomically under independent request/result/proposal
owners. Failed calls include captured requests and available error responses. Exact retries validate
existing evidence and ownership without repairing legacy records. New Study block admission and
Atlas result sealing also revalidate source retention; configure `StudyEngine(artifacts=catalog)`.

This is an offline storage and provenance check, not producer authentication or permission to use
raw evidence in model inputs. Reports, upstream extraction artifacts, and other downstream Atlas
paths still require their own use-boundary review. See
[Atlas forensic retention](atlas-forensic-retention-boundary.md) for exact source fields, limits,
trust assumptions, acceptance evidence, and rollback.
An additional [reviewed Atlas-to-process boundary](atlas-process-evidence-boundary.md) now admits
separate institutional evidence artifacts under an explicitly composed source/target policy. It
retains a version-2 private origin receipt and exposes only the reviewed derivative. It does not
consume eligibility flags, write developmental memory, or grant training use.

## Mechanistic-interpretability interchange

Padawan owns behavioral phenomena, matched probes, authority, and outcomes. The sibling Inkling
interpretability repository owns activation/router telemetry and interventions. Stable joins use:

- content-addressed `phenomenon_id` plus phenomenon manifest digest;
- content-addressed `probe_set_id` plus probe-set manifest digest;
- sorted Atlas item digests;
- immutable Atlas outcome-result digests and aggregate outcome digest;
- exact checkpoint/runtime identity from the execution manifest.

Mutable names, paths, aliases, or benchmark labels are not join keys. Telemetry cannot change an
Atlas grade; a behavioral cluster cannot imply a mechanism without independent intervention
evidence.

## Training eligibility

An Atlas failure is merely a candidate until it is independently reproduced and stable; attributed
to the model after harness effects are ruled out; supported by authoritative verification; licensed
for the target training lane; cleared of contamination; and excluded from adaptive, challenge, and
promotion suites. Even eligible evidence cannot enter the compiler directly. It first requires
governed corpus materialization under Padawan's existing training-product contracts.
That Atlas training-materialization/learning-admission adapter is not implemented. The separate PPRL public
learning projector does not admit Atlas records, and eligibility records have no direct compiler
or memory consumer. Retention and eligibility cannot substitute for reviewed use authorization.

## External authorization plan

The manifest reconciles every condition ceiling against every bound
`planned_item_count × trials_per_item`. The three allocation sets partition all 7,238 planned
trials, so no bound trial is hidden outside the cost plan:

| Allocation set | Requests | Input tokens | Output tokens | Actions | Cost ceiling | Serial runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| W8A16 content-ready local | 1,306 | 51,642,368 | 19,111,936 | 3,196 | $2,049 | 16,660 min |
| W8A16 blocked expansion | 4,192 | 139,850,240 | 82,706,432 | 29,654 | $8,440 | 72,300 min |
| Source-BF16 reference | 1,740 | 57,016,320 | 28,508,160 | 1,740 | $4,350 | 17,400 min |
| **Full ceiling** | **7,238** | **248,508,928** | **130,326,528** | **34,590** | **$14,839** | **106,360 min** |

The first stage is bounded to suites whose content/verifier manifests are ready, but it still needs
explicit authorization, a live matching edge, effort/tool preflights, and execution bindings. The
second cannot run until each public/environment/promotion suite clears its blocker. The third cannot
run until an exact source-BF16 endpoint/path is registered and every non-quantization control
matches. No command is authorization, and the offline implementation spent $0.

Every command emitted by `padawan atlas plan` and `padawan atlas report` now targets the real,
preparation-only CLI seam:

```text
padawan atlas campaign prepare --preparation-only --campaign-digest CAMPAIGN_DIGEST --allocation-set ALLOCATION_SET --authorization-ref APPROVAL_REFERENCE --max-requests EXACT_REQUESTS --max-input-tokens EXACT_INPUT_TOKENS --max-output-tokens EXACT_OUTPUT_TOKENS --max-actions EXACT_ACTIONS --max-cost-usd EXACT_COST --max-runtime-minutes EXACT_RUNTIME
```

The command rebuilds the first campaign, recomputes the complete condition/suite allocation
partition, requires every supplied ceiling to equal the selected frozen allocation set, and rejects
placeholder authorization references. It emits a content-addressed activation envelope and the
future evidence-artifact plan. It performs no network call, database write, artifact write, provider
request, GPU action, or spend. The authorization reference is not verified and
`execution_permitted` is always false. `padawan atlas campaign run` is a fail-closed sentinel until
a governed authorization/execution gateway is implemented.

See `reports/verification/2026-08-12-capability-atlas-v0.md` for the generated verification
handoff.
