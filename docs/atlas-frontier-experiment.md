# Capability frontier experiment

Design revision: 2026-09-06. This replaces the recommendation to run the fixed 96-rollout
elementary-algebra pilot. Its frozen files remain historical preparation evidence. This document
selects the scientific direction and records the first scout. The authorized configuration is
[nemotron-bf16-budget100-v1.json](../configs/atlas/nemotron-bf16-budget100-v1.json).
A partial real BF16 Responses run completed on four serving A100 80GB GPUs; all owned cloud
resources were subsequently removed. It retained 41 scored trials, six unresolved attempts and
145 never-dispatched cells out of 192 planned logical trials. The private research
[run report](../runs/atlas-budget100-20260906/report.md) contains the results, failure audits,
coverage limitations, operational failures and cleanup evidence. No PPRL effect was tested.

The separately authorized **64K versus 128K** experiment used a **$125 cumulative hard limit**,
including the first run. Its first deployment stopped during preflight; a repaired deployment
passed four real serving probes and scored eight of 32 planned trials before a guard-health refusal
stopped further admission. It found 0/4 accepted at each allowance, with 24 cells unrun. All owned
cloud resources and the temporary image were removed. The
[second-attempt design](atlas-64k128k-second-attempt.md) and
[partial-run report](../runs/atlas-64k128k-repaired-20260906/report.md) retain the declared cohort,
coverage limits, independent final-program audits and cleanup evidence. A later guard repair has
control-only validation; no additional GPU deployment followed. These are closed execution records,
and the intended repeated-sample comparison remains incomplete.

The user's follow-up fixes the next comparison to the latest eight problems. The
[fixed-comparison design](atlas-fixed-comparison.md) records statement/judge locks, bounded
guard recovery, continuous queue refill and a separately reported compiler/test arm proposal.
The local preparation and control checks do not authorize or constitute another GPU run.

## Research standard and question

Choose the strongest feasible, valid, controlled experiment. Engineering minimalism reduces the
machinery around the question; it does not reduce task difficulty, useful context, reasoning effort,
tool affordances or hypothesis strength. An easier alternative needs a concrete reason: unavailable
resources or data, an invalid comparison, an unreliable verifier, or an uncontrolled execution path.
An existing toy verifier, convenient record limit or successful smoke test is not such a reason.

First measure **where the exact worker begins to fail despite substantial reasoning/search budget**.
Then test **whether a persistent process moves that boundary beyond an equally funded uninterrupted
solver and a strong scripted coordinator, including after loss of every original worker context**.
Both parts must produce interpretable failures as well as plausible successes. Difficulty is an
observed relationship among task, model, harness and budget, not a property established by a label.

The next milestone is an empirical capability curve and inspected failure cases. A larger manifest,
another successful lifecycle fixture or a published benchmark's old low score does not meet it.

## Challenge selection

**Preferred primary challenge: LiveCodeBench Pro competitive programming**, including its medium
and hard Codeforces/ICPC/IOI problems. Use exact released statements, task limits and complete local
judge packages. Keep the published algorithmic and asymptotic demands. Neither downsize test inputs
nor invent many superficially different copies of one easy template to manufacture a distribution.
The [paper](https://arxiv.org/abs/2506.11928) supplies the benchmark rationale; its historical model
scores are priors, not evidence about the current Nemotron or Inkling artifacts.

There is an [existing local judging toolkit](https://github.com/GavinZhengOI/LiveCodeBench-Pro),
so use its task/checker semantics rather than design a new programming benchmark. The
[statement dataset](https://huggingface.co/datasets/QAQAQAQAQ/LiveCodeBench-Pro) required contact
sharing, which the user explicitly approved. Authorized local import succeeded at statement revision
`adebffce047dddb7768a86bace6aea4f7425e3bc` and test-archive revision
`5257736c0a4e30ba0949d41c56a257c323d9c600`. Six overlapping statement shards deduplicate to
864 problems; 158 lack a matching pinned test archive. The frozen sample contains 8 easy anchors,
24 medium and 32 hard problems, at most two from a Codeforces contest. All 64 selected archives
are supported batch packages, retaining 3,742 complete input/answer pairs. Twenty archives contain
extra complete pairs beyond their declared counts; all are retained and the discrepancy is recorded.
The HF credential is read from the user's already ignored credential file, never copied into this
repository, retained manifests, model requests or cloud configuration.

The upstream [judge launcher](https://raw.githubusercontent.com/GavinZhengOI/LiveCodeBench-Pro/main/judge.py)
uses a privileged container, host directory mounts, a published port, automatic restart and
on-demand downloads. Do not execute that launcher unchanged. Preserve grading semantics inside
an explicitly isolated Linux judging environment, with frozen data and scoped process ownership.
Separate candidate compilation/execution from the hidden-test/checker supervisor so generated code
cannot read answer files. The implemented local composition uses separate, network-disabled
candidate and checker containers. The Docker daemon, host and kernel remain trusted; this is not
independent execution attestation.

Two alternatives were checked rather than selected solely by reputation:

- **SciCode-Verified** is a promising second domain for scientific implementation and dependent
  subproblems. Its authors report major corrections to SciCode and much higher matched scores.
  Thus old SciCode failure rates cannot establish current difficulty. Audit the corrected task and
  numerical authority and measure difficulty before selecting this as the main persistence task.
  [Corrected benchmark and audit](https://github.com/flyingwagner/scicode-verified)
- **FrontierMath** offers a more ambitious mathematical challenge, but the full benchmark and
  verifier access are not established for this checkout. Public examples are useful stretch cases,
  not a substitute for the private benchmark distribution. Its upper tiers remain a candidate when
  access and meaningful task-level success become feasible. [Epoch's benchmark description](https://epoch.ai/benchmarks/frontiermath-tier-4-v2)

## Models and separate training and serving quotas

The 2026-09-06 authenticated GCP check for project `project-49b1b523-d248-434f-bd4` in
`us-central1` found these effective Vertex AI quotas:

| Allocation | A100 80GB GPU limit | Observed resource inventory |
| --- | ---: | --- |
| Custom model training | 4 | No nonterminal custom or hyperparameter-tuning jobs; no persistent resources |
| Custom model serving | 4 | One endpoint, with no deployed models |

These are separate service quotas, not an interchangeable eight-GPU serving pool. Reserve the
training allocation for training; size the frontier inference campaign within the four-GPU serving
allocation. The retained read-only responses are `runs/gcp-quota-check-20260906/quota.json` and
`usage.json`. Allocation-usage monitoring returned no samples in the preceding 48 hours, so the
inventory is the evidence for observed inactivity, not an independently metered zero. Quota does
not establish available physical capacity or an active reservation. Google's
[quota documentation](https://cloud.google.com/vertex-ai/docs/quotas) describes the distinct
training and serving limits; the project-specific API response supplies the values above.

Nemotron 3.5 Lightning remains the initial worker subject. Prefer its **BF16 reference** on the
four A100 80GB GPUs in the serving allocation for the main capability measurement. NVIDIA lists one A100 80GB
as a supported single-GPU deployment; this is a feasibility prior, not a measured Padawan memory
or throughput result. [NVIDIA model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16)

Use up to four independent single-GPU replicas when supported machine shapes, placement and measured
memory permit it; batch independent requests within replicas up to measured capacity. A brief serving check should choose
replica/batch/context settings that use the hardware well while preserving the requested reasoning
budget. Quota constrains concurrent devices, not scientific difficulty or the number of sampled
rollouts. Do not inherit the Mac's concurrency-one setting as a fleet-wide constraint. Keep dependent
steps within a rollout ordered. Freeze a whole adaptive allocation wave before launching it; select
the next wave only after the prior wave's dispositions are recorded. Faster completions must not
silently gain more sampling budget.

The Mac can supervise and run a separately labeled 4-bit comparison. Do not pool its outcomes with
BF16, claim an isolated quantization effect while also changing the runtime, or make its throughput
the pacing constraint for the GPU campaign. Use identical item/seed identities for comparisons,
while retaining precision/runtime as different subjects and reporting actual resource use.

Inkling W8A16 is the available stronger reference candidate. The inspected sibling at `33eae75`
documents a TP4 replica and an unvalidated concurrent Atlas profile; a CUDA-graph candidate failed
on its first generation. One TP4 reference replica uses the entire four-GPU serving allocation,
subject to actual placement and memory checks. Run its reference windows separately from Nemotron
serving windows. The training quota does not supply a second online-serving replica. Do not promise
unmeasured batch throughput. No sibling changes or new serving claims follow from this design.

## Find the boundary before spending the comparison budget

1. Freeze eligible problem IDs, checker/data revisions, publication dates, original difficulty
   metadata and group/contest membership. Prefer problems published after the exact model release.
   NVIDIA discloses LiveCodeBench-derived synthetic training material, so public benchmark branding
   alone is not a contamination guarantee. Older problems may support explicitly labeled diagnostic
   curves; they cannot establish unseen-task generalization. A post-release transfer set remains
   separate. No model gets editorials, reference solutions, hidden tests or prior study outcomes.
2. Start scouting across medium, hard and upper-tail strata, with a small easy anchor. Where numeric
   ratings exist, use adjacent rating bands; do not invent Codeforces ratings for other platforms.
   An initial allocation of 64 distinct problems and four stochastic repetitions per model gives
   256 requests for coarse coverage, not a powered comparative result. Balance groups across the
   available strata, limit contest clustering, and publish the actual realized allocation. Extend
   the range upward whenever scout performance approaches ceiling.
3. Give the worker a serious reasoning opportunity. Start a diagnostic output-budget ladder at
   8K/32K/64K generated tokens, including any private reasoning. Apply the ladder to matched scout
   items with independent repetitions; freeze the main budget after observing truncation and
   improvement, within an actual supported context window. Use the model's supported reasoning
   settings. If capability is still improving at 64K, report that the budget boundary is unresolved
   and evaluate a feasible extension; do not relabel token starvation as a reasoning limit.
4. Concentrate further repetitions and fresh problems around uncertain outcomes, approximately
   20–80% observed success with emphasis near 50%, while retaining anchors above and below. These
   percentages guide exploratory allocation, not acceptance claims from four samples. Inspect
   failed algorithms, counterexamples and complexity failures. If every upper-band task is solved,
   raise difficulty. If everything fails, check grading, inference and truncation, then test the
   full declared tool/search affordances and adjacent meaningful strata. A floor is not a useful
   comparative experiment merely because the benchmark is prestigious.
5. Separate standard code generation from a tool-assisted search condition. The latter permits
   compilation, public examples and model-authored tests in an isolated scratch environment, with
   substantial repeated search. Official hidden evaluation stays terminal and outside the worker's
   observations. Additional hidden-test feedback would be a different, explicitly declared study.
   Correct code, syntax/format errors, candidate runtime limits, broken infrastructure, verifier
   defects and exhausted model budgets have distinct dispositions.

The broader design contemplated 256 base scouting requests per model at the first budget. The
executed $100 allocation below uses two repetitions and 192 total planned cells. Additional
budget-ladder, tool-assisted and reference-model runs are separate charged cells, not free retries.
Freeze their counts, total processed/generated tokens, GPU-hours, wall time, judge CPU/storage
limits and actual monetary ceiling before execution. The quota and inactive resource inventory were
checked above. The allocation and price estimate appear below. Four real BF16 Responses probes
passed on the actual deployment. The old four-hour/$0 Mac envelope does not apply to this scout.

## Test a shift in capability, not just successful bookkeeping

Use the measured difficulty range to select **fresh project instances**, grouped apart from scouting.
One project should require a substantial algorithm, implementation, tests and revision, with several
plausible unsuccessful approaches. Six unrelated easy questions do not create that structure.

The primary comparison is persistent model-directed search versus a strong scripted coordinator.
Both get the same tasks, model, tool affordances, information permissions, client churn, total
processed/generated-token ceilings and tool/elapsed-time budgets. The scripted baseline must
actually use accumulated candidates and negative results to allocate work; it must not be made
weak by discarding useful evidence. Include an equally funded uninterrupted solver and a targeted
memory/negative-result ablation as secondary comparisons.

Replace complete worker contexts at predetermined committed search boundaries. The replacement
receives only the process's admitted problem-specific state: candidate code, explicit public work
reports, checked public tests, unresolved hypotheses and next-work decisions. A persistent worker's
uninterrupted public working context and a replacement's reconstructed state must be operationally
different and measured. Restarting a stateless client that receives an identical prompt is only a
transport check. Shared model-server replacement is a separate intervention.

Raw model traffic, private reasoning, environment/security traces and MI remain researcher-only.
Explicit public work products and reviewed test-result derivatives cross the existing admission
policy. Raw compiler/test logs and discoverable forensic identifiers do not automatically become
memory. Do not turn authoring a public report into permission to copy private reasoning.

Report verified completion by difficulty and budget, the boundary shift relative to each baseline,
actual tokens/GPU-hours, repeated failed approaches, progress after turnover and all missingness.
Ceiling/floor and null results are valid observations, but trigger a stronger or better-targeted
next allocation rather than celebration of an uninformative comparison. Compute and context use
remain visible; a shared ceiling does not prove equal consumed compute.

Scouting is exploratory. Fix the confirmation population, analysis and count from its variability
and the smallest scientifically meaningful effect before examining treatment outcomes. Use fresh
instances, multiple stochastic macro-rollouts and task/contest-aware uncertainty. Do not treat
actions as independent institutions or report ordinary fixed-sample confidence guarantees after
adaptive stopping. The old 12 × 4 × 2 count and the finite plan's 128-entry maximum do not determine
sample size. A larger fixed study can use separately authorized plans with disjoint owners and
preallocated budgets whose total is fixed before execution. That does not establish a shared
cross-authorization account or permit refilling failed tasks or erasing missingness.

## Authorized $100 scout and launch envelope

The user rejected the $600 proposal and authorized **at most $100 and five hours** on
2026-09-06. `configs/atlas/nemotron-bf16-budget100-v1.json` supersedes the earlier launch
proposal. Its retained execution package is `runs/atlas-budget100-20260906/`; the earlier
384-trial/$600 inputs grant no authority to spend.

The official NVIDIA BF16 checkpoint is pinned at
`a9904d24bcc1d289a1950fa9d2b978c47cf903b9`. Its index declares 65,842,365,568 weight bytes.
Only its configuration and tokenizer were downloaded locally. No quantized or mock model is a
substitute for this campaign. The pinned official vLLM 0.27.1 AMD64 image uses the Responses API,
`nemotron_v3` reasoning parser, BF16, TP1, a 73,728-token context and four sequences per replica.
CUDA execution uses the stock runtime defaults; there is no speculative-decoding or quantization
substitution. Prefix caching and Responses server-side storage are disabled.

The reference example used Chat Completions, but the pinned
[Responses implementation](https://github.com/vllm-project/vllm/blob/v0.27.1/vllm/entrypoints/openai/responses/serving.py)
supports the configured reasoning parser and emits separate reasoning and final-output items.
The prepared route is `/v1/responses`, forwarded by a dedicated Vertex `rawPredict` endpoint.
The real preflight verified this exact configuration; there was no automatic protocol fallback.

| Wave | Distinct problems | Repetitions | Requests | Output allowance per request | Concurrent requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base scout | 64 | 2 | 128 | 8,192 | 16 |
| Matched ladder | 16 | 2 | 32 | 32,768 | 4 |
| Matched ladder | 16 | 2 | 32 | 65,536 | 4 |

The 16 ladder problems are fixed before outcomes: 2 easy, 6 medium and 8 hard. Seeds
20260906–20260907 are shared across matched cells. The 192 trials permit at most 4,194,304 output
tokens, including reasoning, and 1,572,864 input tokens. Four separate real protocol probes permit
another 32,768 output tokens. The official tokenizer and a separate direct tokenization check
retain every complete statement and agree on all 64 input lengths: 369–1,903 tokens including
instructions and the chat template. Input truncation is disabled by policy.

The deployment used four `a2-ultragpu-1g` serving replicas, each with one A100 80GB, in `us-central1`.
Google's checked [Iowa Vertex pricing](https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing)
is $5.7818474 per replica-hour, or $23.1273896 per fleet-hour. The approved maximum is **$100**,
including startup, preflight, registry, endpoint/network/storage charges and teardown. Conservatively
count the full fleet from the deployment request. New dispatch stops at hour 2, undeployment starts
by hour 3, and cleanup must finish by hour 3.5, or earlier when finished. Planned compute is at most
$80.95, retaining $19.05 for other charges and cleanup variance. Startup may consume at most
45 minutes. A separate local deadline process initiates cleanup even if the foreground controller
exits; it does not make this a provider-enforced billing cap. The whole task also stops within the
user's five-hour envelope. Do not exceed the financial/cleanup envelope or use the training allocation.

The actual original activation also cut off response capture at hour 2. A prospective operational
amendment proposed finishing only never-dispatched cells before the original hour-3 teardown,
after provisioning and credential repair reduced usable time. That completion launch was blocked
by automatic approval review. Cleanup instead completed at 20:09:46 UTC, about 1.602 hours after
the deployment request, with a conservative compute estimate of $37.05. Actual billing and
incidental charges remain to be reconciled. The report retains the amendment and every missing
outcome; it does not claim the original study completed or that its timing stayed unchanged.

The prospective schedule prioritizes all three budgets for the 16 matched problems. Within each
eight-problem cohort, every problem receives the first seed, then the second; budget order rotates
across problems and seeds. Each cohort contains 1 easy, 3 medium and 4 hard problems. There are at
most eight concurrent trials overall, four per long-budget wave, and two local judges. The remaining
48 problems receive their two base-budget trials only after both matched cohorts. This order is
fixed before any model outcome. It does not select follow-ups from successes or failures.

Primary descriptive outcomes are complete-test verified success by token allowance and the paired
per-problem difference between 32K and 8K, then between 64K and 32K. Preserve both stochastic seeds.
Report token use, finish reason, latency, judge verdict and coverage separately. Budget exhaustion
with no final program is distinct from a transport failure or an unattempted trial. Every one of
the 192 planned cells remains in the ledger. Time-censored or infrastructure-missing cells are not
silently scored as wrong or dropped; report coverage and worst/best-case bounds for the planned
comparisons, alongside descriptive results for complete pairs. Any resampling uncertainty must
preserve paired seeds, problem identity and contest dependence. This small public diagnostic is
exploratory; do not turn a partial time-limited sample into a fixed-sample confirmatory claim.

The narrow composition now imports the pinned data, judges C++ candidates and records native Atlas
requests, verifier results and trial outcomes through the existing generation executor. A private
activation artifact binds the exact registered run, provider, destination, configuration, concurrency
and expiry. Completed calls replay; unresolved calls stop new dispatch and are not retried.
Private reasoning and raw traffic remain privileged artifacts. Only final code enters the judge.
No generic scheduler, new authority database, live PPRL worker lifecycle or trainer was introduced.

Local verification passed nine archive/parser tests, lint/format, all 189 schema comparisons and
type checks over 197 production files. Independently authored C++ controls passed all 60 tests for
1983D and all 58 retained tests for hard problem 1983F, including a final concurrent judge check.
Earlier incorrect controls were rejected. Earlier mocked HTTP integration checks are engineering
evidence only; no further mock-model run was made after the user's instruction, and none supplies
a model capability result. The real GPU preflight and native registration subsequently succeeded.
After observed credential and SQLite receipt failures, the credential path was repaired and the
receipt writer was serialized before catalog lookup. Sixteen concurrent replays of eight retained
real outcomes passed without inference or judging. These repairs do not resolve the missing
scientific outcomes or constitute a complete recovery-runtime validation.

The local judge uses GCC 14.2 on Linux ARM64 under Docker Desktop. It preserves official inputs,
answers, checkers and declared limits, with per-process CPU limits, a bounded wall timeout and a
256 MiB regular-file limit. Its timing/accounting differs from the upstream x86 GoJudge setup;
results are an internal diagnostic, not an interchangeable published LiveCodeBench Pro score.
Inspect time-limit failures before attributing them to model capability. Retaining extra tests also
changes coverage relative to the archive's declared count. Full forensic/MI capture and independent
sandbox attestation remain outside this implementation.

## Running the prepared scout

Install the `coding` extra in the local environment. The preparation and dispatch entry points are:

- `scripts/prepare_atlas_coding.py`: authorized credential-file import of the pinned dataset.
- `scripts/run_atlas_coding.py prepare`: validate full prompts and freeze source/model/profile inputs.
- `scripts/prepare_atlas_vertex.py`: write exact model-upload, endpoint-create, deploy and cleanup JSON.
- `scripts/preflight_atlas_vertex.py`: after authorized deployment, run four real BF16 Responses probes.
- `scripts/run_atlas_coding.py register`: admit the three finite waves using that real preflight,
  the actual dedicated endpoint URL and the original deployment deadline.
- `scripts/run_atlas_coding.py run --wave WAVE`: execute the selected already registered wave.

The retained benchmark is `runs/atlas-lcbpro-bf16-20260906/`.
The original benchmark used `runs/atlas-budget100-20260906/inputs-original-benchmark.json`;
the credential-repaired continuation used `inputs-authorized.json` in that directory. Its later
`inputs-completion.json` freeze includes the receipt fix but produced no new model trial.
`vertex-launch/launch-plan.json` records the exact resources that were created and cleaned up.
`--wave paired` implements the fixed budget-limited schedule. The original $600 launch package
is superseded. The old preflight does not authorize calls against a replacement deployment.
Record deployment start before calling `deployModel`, use the provider-returned dedicated DNS,
and supply the same authorization reference throughout. Run directories contain privileged records
and are ignored by Git. Registration and run controls do not attest the operator or cloud hardware.

Actual GPU activation and spending use the user's explicit $100/five-hour authorization under
[AGENTS.md](../AGENTS.md); it does not authorize the earlier $600 envelope.
Preserve the broader PPRL, epsilon-charity and institution-level goals: this empirical worker
frontier informs subsequent persistence/search experiments and does not complete them.
