# Corpus governance

Padawan separates item cleanliness from student continuity. A student may retain legitimate prior
state; every experimental item must be unseen within its governed lineage and exposure record.

## Lineage and pools

Every item belongs to a competency, template family, and matched instance group. Pool determines a
reserved visibility class:

| Pool | Visibility | Permitted use |
| --- | --- | --- |
| `curriculum` | training | teaching, revision, transfer, retrieval evidence |
| `rotating_shadow` | evaluation | governed nonsealed evaluation |
| `sealed_anchor` | sealed | dedicated sealed evaluation only |
| `quarantine` | transition state | no active use |

Template-family and instance-group composite constraints prevent one lineage from being registered
under conflicting visibility. Sealed items cannot enter teacher, revision, retrieval, or training
flows. Quarantine is not an intake pool.

## Generation and admission

`AlgebraCorpusGenerator` is deterministic from seed and covers distribution signs, variables on
both sides, factoring, rational simplification, invalid cancellation, excluded values, extraneous
roots, and omitted branches. It validates solution problems with SymPy before creating a record and
generates distinct answer keys within a sibling group.

The registry performs batch admission checks. Missing algebra answers, duplicate sibling answers,
or an answer string leaked into a sibling prompt quarantine the complete group and mark the family
contaminated. Every accepted item has a nonoptional difficulty, generator version, verifier,
source, versioned rights manifest, and contamination scope. The legacy license label is retained
only as optional migration evidence; a free-text label is not training authority.

## Leasing and recovery

Workers lease either one item or an entire sibling group. PostgreSQL uses `FOR UPDATE SKIP LOCKED`;
the local SQLite path uses a conditional `UPDATE … RETURNING`. Ownership, token, expiry, and attempt
count are persisted. Expired leases return to active inventory, and stale-worker recovery releases
its run lease. A retired or quarantined item is never selected.

Matched siblings are claimed in one transaction. If the requested count cannot be secured, the
caller receives no block. A lease for a student excludes every instance group with a prior exposure
for that student. Other students may still use a prompt-only group after a failed run. Request IDs,
lease tokens, and stable exposure IDs make retries explicit rather than silently duplicating work.

## Exposure, closure, and retirement

Exposure records prompt, answer, critique, repair, and metadata surfaces independently, together
with student/checkpoint/state lineage and episode. Padawan commits an exposure immediately before
handing a student prompt to the HTTP transport; connection failure is treated conservatively as
possible exposure. A terminal/review run releases its leases while the exposure keeps that group
ineligible for the same student. The default answer-bearing policy retires the exact item and
complete instance group. Prompt-only transfer exposure releases the item; the later cold critique
closes the full matched group in the same transaction. Template families are not retired by default,
preserving the competency for newly generated groups.

Exposure insertion and retirement share one database transaction. A lease that does not match the
owner/token cannot retire inventory. Sealed answer-bearing exposure is rejected. Contamination may
close an item, group, family, or broader generation lineage; the implemented `lineage` closure maps
to the reserved template family. A contaminated family cannot reenter shadow evaluation or
training.

Operators should run `padawan corpus validate` after imports or administrative transitions. It
checks for sealed/curriculum family overlap and active shadow items in contaminated families in
addition to database constraints.
