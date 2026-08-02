# R2.6 Verification: Closed-Record Appellate Briefing

**Date:** 2026-08-02
**Scope:** Padawan repository only
**Status:** Corpus/verifier software acceptance passed; live workflow not implemented

## Delivered boundary

`legal.appellate.fourth_circuit@1.0.0` installs one content-addressed court pack and three matched
synthetic transfer families. The package includes:

- official-source snapshots, non-quotable task renderings for the governing rules and statutes, and
  verbatim pinpoint passages from *Tolan*, *Scott*, and the published Fourth Circuit *Brown* opinion;
- a content-addressed synthetic joint appendix and canonical record facts per scenario;
- ordered brief, claim, paragraph, citation, compliance-certificate, semantic-adjudication,
  citator-assessment, verification-bundle, reward, and training-eligibility contracts;
- deterministic pack, task, rule, claim-map, record, authority, quotation, and leakage hard gates;
- separate proposition-support, applicability, adverse-authority, issue/preservation/remedy, and
  currentness results;
- a currentness capability of `unknown_without_citator` for the first pack; and
- CLI paths for corpus registration and offline submission verification.

The package has no `build_workflow`. It does not call Inkling, OpenAI, Anthropic, a citator, or any
other remote service. It does not modify Magellan or use live client records.

## Acceptance checks

The tests establish that:

- generation is deterministic across random seeds, produces matched sibling groups, and round-trips
  through the durable corpus schema;
- source, court-pack, record, scenario, submission, and verification digests reject detached or
  altered evidence;
- unknown record pages, noncanonical appendix locators, out-of-pack authorities, bad pinpoints,
  changed quotations, outside-record controlled facts, section reordering, false word counts, and
  sealed canaries fail their specific deterministic gates;
- an authority absent from the closed pack is described as unresolved, not automatically fabricated;
- substantive prose cannot bypass the claim map;
- semantic adjudication cannot cite a passage, rule, or fact that was not attached to the exact
  proposition and submission digest;
- adverse-authority treatment remains separate from bare authority resolution;
- a self-attested `good_law` value from an unadmitted source cannot override missing citator
  coverage;
- currentness remains missing rather than zero and uses the reward policy's explicit `omit` action;
- unadjudicated, baseline, teacher, shadow, sealed, hard-failed, and non-target submissions cannot
  enter target RLVR/SFT lanes; and
- a fully hard-gated, semantically adjudicated target curriculum brief can be admitted to RLVR,
  SFT, preference, and process products while still carrying unknown currentness.

## Verification commands and outcomes

```text
make check
```

- Ruff formatting and lint: passed across 181 files.
- JSON Schema drift: 58 schemas match their Pydantic contracts.
- Mypy strict mode: passed across 104 source files.
- Hermetic test suite: 156 passed, 6 environment-gated tests deselected.

```text
make test-lean
```

- Pinned Lean kernel regression: 1 passed, 160 deselected.
- The first invocation inside Codex's outer filesystem sandbox produced the expected
  `sandbox_apply: Operation not permitted` infrastructure result. Re-running with approval outside
  that outer sandbox allowed Lean's own network-denied macOS sandbox to apply, and the regression
  passed. No Lean code or dependency was changed.

PostgreSQL, live-provider, and GCS-live suites were not re-run because this slice changes no
migration, provider, or artifact-backend code. Existing environment-gated tests remain available.

## Rights and publication boundary

The generated records and task compilation are project-authored. Verbatim federal opinion passages
and task-scoped rule/statutory renderings retain official-source attribution and content digests.
The corpus rights manifest permits the declared internal research/training uses and forbids
redistribution. This governance record is not a legal opinion. No commercial citator or licensed
third-party treatise is included.

## Remaining gates

- Connect a real target runtime after the Inkling endpoint is operationalized in its own workstream.
- Select and govern a human or model-assisted semantic adjudicator; model assessments must use the
  typed, evidence-bound contract.
- Add a licensed or otherwise dependable citator source before awarding currentness reward or
  making any good-law assertion.
- Build the developmental episode workflow and run empirical transfer/retention studies.
- Expand to another court or posture only as a new declared pack/version; this implementation does
  not imply multi-jurisdiction validity.
