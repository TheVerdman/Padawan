# ADR 0005: Capture private reasoning only as sensitive observation

- Status: Accepted
- Date: 2026-08-01

## Context

Some runtimes expose reasoning channels and others do not. Treating hidden reasoning as required or
ground truth would invite fabrication and overclaiming.

## Decision

Runtime capabilities state availability explicitly. Real private traces are restricted raw
artifacts with validated token spans. They rank below observable evidence, require policy approval
for teacher access/export, and are never synthesized when unavailable.

## Consequences

Padawan can study real traces without coupling core operation to them. The current live algebra
teacher receives no private trace.
