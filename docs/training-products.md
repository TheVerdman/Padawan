# Internal training products and source rights

Padawan compiles one immutable internal bundle from a persisted evidence snapshot. It does not
publish a dataset, call a trainer, update model weights, or imply that internal research use is a
blanket copyright exception. Every product is a restricted content-addressed artifact and every
included or excluded candidate remains traceable to source-record digests and a compiler version.

## Products

One bundle always manifests all of these views, including zero-row views:

| Product | Admission boundary |
| --- | --- |
| evidence ledger | every source record and immutable artifact reference in the snapshot |
| normalized episodes | attempts, grades, interventions, revisions, transfers, rewards, and eligibility evidence |
| authored SFT | verifier-backed project-authored gold with explicit source/output SFT rights; never labeled as a student attempt |
| SFT | successful, deterministically graded target output with explicit SFT eligibility |
| preference | same-prompt target outputs with a strict verified score ordering and explicit eligibility |
| RLVR | governed task/verifier environment plus observed target output and reward evidence |
| negative/episode-process | public developmental derivation and outcome evidence; never private reasoning content |
| PPRL trajectory | complete immutable project state/event trajectory with an explicit process-learning decision |
| PPRL fork preference | paired continuations from one fork with a strict outcome ordering and declared preference lane |
| PPRL verifiable | complete project trajectory with genuinely verifiable outcome authority and explicit RLVR rights |
| continued pretraining | separately admitted, rights-confirmed, quality-gated source documents |
| sealed evaluation | clean sealed-anchor tasks for checkpoint comparison only |
| exclusions | candidate, product, lane, reason codes, details, and evidence lineage |

The evidence and normalized views retain baseline and teacher records with their research roles.
Baseline outputs, teacher interventions, and target prompts directly influenced by a teacher are
excluded from target-training products by default. A later policy cannot recover them by deleting
the exclusion evidence; it needs a separately versioned decision and, where applicable, a
teacherless successful replay.

Authored demonstrations use a separate append-only admission path and product. Each row cites its
curriculum item, canonical multi-turn transcript, target event, final answer, deterministic verifier
result, quality evidence, and rights digests. Downstream training may intentionally mix
`authored_sft` with `sft`, but provenance always preserves which examples were authored gold and
which were observed target successes.

Persistent-process products use their own source identities and never masquerade as developmental
episodes. The compiler requires a complete non-sealed rollout, verifies every state/event digest,
reconstructs the Amber lifecycle and each admitted request/decision, checks the cited outcome and
separate eligibility decision, enforces both distribution-source and execution-output rights, and
rechecks the distribution's unique-instance and per-instance rollout minimums. Quarantined,
revoked, and explicitly expired Amber lineages cannot contribute to a new product. Fork preference
requires a strict scalar ordering between comparable child outcomes. PPRL-verifiable additionally
requires `verifiable` authority; an empirical, adjudicated, or hybrid score remains ordinary PPRL
even when it is scalar.

Sealed, rotating-shadow, quarantined, contaminated, unreviewed, ineligible, and unregistered-
checkpoint material is structurally excluded from target-training views. A registered checkpoint
supplies both model and tokenizer identity. Episode output is never silently reclassified as
continued-pretraining text.

## Rights manifests

`SourceRights` replaces a free-text `license` label as the governing contract. The legacy database
column remains nullable for audit and downgrade compatibility, but the compiler does not use it as
authority. A rights manifest records:

- a versioned rights identity and basis (`project_authored`, `public_domain`, `open_license`,
  `contract_authorized`, `fair_use_reviewed`, `provider_terms`, or `unknown`);
- each expressly permitted use, separately for evidence retention, internal analysis, evaluation,
  continued pretraining, SFT, preference, RLVR, process training, and redistribution;
- internal-only, agreement-restricted, or redistributable scope;
- an optional license identifier, pinned terms URI and digest, attribution, and restrictions;
- confirmed, review-required, or prohibited status plus reviewer evidence.

The manifest is governance evidence, not an automated legal opinion. Project-authored Padawan task
generators are confirmed for internal research uses. A migrated third-party legacy label remains
review-required. Public-domain status, an open license, contractual authority, provider terms, or a
fair-use determination must be established for the particular source and intended use; “internal
research” alone does not set those facts. Federal material may also contain embedded third-party
content, so source-level review still matters.

OpenAI and Anthropic baseline/teacher outputs are retained as restricted evidence and excluded from
target-training products by role. This preserves the baseline and teacher record without treating
provider output ownership language as training authorization.

New target attempts also carry their own versioned output-rights declaration. A legacy attempt
without that declaration remains available in evidence but is reason-coded out of every target
training lane; the compiler never infers output authority from the source-task license or from a
provider name.

## Reproducible compilation

Run migrations, then compile and verify:

```text
padawan db migrate
padawan training compile
padawan training verify BUNDLE_ID
padawan training inspect BUNDLE_ID
```

Without `--as-of`, the compiler uses the latest source-record timestamp rather than wall-clock
time. Repeating the command without a source change therefore produces the same snapshot digest,
product digests, bundle ID, and manifest artifact. To reproduce an older bundle, pass its exact
timezone-aware timestamp:

```text
padawan training compile --as-of 2026-08-01T20:15:00Z \
  --eligibility-policy-id padawan.example.training \
  --eligibility-policy-version 1
```

The optional policy selector prevents evidence from another eligibility policy from admitting a
row. The manifest records the invocation, source snapshot, checkpoint identities, episode,
source-document, authored-demonstration, and process-rollout IDs, source artifact digests,
verifier/environment fingerprints, rights digests, eligibility policies, product counts, exclusion
counts, and content-addressed product artifacts.

Verification re-reads every restricted artifact, validates its SHA-256 identity, parses every row
against the versioned contract, checks canonical JSONL ordering and counts, recomputes the bundle
identity, and rebuilds the source snapshot digest from the database.

## Continued-pretraining sources

Continued-pretraining material has its own append-only admission path. Store a source-rights JSON
manifest and admit a content file:

```text
padawan training source admit \
  --content-file corpus.txt \
  --rights-manifest source-rights.json \
  --source-id internal.research.corpus \
  --source-version 1 \
  --title "Internal research corpus" \
  --quality-evidence quality-review-2026-08-01 \
  --status active \
  --admitted-by research-review-board
```

The content is stored as a restricted raw-data artifact. Source documents and lifecycle decisions
are append-only. Quarantined or retired documents cannot return to active status in place; a new
source version with a new rights manifest must supersede them. The compiler deduplicates active
versions by content digest and records the excluded duplicate.

## Current boundary

The compiler is usable with local or GCS artifact storage, but it is still an offline product
boundary. It does not operationalize Inkling serving, modify Magellan, call a citator, or supply an
external trainer. Lean and appellate student/adjudicator workflows now exist as software, but
RLVR-eligible evidence still requires a real external target run and, for appellate work,
digest-bound semantic adjudication. A checkpoint produced elsewhere can cite the bundle manifest
digest when it is registered, after which the existing sealed-suite promotion lifecycle governs it.
PPRL trajectory, fork-preference, and verifiable products are compiled by this same offline
boundary. Compilation does not claim that a process policy has been optimized or that one retained
historical rollout constitutes a training distribution.
