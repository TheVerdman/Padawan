# ADR 0004: Deterministic evidence outranks model interpretation

- Status: Accepted
- Date: 2026-08-01

## Context

Teacher models can offer useful diagnoses but may confidently contradict executable truth.

## Decision

Authority is ordered: deterministic environment evidence, observable action/tool results, private
reasoning, public explanation, then teacher interpretation. Conflicting teacher output is rejected,
retried, or queued for review; evidence is never averaged with confidence.

## Consequences

Teacher creativity remains useful inside objective constraints. Domains without deterministic
graders need explicit uncertainty rather than simulated certainty.
