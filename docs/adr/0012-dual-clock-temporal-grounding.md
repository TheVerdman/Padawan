# ADR 0012: Temporal Grounding Uses Separate Operational and Scenario Clocks

**Status:** Accepted and implemented for the first temporal slice

## Context

A persistent agent needs to reason about elapsed conversational time, stale observations, active
work, expected duration, and due checks. The runtime also needs wall and monotonic time for leases,
timeouts, retries, and provider telemetry. Treating those as one clock makes deterministic temporal
training unsafe: advancing a synthetic conversation by ninety days could expire real worker leases
or trigger real I/O, while freezing operational time would make duration evidence meaningless.

A timestamp in a system prompt is insufficient. The model needs authoritative event relationships,
and the harness needs a testable contract for where those timestamps came from and what they may
control.

## Decision

Padawan has two explicit clock authorities:

- `SystemClock` supplies real UTC wall time and a monotonic duration source for operational state,
  external calls, operation spans, leases, retries, and measured profiles.
- `VirtualClock` supplies deterministic scenario time for generated conversations and tests. It may
  advance without sleeping, but it never controls operational leases, network retries, or provider
  I/O.

`TemporalFrame.current_event_at` anchors the current user event. `now_utc` is the harness observation
time, and elapsed conversational time is derived from the current event and the most recent prior
exchange rather than from prompt prose. Frames carry ordered events, recorded activity intervals,
freshness-qualified observations, and active operations with versioned duration profiles.

Real operation duration evidence is append-only. `operation_spans` indexes the current lifecycle,
`operation_span_events` retains every transition, and `duration_profiles` records deterministic
p50/p90/p95 and timeout estimates together with the exact source span IDs and evidence cutoff.

## Consequences

Synthetic weeks or months can pass instantly without changing real worker state. Runtime duration
calibration remains based on observed operations rather than invented scenario time. Tests must
inject a clock or explicit timestamp instead of patching global time. A component that converts a
virtual timestamp into a real sleep, retry, lease, or external side effect violates this boundary.

The initial runtime instrumentation covers model-generation operations. Other tool, indexing,
deployment, and sandbox operations can join the same span/profile contract as they acquire durable
lifecycle boundaries.
