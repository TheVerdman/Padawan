# Temporal grounding and duration calibration

Padawan treats time as typed harness evidence, not as a sentence asking a model to “be aware of
time.” The first slice combines runtime instrumentation, a canonical temporal frame, deterministic
matched scenarios, structured model decisions, and an explicitly authored SFT bootstrap product.
Duration calibration is part of this first slice rather than a later extension.

## What the model receives

Temporal tasks preserve the real multi-turn role sequence and add one developer message containing
a versioned `<padawan_temporal_context>` block. The frame distinguishes:

- the current event time from the harness observation time;
- the previous user and assistant event times and the derived exchange gap;
- recorded agent activity from unobserved wall-clock silence;
- fresh, stale, unknown, and immutable observations;
- queued, running, or waiting operations and their last progress evidence; and
- p50, p90, and p95 duration evidence, timeout probability, next safe poll, and timeout bounds.

The model returns a strict `TemporalDecision`: continuity mode, whether the gap should be
acknowledged, an always-false unsupported continuous-activity claim, typed actions, calibrated
completion forecasts, the next check time, and a user-visible response. The structured policy is
the control surface; prose cannot schedule a poll or establish that work occurred.

Ordinary provider adapters do not automatically attach real conversation timestamps, user activity,
or workspace observations. The current temporal domain sends only project-authored synthetic frames.
Exporting real-session temporal metadata to an external provider requires a future explicit,
reviewable opt-in boundary.

## Curriculum and evaluation design

`TemporalGroundingCorpusGenerator` produces matched siblings and counterfactuals for five
competencies:

| Family | Controlled distinction |
| --- | --- |
| gap continuity | seconds, hours, and long absences without pretending the agent worked between messages |
| activity honesty | status reports with no recorded background operation or activity interval |
| observation freshness | identical dialogue with fresh, unknown, or stale workspace evidence |
| duration calibration | active operations with versioned duration quantiles and a not-yet-due poll |
| ETA revision | healthy wait, due poll, and timed-out/no-progress inspection states |

Scenario time is deterministic and never sleeps. The policy verifier checks continuity, gap
acknowledgement, activity honesty, exact action selection, forecast/profile identity, forecast error
against an explicit tolerance, next-check timing, and forbidden false-activity language. It retains
continuous duration error even when a forecast is within the acceptance band, so calibration can be
measured rather than reduced to pass/fail.

## Duration evidence loop

`IdempotentGenerationExecutor` creates a durable `model_generation` operation before provider I/O
and transitions it to waiting, failed, or succeeded with the external-call record. The workload
class records provider and size/capability metadata, not prompt content. `OperationTelemetryStore`
then builds reproducible profiles from terminal spans by operation type, environment fingerprint,
workload class, and evidence cutoff.

The curriculum uses versioned synthetic profiles so model behavior can be trained and evaluated
without waiting in real time. Runtime profiles use real operation evidence. Keeping both on the
same `DurationProfile` contract is the harness/model co-design seam: the model learns the same shape
that production instrumentation can eventually supply.

## Authored SFT bootstrap

Gold temporal behavior is never stored as a successful student attempt. The
`TemporalAuthoredDemonstrationFactory` renders the exact live transcript, constructs the governed
decision, and runs the deterministic temporal verifier. `AuthoredDemonstrationRegistry` admits it
only when:

- the source is an active, training-visible curriculum item;
- source and output rights explicitly permit SFT;
- the cited verifier result is persisted, digest-matched, deterministic, verified, and scoped to
  the same scenario; and
- the target event is the canonical serialization of the final answer.

The training compiler emits these rows as `authored_sft`, separate from `sft`, which remains reserved
for independently successful, eligible target attempts. A trainer may intentionally combine the two
views, but the bundle manifest, evidence ledger, rights digests, and source record digests preserve
which behavior was authored and which behavior was observed.

Programmatic admission is deliberately two-stage so verifier evidence exists before gold is
admitted:

```python
built = TemporalAuthoredDemonstrationFactory().build(item)
await RewardEngine().record_verifier_result(session, built.verifier_result)
await AuthoredDemonstrationRegistry().admit(
    session,
    demonstration=built.demonstration,
)
```

After admission, the normal `padawan training compile` and `padawan training verify BUNDLE_ID`
commands materialize and verify the restricted product.

## Current boundary

The complete synthetic developmental workflow, deterministic verifier, authored bootstrap, exact
transcript preservation, model-generation spans, and duration-profile builder are implemented and
covered by unit, integration, and system tests. The slice does not claim that an existing model has
already internalized temporal behavior, does not attach real temporal frames to ordinary provider
traffic, and does not yet instrument every non-model tool operation. Those are empirical rollout and
instrumentation steps, not prompt changes.
