# ADR 0010: Parameter updates are optional governed backends

- Status: Accepted
- Date: 2026-08-01

## Context

Making an existing LoRA prototype load-bearing would couple core research to an unaudited trainer
and encourage commit-before-evaluate updates.

## Decision

`ConsolidationBackend` defines proposal and rollback boundaries. A parameter backend is admissible
only if it can create an isolated candidate, run target/held-out/interference evaluations, obtain a
governance decision, commit or reject, record provenance, and later restore. Padawan currently ships
only the operational memory backend and an explicit refusal backend for parameters.

## Consequences

No fake LoRA path appears as completeness. A future adapter can be added without changing the
developmental episode contract, but it must bring real candidate-state and rollback tests.
