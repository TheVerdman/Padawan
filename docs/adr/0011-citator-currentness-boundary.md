# ADR 0011: Citator Currentness Is an Independent Evidence Authority

**Status:** Accepted design; provider integration pending

## Context

Citation resolution, quotation fidelity, proposition support, applicability, and authority
currentness answer different questions. A language model can assess support from admitted text, but
it cannot establish that an authority remains good law by asserting that fact. Public citation
graphs are useful discovery and identity aids, but their collection coverage is not itself a
good-law determination.

Commercial systems expose the relevant treatment product: [Shepard's](https://www.lexisnexis.com/en-us/products/lexis/shepards.page),
[KeyCite](https://legal.thomsonreuters.com/en/products/westlaw/keycite), and Bloomberg Law's
[BCITE description](https://pro.bloomberglaw.com/about/our-approach-to-ai/). Whether any account
includes licensed machine access, retention, or downstream research use is a contract question;
the public product pages do not establish API entitlement. CourtListener's
[citation lookup](https://www.courtlistener.com/c/) is collection-limited and therefore remains a
supplemental resolver, not Padawan's good-law oracle.

## Decision

Padawan will add a provider-neutral citator boundary, separate from student, teacher, semantic
adjudicator, and reward code. A court-pack version may admit a citator only when its source snapshot
and source ID are included in the content-addressed pack and its terms permit the intended automated
access and evidence retention.

One lookup request binds:

- court-pack ID and digest;
- authority ID, canonical citation, docket, court, decision date, and admitted authority digest;
- the proposition or passage for which currentness matters;
- governing-law cutoff, requested observation time, and freshness policy; and
- a deterministic request/idempotency ID.

One normalized result retains:

- provider/source ID, query identity, retrieval time, provider as-of time, and coverage statement;
- provider treatment signal plus a conservative normalized status of `good_law`,
  `negative_treatment`, or `unknown`;
- cited treatment decisions and point-of-law scope when the licensed result supplies them;
- a digest of the raw result and a restricted artifact reference when retention is permitted;
- ingestion method (`licensed_api` or `governed_import`), operator/reviewer identity for imports,
  and validation errors; and
- the exact authority and court-pack digests to prevent replay against changed source text.

The verifier accepts currentness only when every authority cited by the brief has exactly one
assessment from a source admitted by that pack, every identity/digest matches, the result is fresh
under the run policy and no older than the governing-law cutoff, and retained evidence satisfies
the provider's license policy. Negative treatment is a lexicographic rejection. Caution,
ambiguity, incomplete coverage, stale results, a missing authority, or a result that cannot be
audited remains `unknown`. No scalar reward can compensate for a failed or missing currentness
gate, and neither the semantic adjudicator nor the teacher may self-attest good-law status.

## Operational routes

The preferred route is a licensed, documented provider API when the user's subscription explicitly
permits automated querying and internal evidence retention. The fallback is a governed human import
of an authorized report/export, stored as a restricted artifact and reviewed against the normalized
contract. Padawan will not scrape a commercial UI or infer API rights from possession of ordinary
interactive credentials.

Until one route is configured, the current Fourth Circuit pack keeps
`dependable_citator_source_ids=()` and currentness remains `unknown_without_citator`.

## Consequences

Adding a provider requires a new court-pack version and a provider-specific adapter, but does not
change the appellate student or semantic-adjudicator contracts. Raw citator material stays out of
target-training products by default; normalized currentness evidence may gate eligibility and
participate in versioned meta-utility only after the independent hard gate passes.
