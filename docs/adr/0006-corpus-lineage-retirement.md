# ADR 0006: Reserve corpus visibility by lineage and retire on feedback

- Status: Accepted
- Date: 2026-08-01

## Context

Application-only pool checks allow accidental leakage between training, shadow, and sealed uses.
Answer-bearing feedback also contaminates matched siblings.

## Decision

Template families and instance groups have reserved visibility enforced by database constraints.
Exposure plus retirement is transactional. Default answer-bearing feedback retires the exact item
and instance group; contaminated families are closed, while new groups may preserve the competency.

## Consequences

Inventory consumption is real and requires replacement generation. Sealed families cannot be
recycled into curriculum, and malformed sibling batches become quarantine evidence.
