# Fixed-problem compiler experiment

Record status: completed historical attempt with retained negative results and cleanup evidence.
The [local coding condition](atlas-local-endurance.md) is a separate subsequent configuration.

The user authorized compiler upgrades and a GPU launch on 7 September 2026. This attempt gives
each of the [eight locked problems](atlas-fixed-comparison.md) one compiler-assisted trajectory
with seed 20260916 and a cumulative allowance of 65,536 generated tokens. The exact statements,
dataset revisions and judge packages remain pinned. It adds no baseline rerun, second seed, 128K
arm, model replacement, parameter update or PPRL developmental episode.

Four problems have completed historical 64K baseline results at this seed. Report those four
matched pairs separately from the full eight-problem tool arm. The remaining four historical
baseline cells were unrun, so they have no baseline score. Tool access, tool instructions, public
continuation and the model-card-recommended tool parser/template setting form the new harness
condition. This is a small diagnostic of that condition; it does not isolate a parameter change,
establish an intrinsic reasoning improvement, or provide a repeated-rollout training distribution.

## Declared trajectory and execution boundary

The model receives the unchanged base task instructions plus the compiler instructions in the
[attempt configuration](../configs/atlas/nemotron-bf16-compiler-budget125-v1.json). It can choose
`compile_and_run` through the Responses function-call interface. A response without a function
call is its final submission. Compiler access is available, but the model may submit without
using it; actual use and actual compiler execution counts are reported separately.

| Limit | Value |
| --- | --- |
| Model turns per trajectory | At most 5 |
| Tool calls per trajectory | At most 4, including invalid calls |
| Generated tokens | 65,536 total across reasoning, tool arguments and final text |
| Complete public input | At most 32,768 tokens per turn; no truncation or summarization |
| Serialized public history | At most 512 KiB |
| Model/tool wall time | At most 40 minutes per trajectory, within the original cloud deadline |
| C++ source | At most 64 KiB per call |
| Standard input | One to four strings, at most 16 KiB combined |
| Compilation | C++17, 60 CPU seconds, 2 GiB RAM |
| Each execution | 5 CPU seconds, 1 GiB RAM, one CPU |
| Captured stdout/stderr | At most 64 KiB per stream; stop on overflow |
| Returned diagnostics/output | First 4 KiB per stream, with explicit truncation indicators |
| Local CPU concurrency | At most two compiler/judge operations combined |

The model chooses all tool inputs. Public examples, model-authored reference solvers and stress
tests are permitted. Hidden judge inputs/answers and researcher diagnoses are never tool inputs
or feedback. One final program is graded against the unchanged released judge package after the
trajectory ends. No hidden result is returned to the model.

Each tool call uses a new pinned GCC container and scratch directory. Only its source is mounted;
standard input arrives through stdin. Execution uses a read-only mount, no network, no credential
mount, a read-only root filesystem, dropped capabilities, a non-root UID, no new privileges, and
bounded processes, memory, CPU, output and wall time. The compiler receives no judge package or
testlib mount. Scratch storage is not shared between calls or trajectories. These are controls
inside the trusted local Docker/host/kernel perimeter, not an independently attested sandbox.

## Admission, information flow and retained evidence

The fixed native Atlas request owns an immutable `CodingToolTrajectory` with the exact initial
request, tool policy, activation, problem identity and original clock. Each model turn passes
the existing native `IdempotentGenerationExecutor` and Atlas activation gateway. Child IDs are
finite, sequential and rooted in that request. Their exact prepared HTTP bodies, native generation
envelopes and costs/token usage remain separate. A child requires completed predecessor records
and matching retained compiler intents and receipts. An unresolved model or compiler intent cannot
be automatically repeated, and a final submission cannot authorize another model turn.

The explicit projection copies only public assistant text, addressed function calls and the bounded
tool-feedback fields. It excludes reasoning items, reasoning summaries, provider extensions,
private receipt metadata and arbitrary source identifiers. Neither `previous_response_id` nor
response storage is enabled. All model-emitted private reasoning and raw traffic remain retained
as privileged evidence and are never fed into subsequent turns or shared process memory.

The tokenizer checks the complete public history before each new model call; the native usage is
checked afterward. The next turn receives only the remaining generated-token allowance. Tool
resources and elapsed time do not reset with a new turn. This is a trusted experimental controller,
not a PPRL worker assignment lifecycle or authenticated worker capability.

A terminal compiler trajectory uses its own private
`application/vnd.padawan.atlas-coding-tool-result+json` record and a native deterministic verifier
record. It retains the root request, actual terminal generation, cumulative usage and source
artifacts. It is not inserted into the legacy single-generation `AtlasTrialResult` table or silently
pooled with those results. The original per-call envelopes remain unchanged. Automatic generic
Atlas aggregation, process admission and training materialization of this new result type are not
implemented by this experiment.

## Deployment and validation

The checkpoint remains NVIDIA's official BF16 revision
`a9904d24bcc1d289a1950fa9d2b978c47cf903b9`, using the same pinned vLLM 0.27.1 image and four
A100 80GB serving replicas. Temperature remains 1.0 and top-p 0.95. The server adds
`--enable-auto-tool-choice --tool-call-parser qwen3_coder`, with
`chat_template_kwargs.force_nonempty_content=true` on the client. These are the tool integration
settings documented in the [NVIDIA model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16#tool-calling).
The existing BF16, float32 Mamba cache, context and sequence configuration remains pinned.

Before benchmark admission, the new deployment must pass four actual SCC protocol probes and a
two-model-call compiler/feedback preflight. The latter verifies real function-call dispatch,
compilation/execution, public feedback serialization and a final program checked on four inputs.
Those probes are operational validation and are excluded from the eight benchmark outcomes.
There is no substitute or mocked model in a live run.

Offline validation includes public/private projection and token-budget fixtures, real concurrent
SQLite admission stopped before HTTP, structural child-admission fixtures, and real CPU container
checks for successful/failed compilation, process/output deadlines and namespace isolation.
Structural records are explicitly labeled and provide no model capability evidence. Live runtime
and benchmark results are reported only after actual execution.

The prior three deployment-clock holds total $66.1041691176342. They include provisioning and are
not a reconciled invoice. A maximum 100-minute deployment at the rechecked published fleet rate
of $23.1273896/hour reserves $38.54564933333334, leaving the separate $20 incidental/cleanup reserve
inside the unchanged $125 ceiling. The total conservative allocation is $124.64981845096753.
The rate comes from [Google's A2 pricing](https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing?authuser=0),
checked 7 September 2026. These are planning bounds, not a cloud-native spending cutoff.

New trajectories stop admission at minute 40; undeployment begins by minute 82; capture ends at
minute 96; cleanup is bounded by minute 100. An independent guard retains the existing recoverable
pause policy and hard ownership/clock/stop checks. The launcher cleans up the owned model and
endpoint after completion or failure. The temporary registry image is independently removed and
verified afterward. Other projects, shared images, original results and cached problem data remain
outside this cleanup scope. No further automatic deployment is authorized by this configuration.

## Observed attempt, 7 September 2026

The paid attempt completed all eight trajectories and retained twenty benchmark model calls,
following six successful real preflight calls. No submission passed the frozen final parser and
judge. The four available historical baseline pairs remained 0/4 with and without the compiler
condition; the other four problems had no prior observation at this seed and allowance.

Four trajectories attempted the tool twelve times. Ten calls failed argument validation: nine
provided a scalar `stdin` value and one provided an empty array. Two calls reached the compiler;
one failed compilation and one compiled and executed successfully. Both belonged to `2061H1`,
which ultimately emitted no final program. The remaining four trajectories never called the tool.
Five trajectories exhausted the cumulative 65,536-token allowance.

Primary terminal outcomes were five compile errors, two missing final programs, and one wrong
answer. These are outcomes of the complete declared harness, not eight clean tests of algorithmic
correctness. A separate CPU sensitivity check removed the erroneous `python` fence label from
the otherwise unchanged C++ submission for `2107F2`; it then compiled but gave a wrong answer.
That check does not replace the original result. Parsed wire records establish the argument shapes
received by Padawan; original pre-parser XML was not retained, so the scalar values are not
attributed exclusively to the model or parser.

The run produced 446,178 benchmark output tokens across its twenty model calls. The observed
deployment-to-cleanup clock was about 47.65 minutes, giving an $18.37 conservative compute hold
and an $84.47 cumulative hold across all four attempts. Billing remains unreconciled. Independent
fresh cloud reads verified the owned endpoint and model absent, followed by a full registry listing
that verified the temporary image/tag absent and all thirty-four unrelated image versions and tags
unchanged.

Detailed private evidence is retained in `runs/atlas-compiler-20260907/`: `report.md`, the frozen
inputs and source snapshot, native SQLite/artifacts, `analysis/`, `tool-usage-audit/`, and
`final-cleanup-confirmation.json`. These files remain ignored by Git and outside process-memory
or training admission. The result supports further work on reliable tool arguments and terminal
submission behavior; it does not establish a compiler-assisted reasoning gain or a PPRL learning
result.

A final local sweep also found a stopped container left by the prelaunch expired-deadline control.
It was removed after confirming its run-owned scratch mount. The current CPU wrapper checks the
deadline before starting Docker and reaps its client before removing the container. This correction
was made after the paid run; its source snapshot and results are unchanged. Five real expired or
short-deadline controls, a normal compile/run, and thirty-seven focused local tests passed afterward,
with immediate and delayed checks finding no remaining owned containers. The private
`postrun-cleanup-validation.json` receipt identifies the corrected source.
