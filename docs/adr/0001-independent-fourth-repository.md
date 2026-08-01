# ADR 0001: Keep Padawan as an independent fourth repository

- Status: Accepted
- Date: 2026-08-01

## Context

Inkling owns checkpoint serving, Heirloom owns its execution/training and audit surfaces, and VECL-QB
contains experimental learning components. None should become the owner of the research institution.

## Decision

Padawan independently owns orchestration, state, corpora, evidence, experiments, memory, governance,
and reports. It integrates through adapters and file/protocol contracts. No sibling repository is a
runtime Python dependency.

## Consequences

Repository responsibilities stay clean and sibling churn cannot silently alter Padawan semantics.
Some useful concepts are reimplemented and cross-repository compatibility requires explicit tests.
