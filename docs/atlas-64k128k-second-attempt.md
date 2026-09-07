# Nemotron 64K versus 128K: second finite attempt

Record status: closed partial campaign. The [fixed comparison](atlas-fixed-comparison.md) and
[compiler attempt](atlas-compiler-experiment.md) are its historical successors.

Status: the repaired deployment on 6 September 2026 retained **eight scored trials out of 32**:
**0/4 accepted at 64K and 0/4 at 128K**. A guard-health refusal stopped new admissions;
all eight started requests drained, and 24 trials remained unrun. Four real serving probes passed,
including one with exactly 131,072 input tokens. All owned GPUs, endpoint/model records and the
temporary image were removed; 34 unrelated registry images were preserved. The
[partial-run report](../runs/atlas-64k128k-repaired-20260906/report.md) contains the audited table,
coverage bounds, independent final-program checks, operational diagnosis and cleanup evidence.
The comparison remains incomplete, including its predeclared first-seed readout.

A subsequent local repair separates guard heartbeats from synchronous cloud reads and retains
the first admission-stop reason. A delayed real GCP read reproduced the original heartbeat defect;
the repaired check passed without changing the 20-second heartbeat or 75-second cloud-access limits.
These post-run checks created no cloud resources and made no model calls. The paid run did not
retain its exact failing health predicate, so the reproduced mechanism is not a complete forensic
reconstruction of that event. No further paid deployment was launched after the repair.

The first deployment of this design stopped during preflight on 6 September 2026.
All four replicas initialized with the declared configuration. One real short probe completed
and passed all sixteen CPU checks; three raised database errors before committing call intents.
No benchmark trial was admitted. The controller immediately cleaned up the GPUs, endpoint and
model; fresh reads verified their absence. This was a harness failure, not a model score.

The repaired deployment uses
[nemotron-bf16-budget125-64k128k-v3.json](../configs/atlas/nemotron-bf16-budget125-64k128k-v3.json)
and retains new evidence under `runs/atlas-64k128k-repaired-20260906/`. Tasks, seeds, serving
parameters and all 32 scientific cells were unchanged; eight now have retained outcomes and
24 remain unattempted. The previous preflight
and its one actual result remain under `runs/atlas-64k128k-20260906/`; no unknown effect is retried.

The original contention validation covered the governed benchmark path but missed the concurrent
preflight path. A CPU reproduction using all four actual retained preflight requests reproduced
SQLite busy errors during artifact registration. Reserving the SQLite writer before catalog
reads allowed all four intents to commit. That validation stops immediately after commit and
before transport; it generates no model response. Completion, cancellation and provider-error
transactions now acquire the same writer reservation. Failed probes retain exception types,
SQLite error codes and stack locations without recording exception text or model traffic in
the public-facing log.

The repaired controller binds both previous deployment ledgers. Their **$49.63597459246453**
deployment-clock estimate is an allocation hold, not confirmed spending. The user clarified
that time before billable running should not count as consumption. Actual billing remains
unavailable, so uncertain amounts are retained for planning without calling them charges.
The repaired deployment permits 2.25 fleet-hours (**$52.0366266**) plus a separate $20 reserve,
for a cumulative held allocation of **$121.67260119246453**, below the $125 hard total.
Its fixed boundaries are 69 minutes for last dispatch, 111 minutes for starting undeployment,
126 minutes for result admission, and 135 minutes for completed cleanup, all measured from
before deployment submission. The earlier windows below describe the stopped deployment.
Four fresh runtime probes passed before benchmark admission. No further automatic
redeployment is authorized by this repaired configuration.

The user authorized operational fixes and a second GCP launch, then raised the cumulative hard
limit to **$125 total**, including the first attempt. This is an independent second experiment;
the first run's results, six unresolved attempts and 145 unattempted cells remain unchanged.

The stopped deployment's retained configuration is
[nemotron-bf16-budget125-64k128k-v2.json](../configs/atlas/nemotron-bf16-budget125-64k128k-v2.json).
That stopped deployment's execution evidence is retained under `runs/atlas-64k128k-20260906/`.

## Question and population

Does increasing the generated-token allowance from 65,536 to 131,072 increase verified coding
success for this worker under the same second-run serving configuration? The allowance covers
provider-exposed reasoning and the final answer together. Complete problem statements remain
below the separate 8,192-token input limit; input is never truncated.

Use the entire second eight-problem cohort declared before the first run. None of these problems
was reached by that run: 2061H1, 2107F2, 2097F, 2090B, 2023F, 2002D1, 2035H and 2071D2. The
publisher labels are one easy, three medium and four hard. Selection is independent of all model
outcomes. These public problems can have appeared in training; this is an exploratory diagnostic,
not an unseen-task or leaderboard estimate.

Each problem receives two new stochastic samples at each allowance: **32 planned trials**. Seeds
are 20260916 and 20260917, paired across allowances. Retain the original instructions, temperature
1.0, top-p 0.95, official BF16 checkpoint/revision, released judge data, pinned compiler image and
terminal evaluation. There are no worker tools, retries, hidden-test feedback, model substitutions,
training or cross-request memory. A tool-assisted experiment remains a separate future condition.

The prospective order completes the first seed across all eight problems before the second seed.
Within each seed, use the listed problem order and alternate which allowance is scheduled first.
Dispatch groups of four problems with both allowances, at most eight requests overall and four per
allowance. Drain the group before starting the next. Stop admitting groups at the fixed dispatch
deadline or the first infrastructure failure. Every planned cell remains in the result table.
Insufficient time for another group is a recorded operational stop, not permission to extend the
deployment or replace a sample.

The primary endpoint is complete acceptance by the retained judge. Compare 128K minus 64K within
each problem after averaging its two samples; the eight problems are the analysis units. Report
raw outcomes, paired differences, output exhaustion, final-code submission, tokens and wall time.
Any resampling uncertainty must resample whole problems and preserve both allowances and seeds;
eight clusters provide limited precision. The complete first-seed block is a predeclared secondary
descriptive readout. Missing and unresolved outcomes retain their classifications and worst/best
success bounds; do not treat them as incorrect programs or silently drop them from the planned
comparison. Do not pool this run with the first deployment, whose serving configuration differs.

## Serving and real preflight

Use four `a2-ultragpu-1g` serving replicas, each with one A100 80GB, the pinned official vLLM
0.27.1 image, BF16 weights, TP1 and explicit float32 Mamba state. Set the shared server context to
139,264 tokens and at most two sequences per replica. Both allowance conditions use that same
configuration. Responses storage and prefix caching remain disabled.

Before benchmark admission, retain four real Responses probes. Three use a short directed-graph
programming task, including requests with the 128K output ceiling. One uses approximately 131,072
input tokens and an 8,192-token output ceiling to exercise the enlarged context allocation. Its
padding is generated solely for runtime validation; it is never a benchmark task or score.

Each final probe program must compile and pass sixteen independent CPU checks: small directed
graphs evaluated through mutual reachability, explicit corner cases, and 200,000-vertex chain and
cycle cases. Probe failures stop benchmark admission. All started probes drain before the client
and database close. Passing this preflight establishes observed configuration, protocol, capacity
and these program checks; it does not establish BF16 equivalence to another runtime or validate
every possible 128K generation trajectory. The benchmark retains any actual long-generation failure.
Registration binds the preflight to the complete frozen serving configuration; the first run's
73,728-token deployment receipt cannot authorize the enlarged-context condition.

## Operational changes

Credentials now use the expiry returned by gcloud's configuration helper. A model request requires
enough remaining validity for its full HTTP timeout plus a margin. Cached or unknown expiry is
never assigned an assumed fresh hour. Only control-plane requests can repeat after an explicit
401 authentication rejection; model calls remain single-attempt effects.

The dispatch-status writer acquires the database writer lock before its catalog read. Completed
native results can be read after activation expiry without admitting another effect. The finite
dispatcher stops admission on worker/reporting failures and drains all admitted coroutines before
closing shared clients or stores. Parent cancellation also drains started work. An OS process kill
can still leave an unknown model effect; it does not authorize resending that request.

An independent local guard proves current cloud access before `deployModel`, maintains a fresh
heartbeat and checks cloud access periodically. Authentication or network errors are retained and
retried rather than terminating the guard. The foreground controller refuses stale guard health,
publishes its own heartbeat, and requests cleanup in its finalization path. Controller loss, an
unready deployment after 45 minutes, ten idle minutes after readiness, or the fixed teardown time
causes the guard to clean up the exact owned endpoint/model. File locks and atomic writes protect
shared control state. Deadlines are checked against the frozen configuration on every state read.

This remains a trusted-host experiment controller. It does not implement PPRL's general replacement
runtime, attest model or host isolation, or provide a cloud-native spending cutoff. Independent
fresh reads must verify actual cloud cleanup; budget alerts are not treated as a hard enforcement
mechanism. Provider failures can delay deletion, so the plan reserves substantial time and money
before the user's ceiling.

## Historical budget and windows for the stopped preflight deployment

The first attempt's retained conservative compute estimate is **$37.04536036372267**, including
provisioning through verified cleanup. A fresh read found no deployed endpoint models or running
custom jobs. Billing was enabled, but billing-export discovery returned HTTP 403; no actual invoice
total is claimed and no assumed billing credit is subtracted.

At the retained published four-replica rate of **$23.1273896/hour**, this attempt permits at most
2.75 conservative deployment hours, or **$63.6003214**. Together with the first estimate and a
separate **$20** allowance for incidental charges and cleanup variance, the planned allocation is
**$120.64568176372268**, below the user's $125 hard limit. The sum of both conservative deployment
durations is 4.352 hours, below five hours. The first run's expired wall-clock authorization is
retained historically; the user's new launch instruction authorizes this separate evening attempt.

All windows start before the deployment request:

| Boundary | Time after deployment clock starts |
| --- | ---: |
| Provisioning limit | 45 minutes |
| Last new benchmark dispatch | 93 minutes |
| Begin GPU undeployment | 135 minutes |
| Last result/judge admission | 150 minutes |
| Verify completed cloud cleanup | 165 minutes |

The 40-minute model timeout fits completely between the last dispatch and GPU teardown. Its
additional 15-minute judge allowance fits before result admission closes. Cleanup begins earlier
when the finite work finishes or an operational stop requires it. No deadline extension is planned.
The twenty-dollar reserve is an allocation against unmeasured charges, not a verified invoice cap.

Only this attempt's endpoint, model record and temporary registry copy are cleanup targets. Existing
shared registry images, the cached dataset, prior native records and other projects remain intact.
Raw model traffic, private reasoning and operational evidence remain researcher-only; only final
programs reach the isolated CPU judge. This run provides no training or scientific PPRL claim.

## Validation before deployment

The retained validation must cover near-expiry/missing-expiry rejection, actual GCP credential
refresh and authentication recovery, concurrent native receipt writes, a receipt failure while
other work is running, cancellation/drain, deadline refusal, budget over-allocation refusal, and
independent guard cleanup after loss of an owned foreground process. Native replay uses actual
retained BF16 results on a disposable database copy and admits no new model or judge call.
The real GCP guard check uses an empty owned endpoint and allocates no GPUs.
