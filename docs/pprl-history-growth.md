# Bounded PPRL history-growth measurement

Status: protocol frozen before measurement; all 24 fixtures passed on 2026-09-06. This is foundation
milestone 3 under the user's same-checkout local engineering continuation. The preceding milestone
hold is superseded; the resulting pilot readiness review and all later execution gates remain.

## Question and boundary

Measure the current enrolled claim/observe/propose/commit path, the clear-history integrity walk,
fresh-process reopen/replacement, and retained storage as committed history grows. Use production
stores without changing their checks. This is SQLite on this Mac with trusted synthetic clients,
not model inference, native worker transport latency, PostgreSQL concurrency, a scientific task,
physical resource billing, or evidence for 10M-token operation.

`scripts/measure_pprl_history.py` creates a separate explicitly enrolled and funded one-task fixture
for each condition. Histories contain 0, 8, 32 or 64 committed pure state updates. Both a constant
plan and a plan growing by one 128-character entry per action are measured, each in three fresh
databases. Each fixture declares its entire history plus one measured action before enrollment;
it cannot enlarge a prior plan or refill an account. No outcome or training eligibility is recorded.

## Measurements and limits

- Build the declared history through authenticated broker requests and ordinary event commits.
  Measure one further whole action without SQL instrumentation. Report all three independent
  fixture observations and median/min/max; these are engineering repeats, not stochastic samples.
- At the original history length, warm the clear-history path once, then time five traversals in
  separate transactions. Count SQL statements in a separate sixth traversal. Do not treat these
  within-database repetitions as independent samples or report an unsupported tail percentile.
- After the measured action, exit the builder and start a fresh interpreter. Reopen retained stores,
  revoke the original capability, independently replay the state/events and resource journal, issue
  a new capability, claim the same rollout and reconstruct its observation. Compare exact canonical
  state and original task/grant identities. Release the untouched lease and revoke the probe worker.
  Parent elapsed time includes imports, process launch and exit; child phase times identify the
  narrower reconstruction and claim paths. OS filesystem cache is not cleared.
- Record SQLite/WAL and artifact file bytes, canonical state/event JSON bytes, observation bytes,
  source/interpreter/package/host identities, exact fixture plans, action counts, charges and holds.
  File size is logical retained storage, not allocated disk or a production storage forecast.
- Maximum: 24 fixtures, 648 committed actions, 48 sequential owned child processes, one active
  child, 180 seconds per child, 1,200 seconds overall, 768 MiB observed child RSS, 256 MiB total
  evidence and 2,000 files. Monitoring additionally permits at most 1,200 bounded `ps` subprocesses
  and two Git identity reads; each monitor is synchronously reaped. The owning parent samples
  children and storage at one-second intervals; a bound or nonzero exit
  stops the ladder without retry. Per-child CPU and file-size limits provide additional stops.
  Polling bounds are observed safeguards inside the trusted host, not OS attestation.

Raw databases, privileged receipts, source/runtime manifests, failed runs and logs stay in an
exclusive ignored `runs/` directory with private permissions. No plaintext credential is serialized.
The fixture admits only authored public synthetic state; no forensic source or reference hydrates
the synthetic client. Terminate and reap only owned children. Preserve evidence and financial holds
on failure. Rollback is to stop this measurement tool, preserving all production history and checks.

## Interpretation fixed before measurement

Separate storage growth from integrity traversal and end-to-end cost. A growing plan causes full
state snapshots and observation receipts to repeat prior content; a constant plan isolates more of
the history-check cost. Neither fixture includes large reviewed artifact graphs, model traffic,
recovery-source sets or multiple tasks sharing an account. Increased latency motivates a bounded
pilot cap or later measured optimization, not automatic pruning or an invented incremental cache.
The upper measured rung is not a guaranteed capacity or safety limit. Stop here before expanding
the foundation scope; discretionary consolidation follows the frozen repeated behavioral baseline.

## Results and interpretation

All 24 cases passed: **648 committed updates, 48 builder/restart children reaped, 551.691 seconds**
for the supervised ladder. It used 545 synchronously reaped monitoring subprocesses. Observed peak
child RSS was 150,274,048 bytes (143.3 MiB); final retained evidence is 267 files / 72,951,106 bytes.
The read-only audit independently checked SQLite integrity/foreign keys, exact row counts, every
synthetic state update, empty leases, revoked worker heads, conserved charges, zero holds and unchanged
task/grant/account identities on restart. No model token, model cost, artifact reference or process
memory reference was created. State/receipt SQL bytes are measured separately from the synthetic
zero-artifact-byte account; declared resource accounting is not physical database metering.
Each pure update declares ten synthetic action-seconds; that allowance is not its measured latency.

Each cell below is a median across three fresh fixtures; bracketed action values are their observed
min/max, **not confidence intervals**. Integrity timing first takes the median of each fixture's
five timed traversals, then the median of those three fixture medians.

| Prior events | State shape | Integrity walk (ms) | SQL statements per walk | Whole action (s), median [min, max] | Fresh child revoke/replay (s) | Fresh issue/claim/observe (s) |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | flat | 4.3 | 17 | 0.186 [0.185, 0.189] | 0.050 | 0.056 |
| 0 | growing | 4.3 | 17 | 0.189 [0.187, 0.191] | 0.051 | 0.056 |
| 8 | flat | 61.4 | 257 | 0.346 [0.340, 0.349] | 0.110 | 0.114 |
| 8 | growing | 61.9 | 257 | 0.341 [0.338, 0.341] | 0.110 | 0.114 |
| 32 | flat | 230.6 | 977 | 0.819 [0.814, 0.821] | 0.282 | 0.285 |
| 32 | growing | 230.9 | 977 | 0.828 [0.825, 0.831] | 0.282 | 0.284 |
| 64 | flat | 458.1 | 1,937 | 1.467 [1.459, 1.475] | 0.517 | 0.521 |
| 64 | growing | 457.9 | 1,937 | 1.488 [1.487, 1.499] | 0.518 | 0.522 |

The measured SQL statement count is exactly **30 × prior events + 17** in all 24 cases. It counts
SQLAlchemy cursor executions, including transaction setup; it is not a query-plan or physical-I/O
measurement. For flat state at 64 events, the median whole action splits into 0.522 s claim/observe,
0.885 s proposal/admission and 0.052 s commit/reconstruction. Rounding and separately computed
medians need not sum exactly. This agrees with the code's repeated historical checks before claim
and admission. The commit path stayed near 0.05 s in this fixture.

Building flat histories of 8, 32 and 64 updates took medians 1.991, 15.779 and 52.378 seconds.
**Inference:** a linear history walk repeated for each action produces superlinear cumulative build
cost here. This supports a scaling concern without establishing a long-horizon asymptote, production
throughput, service tail latency or a need to optimize before the small pilot.

| Prior events | Shape | All immutable state JSON at that history (bytes) | Current public state (bytes) | Restart observation after one probe action (bytes) | SQLite after probe/restart (MiB) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 0 | flat | 693 | 364 | 400 | 2.082 |
| 0 | growing | 693 | 364 | 531 | 2.082 |
| 8 | flat | 7,013 | 365 | 400 | 2.305 |
| 8 | growing | 11,729 | 1,413 | 1,579 | 2.312 |
| 32 | flat | 26,042 | 367 | 402 | 2.957 |
| 32 | growing | 95,210 | 4,559 | 4,725 | 3.086 |
| 64 | flat | 51,418 | 367 | 402 | 3.867 |
| 64 | growing | 323,898 | 8,751 | 8,917 | 4.395 |

The SQLite floor includes the repository's full disposable schema, private control receipts and
indexes. All connections were closed and WAL checkpointed before the reported final file sizes.
These are logical file lengths, not allocated disk. The growing condition repeats earlier plan
content in every full state version and observation, so its cumulative JSON grows faster than its
current payload. No compaction, pruning, result cache or incremental frontier was introduced.

Parent-observed restart spans were about 1.01, 1.02, 2.04 and 3.05 seconds across the four rungs.
They include interpreter imports, private reconstruction, claim/release, final audit and process
exit detection, including **up to one second of monitor polling delay**. Use the separate child phase
times for path costs; do not present the quantized parent values as precise launch-to-ready latency.
The restart occurs at H+1 events, after the measured whole action; integrity/action measurements use H.

Host/cache limitations: the machine was not reserved or cold-cache isolated. A read-only local model
inventory hash and brief offline sample preparation occurred during parts of the ladder. Their work
is separate from the fixture timing/caps; ordinary host activity can affect absolute times. No model
ran, and the exact SQL counts and retained-state identities do not depend on those timings. The
small observed ranges do not establish an isolated-hardware capacity or endurance result.

**Recommendation:** keep the proposed pilot at at most 12 model calls plus one terminal control
action per rollout, between the 8- and 32-event measured rungs. Add its actual model/derivative bytes
and 104-task shared-account topology in the bounded integration gate; this single-task pure-update
measurement does not validate that topology. There is no demonstrated need for an incremental
verification design to prepare this pilot. Longer histories and large source graphs still require
fresh measured gates; discretionary consolidation remains after the repeated behavioral baseline.

## Evidence and code map

Retained measurements: `runs/pprl-history-growth-20260906/`; independent audit and pilot preparation:
`runs/pprl-foundation-pilot-20260906/`. These are private engineering records, never hydration.
The measured source is based on production HEAD `92e346a38d18cb97ffbf4dd777db4d9439324fce`.
Measurement-script SHA-256 is
`c9473904be63bad23b6d340bccec06af573e2c1601598de65685905f32818ea9`.
Each case retains its exact fixture/task/grant identities, SQLite rows, raw timing arrays, process
exit/RSS records and storage inventory. The manifest pins every tracked Python file, the actual
interpreter and package versions; a byte-identical measurement source is retained with the run.

| Evidence kind | Current source and meaning |
| --- | --- |
| Code fact | `padawan/pprl/resources.py:277`, `ProcessResourceStore.assert_rollout_recoverable`, reconstructs every prior admission/reservation and its closing evidence |
| Code fact | `padawan/pprl/store.py:438`, `ProcessStore.claim_next`, invokes the full check after selecting a candidate; `resources.py:805` reconstructs the resource journal |
| Code fact | `padawan/pprl/store.py:541` and `:1270`, ordinary commits and replay, retain immutable full state/event lineage |
| Measured engineering fact | Raw case JSON/SQLite plus `scripts/summarize_pprl_history.py`, 24 successful bounded cases with independent retained-row checks |
| Documented boundary | `pprl-task-ownership-boundary.md` and `pprl-scripted-continuity-boundary.md`, private finite ownership and trusted scripted continuity, not scientific efficacy |
| Recommendation | Keep the first model pilot short and validate its additional source/account topology; no runtime optimization in this milestone |

Reproduce into a **new** ignored output directory using the current source-root convention:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_pprl_history.py --output runs/NEW-HISTORY-RUN
.venv/bin/python scripts/summarize_pprl_history.py runs/NEW-HISTORY-RUN
```

The parent requires permission to inspect its own PIDs for the memory watchdog. The initial smoke
attempt stopped because the default tool sandbox denied `/bin/ps`; no resource check was bypassed.
The next smoke exposed a fixture-only strict enum construction error before an effect. After using
the existing enum, four smoke cases passed; the final ladder uses the retained final source above.
All stopped attempts remain separately retained. No production runtime, schema or migration changed.
