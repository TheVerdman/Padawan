# ADR 0003: Use state forks for experimental controls

- Status: Accepted
- Date: 2026-08-01

## Context

A blank control is not comparable to a persistent student with accumulated cognition. Copying state
outside a transaction risks asymmetric or contaminated branches.

## Decision

Experiments fork one immutable parent into treatment and control in a single transaction. Both
children inherit identical content and diverge only by recorded condition. Post-fork memory is
branch-scoped, and both branches remain after canonical promotion.

## Consequences

Treatment effects are paired within a developmental state. Fork storage grows monotonically and
canonical selection must be conservative; treatment requires a strict transfer advantage.
