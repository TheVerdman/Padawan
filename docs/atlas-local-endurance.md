# Local coding endurance condition

Record status: historical local condition and launch recipe. Its exact prerequisites and source
binding rules are in [operations](operations.md); the original configuration and limits below are
preserved. The [Stage 1 report](../reports/verification/2026-09-07-stage-1-preservation-checkpoint.md)
records the retained local-run inventory.

The September 7 local condition runs the already cached affine 4-bit Nemotron checkpoint
through the pinned vLLM-Metal runtime on this Mac. The user authorized the compiler upgrades
and local launch after requesting an unattended local run without further cloud spending.
Configuration: `configs/atlas/nemotron-metal-endurance-v1.json`.

Compiler policy v2 accepts one string for `stdin` and uses an explicit terminal
`submit_solution(source)` call. Each episode has eight model turns, four compiler attempts,
six total tool calls, 32,768 cumulative generated tokens, and a 4,096-token finalization reserve.
Each model response is capped at 8,192 tokens. Ordinary prose receives bounded correction;
finalization offers only submission and disables thinking. Function selection remains automatic.
The complete public history is reconstructed from native receipts. Private reasoning and raw
traffic remain forensic artifacts and are excluded from continuations. Historical v1 contracts
retain their original fields and identities.

The finite queue samples eight publisher-easy, eight medium and eight hard LiveCodeBench Pro
problems from the existing frozen population, retaining the eight earlier regression anchors.
There are eight predetermined stochastic seeds per problem. Statements, constraints and full
judge archives remain unchanged. Difficulty labels are publisher categories, not calibrated
ability estimates. The six-hour wall clock includes startup and integration, and there is also
a 2,097,152 generated-token cap including integration. Unrun and interrupted episodes are
reported explicitly. This is a separate quantized inference and operating-endurance condition;
it does not establish a persistence advantage, learning effect, or BF16 comparison.

One loopback server remains loaded, with one active episode and a 32K context. The prepared
input limit is 24,576 tokens. Existing cached model files are rehashed, runtime revisions and
packages are checked, and code/configuration are frozen before launch. No download, cloud
resource, parameter update, or service deployment is part of this condition. CPU judging uses
the existing pinned Linux arm64 compiler image, without network access or worker credentials.

An independent watchdog owns cleanup of recorded process groups and run-labeled containers.
It detects supervisor/controller heartbeat loss, stalled progress, competing model processes,
swap growth, memory pressure, thermals, power loss, disk pressure and the hard deadline.
Startup permits at most 4 MiB of existing swap; any growth above that measured baseline
persisting for 60 seconds stops execution. This tolerates small pre-existing host use.
Descendants remain cleanup targets if their original group leader exits. `caffeinate` prevents
idle sleep while the supervisor is alive; the Mac still needs AC power and an open lid.
Retained output is capped at 2 GiB and transient scratch at 4 GiB. The controller stops new
episodes 15 minutes before the six-hour stop; the hard stop can interrupt a longer final episode.

Before launch, run the relevant offline contract tests plus
`scripts/validate_atlas_local.py --root <prepared-root>`. Its controls exercise real CPU
compilation/timeouts, watchdog cleanup after supervisor loss, and native registration/admission
with transport deliberately stopped before HTTP. Those controls provide no model evidence.
Then `scripts/launch_atlas_local.py start --root <prepared-root>` launches the detached supervisor.
The actual local model must first complete two compiler-error/repair/submission cycles and a
near-limit input probe through automatic function selection. Failure stops and cleans up.

After the first completed episode, one planned controller restart replays the committed result
and verifies that native model-call counts do not change. Unknown external effects are not
retried. The controller writes `report.md`, `summary.json` and `trials.csv` after each completed
episode, and the supervisor refreshes them after cleanup. `state.json`, `guard.json`,
`host.jsonl`, `resume-proof.json` and `cleanup.json` retain operational evidence.
