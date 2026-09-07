# Fixed Nemotron coding comparison

Record status: historical comparison design. The [compiler attempt](atlas-compiler-experiment.md)
and [local condition](atlas-local-endurance.md) record the subsequent work. The original design
and frozen problem lock below remain unchanged.

The next comparison keeps the eight problems from the latest 64K/128K experiment. The user
selected this population on 6 September 2026. The checked-in
[problem lock](../configs/atlas/nemotron-64k128k-problems-v1.json) binds the complete statements,
released judge archive hashes, case counts and dataset revisions. Preparation, launch and
dispatch now verify that lock; launch also checks the physical archives before any cloud action.
Historical inputs, requests and results remain unchanged.

| Fixed order | Problem | Publisher title |
| ---: | --- | --- |
| 1 | 2061H1 | Kevin and Stones (Easy Version) |
| 2 | 2107F2 | Cycling (Hard Version) |
| 3 | 2097F | Lost Luggage |
| 4 | 2090B | Pushing Balls |
| 5 | 2023F | Hills and Pits |
| 6 | 2002D1 | DFS Checker (Easy Version) |
| 7 | 2035H | Peak Productivity Forces |
| 8 | 2071D2 | Infinite Sequence (Hard Version) |

The baseline definition remains the official BF16 checkpoint at revision
`a9904d24bcc1d289a1950fa9d2b978c47cf903b9`, the same full problem statements and instructions,
temperature 1.0, top-p 0.95, seeds 20260916 and 20260917, output allowances 65,536 and 131,072,
and the same pinned serving and judge configuration. That defines 32 baseline cells. These
allowances cap generated reasoning plus final output; they are not the input lengths. A repeated
seed is a recorded sampling parameter, not a promise of bitwise deterministic GPU output.

A compiler/test condition is implemented as a separately identified arm. It measures the model
with an execution tool. The baseline remains available for a matched comparison. The full design
with two seeds, both allowances and both tool conditions contains 64 trajectories; its feasibility
must be checked against the remaining authorized budget before activation. Do not substitute a
different problem population to improve a score or silently pool unlike conditions. Any smaller
design must be declared prospectively and retain every problem with balanced conditions.

The tool interface accepts C++17 source and explicit standard input, compiles in the
pinned local CPU environment, and returns bounded compiler diagnostics, exit status and output.
It can run public examples and model-authored small reference solvers or tests. The hidden judge
packages, expected answers, researcher analyses and other attempts' artifacts are inaccessible.
Use a separate scratch directory per trajectory, no network and no credential mounts. Declare
the tool-call, CPU, memory and output limits before the run. Charge every model turn against one
cumulative 64K or 128K generation allowance, rather than renewing the allowance after each tool
call. Grade one final submitted program after the trajectory ends. Compiler access, retries
within the declared tool trajectory and terminal judging must have separately retained receipts.

The user authorized implementation and one compiler-enabled GPU launch on 7 September 2026.
The [bounded compiler attempt](atlas-compiler-experiment.md) includes all eight problems, one seed
and a cumulative 65,536-token allowance. It retains the $125 cumulative ceiling. The new compiler
profile and trajectory admission are separate from legacy single-generation results. This design
document itself is not an activation record; the attempt has its own frozen authorization and
operational receipts.

The controller now tolerates sixty-second heartbeat age and 180-second cloud-proof age. A known
transient health problem pauses new admission and can recover for at most 180 seconds; the
original financial deadline can end the wait sooner. Recovery admits only previously untouched
requests. It does not resend a dispatched or unresolved model call. Explicit stops, dead guards,
ownership/configuration mismatches and invalid evidence remain terminal. Cloud authentication
rejections that persist after the existing refresh attempt, or access denials, remain terminal;
timeouts, temporary credential refresh failures, HTTP 429 and HTTP 5xx can receive bounded recovery.

The guard heartbeat is independent of synchronous cloud reads. The controller retains pause,
resume and first-terminal-stop evidence. The fixed request queue now refills available slots
without waiting for the previous eight-request block to finish, while preserving its order,
global concurrency and per-condition limits. Set `paired_block_barrier: true` only when a newly
declared experiment specifically requires the old block barrier. This is a trusted-host finite
experiment controller, not an independently enforced cloud spending cap or PPRL scheduler.

Local control-only checks cover recovery, refusal, deadlines, first-stop durability and identical
statement/judge enforcement. The compiler attempt on 7 September completed all eight trajectories
and retained all twenty benchmark model calls without a guard stop or unresolved model effect.
Transient-fault recovery remains covered by the separate local controls; this successful completion
does not imply that such a fault occurred during the paid run.

The benchmark authors discuss compilation, public-example checks and brute-force stress testing
as distinct benefits of tool access in
[LiveCodeBench Pro, section 11](https://arxiv.org/html/2506.11928v1#S11). Tool conditions must be
reported explicitly when comparing against a result obtained without tools.
