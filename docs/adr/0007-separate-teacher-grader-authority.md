# ADR 0007: Separate teacher and grader authority

- Status: Accepted
- Date: 2026-08-01

## Context

A teacher that creates tasks, decides correctness, explains errors, and selects updates cannot be
independently evaluated.

## Decision

Corpus, grader, teacher, comment validator, and consolidation backend are separate components. The
SymPy grader supplies authoritative evidence. A teacher must cite it, and an independent validator
checks citations, contradiction, localization, confidence, and leakage before use.

## Consequences

Rejected teacher calls remain evidence and may be retried without changing the grade. Additional
teachers can be compared by downstream transfer rather than self-evaluation.
