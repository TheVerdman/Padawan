# ADR 0002: Persistent student, clean item

- Status: Accepted
- Date: 2026-08-01

## Context

Resetting a developmental agent for each task discards the cognition being studied. Reusing exposed
items, however, confounds learning with memorization.

## Decision

Student state is an immutable, continuous lineage and may retain validated lessons, hypotheses, and
episode summaries. Item novelty is governed independently through leases, exposure records,
lineage visibility, contamination closure, and retirement.

## Consequences

Ordinary episodes advance canonical state instead of returning to a blank checkpoint. Every causal
claim must show clean item lineage; state history itself is not treated as contamination.
