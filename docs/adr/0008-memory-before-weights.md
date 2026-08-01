# ADR 0008: Consolidate into memory before parameters

- Status: Accepted
- Date: 2026-08-01

## Context

Parameter updates are expensive, difficult to reverse, and can introduce interference. The system
must be useful with an inference-only student.

## Decision

Validated lessons enter versioned retrieval memory only after evidence and unseen transfer. Memory
writes have before/after snapshots, harm counters, invalidation, conflict review, and rollback.
Unimplemented weight updates fail with an explicit unsupported-capability error.

## Consequences

The developmental loop operates without a trainer and lessons remain auditable/reversible. Weight
adaptation can be added later only behind the same proposal/evaluation/governance discipline.
