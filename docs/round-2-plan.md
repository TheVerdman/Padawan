# Padawan Round 2 Plan: Domain-General Offline Development

**Status:** In progress
**Baseline:** `main` at `6a16db8`
**Prepared:** 2026-08-01

## Implementation checkpoint — 2026-08-01

The first Round 2 slice is implemented and verified locally:

- research roles now distinguish target, baseline, teacher, verifier, and adjudicator; OpenAI in
  the student-shaped CLI slot is persisted as a baseline and cannot consolidate target memory;
- algebra runs through a version-aware domain registry, while Lean mathematics is installed as a
  corpus/verifier package without falsely claiming an autonomous teaching workflow;
- the GCS backend uses create-only generations, checksum and SHA-256 validation, immutable storage
  metadata, async worker offloading, restricted-data controls, and dry-run garbage collection;
- a credentialed concurrent round trip passed against the user-authorized GCS bucket and removed
  its isolated test object afterward;
- external dotenv use is explicit through the bootstrap-only `PADAWAN_ENV_FILE`; sibling paths and
  credential values are neither discovered nor committed;
- Lean 4.32.2 and Mathlib 4.32.2 are pinned and cached workspace-locally; generated theorem tasks,
  environment fingerprints, a command-injection policy, network-disabled/write-confined macOS
  execution, kernel diagnostics, and distinct verified/rejected/infrastructure outcomes are real;
- `VerifierResult`, hard gates, reward components, and training-lane eligibility contracts exist,
  including the invariant that a hard-gate failure cannot receive scalar utility.

After this first slice, durable reward persistence/recomputation, retention and interference
scheduling, checkpoint lifecycle/compiler work, a closed appellate court pack, and the full Lean
developmental workflow remained pending.

## R2.3 implementation checkpoint — 2026-08-01

The reward, study, and external-checkpoint substrate is now implemented:

- verifier results, reward policies, reward records, and training eligibility are durable,
  append-only evidence objects; every reward retains raw/normalized components and policy/input
  digests and can be deterministically recomputed;
- hard-gate evidence must resolve to persisted verifier results, missing observations stay explicit,
  and failed gates cannot receive utility or enter a training lane;
- immutable study manifests bind experiments to conditions, roles, frozen checkpoints, identical
  suite digests, environment fingerprints, assignment seeds, and optional propensities;
- study aggregation consumes the original paired blocks and reports missing, contaminated, and
  infrastructure attrition without imputation;
- delayed-retention and interference trials bind complete source episodes, immutable state
  snapshots, fresh shadow/sealed item groups, and—in interference trials—learning in another
  competency; due claims and expiry recovery coordinate trial and corpus-item leases;
- checkpoint, evaluation-suite, evaluation, promotion-policy, comparison, and decision records form
  a real external/offline lifecycle through candidate, evaluating, promoted/rejected, quarantine,
  and revocation states;
- checkpoint comparison is lexicographic and recomputable from the same frozen suite evidence;
  optional missing metrics remain missing, while hard gates, required metrics, and regression
  tolerances cannot be scalar-compensated;
- read-only study, reward, and checkpoint reports expose immutable inputs and live integrity checks;
  no code path calls memory consolidation a weight update or claims an internal trainer exists.

R2.3 does not contact a provider, mutate checkpoint weights, or access Magellan. The remaining major
tracks are the full Lean developmental workflow, Magellan Improvement, the closed appellate pack,
and the training-product compiler.

## R2.5 protocol checkpoint — 2026-08-01

The Padawan-side Magellan protocol and deterministic authority are implemented after a read-only
audit of the retuned worktree:

- the external repository inspector binds commit, complete tracked diff, bounded non-sensitive
  untracked source, dependency, and migration digests without persisting a local machine path;
- the handshake requires content-addressed source materialization, allowlisted runtime secrets,
  PostgreSQL, verified isolated reset/fork behavior, independent matched worlds, restricted
  network/external effects, Responses, tenant enforcement, durable idempotency, mutating-tool
  validators, and typed tool schemas;
- eight matched scenario families cover the existing Magellan behavior plus tenant, unavailable
  tool, invalid dependency, approval, and replay failures;
- typed world, plan, tool, observation, approval, failure, and matched-allocation records bind
  database, tenant, user, authorization, provenance, and replay identity;
- deterministic verification separates environment, authorization, trace, safety, and task results;
  the first four are non-compensable hard gates, while task/constraint/recovery/efficiency/cost
  remain a recomputable vector;
- training eligibility keeps baseline, sealed, unsafe, and infrastructure-failed traces
  evaluation-only and never reclassifies episode data as continued-pretraining data.

R2.5 is not accepted as a live integration. The audited Magellan tree still lacks an enclosing
world reset/fork boundary and durable idempotency, does not enforce every capability identity,
allows caller-created approval bypass, and uses Chat Completions in both OpenAI paths. No Magellan
code was modified and no Inkling pilot was claimed. A real upstream driver and pinned sandbox must
satisfy the handshake and matched-world acceptance test before the domain gains `build_workflow`.

## R2.7a compiler checkpoint — 2026-08-01

The internal training-product compiler and source-rights boundary are implemented:

- a versioned `SourceRights` manifest replaces the legacy free-text license label as training-use
  authority while preserving that old field for audit and migration compatibility;
- evidence, normalized episode, SFT, preference, RLVR, negative/process,
  continued-pretraining, sealed-evaluation, and exclusion artifacts compile as restricted,
  canonical JSONL from one deterministic source watermark;
- every row carries compiler version, source evidence references, record digests, and rights
  digests; manifests also bind checkpoints/tokenizers, verifier/environment fingerprints, policy
  versions, counts, and invocation;
- baseline and teacher records remain in evidence and normalized products but are reason-coded out
  of target-training products by default; teacher-influenced target attempts require a teacherless
  replay;
- sealed, shadow, quarantined, contaminated, rights-unreviewed, eligibility-missing, and
  unregistered-checkpoint candidates are structurally excluded;
- continued-pretraining sources have separate append-only admission and lifecycle decisions,
  content-digest deduplication, quality evidence, and rights gates;
- local and GCS backends share the same artifact protocol, and `training verify` rechecks every
  artifact, row contract, digest, count, bundle identity, and database snapshot.

R2.7a does not train a model or publish a dataset. The empirical release study still depends on a
real Inkling endpoint and an externally produced checkpoint.

## R2.6 software checkpoint — 2026-08-02

The first closed-record appellate package is implemented:

- one content-addressed Fourth Circuit pack pins declared rule dates, official source snapshots,
  a three-opinion closed authority corpus, coverage limits, and a governing-law cutoff;
- three deterministic transfer families generate matched synthetic joint appendices without live
  client material;
- typed submissions bind every substantive paragraph to claims and exact record, rule, and
  authority locators;
- pack/task/rule/claim-map/record/authority/quotation/leakage hard gates are lexicographic and
  cannot be compensated by semantic or stylistic reward;
- support, applicability, adverse-authority treatment, and issue/preservation/remedy coverage use
  a separate digest-bound semantic adjudication contract;
- the pack has no citator and therefore emits `unknown`, never a good-law claim, for currentness;
- target curriculum successes can be RLVR/SFT eligible after adjudication, while baseline, teacher,
  shadow, sealed, unadjudicated, and hard-failed briefs remain excluded as policy requires.

R2.6 is complete as a corpus/verifier software package, not as a live developmental workflow. It
does not run Inkling, call an adjudicator, research outside the closed pack, file a brief, or replace
professional legal judgment.

## R2.4/R2.6 developmental-workflow checkpoint — 2026-08-08

The two deferred domain workflows are now implemented as software:

- `DomainDevelopmentalWorkflowHandler` provides a second, 10-transition durable path for
  domain-owned student rendering, output decoding, and grading while preserving three-sibling
  leasing, symmetric state forks, exposure-before-I/O, validated teaching, matched transfer,
  branch isolation, memory governance, retirement, provenance, and episode commitment;
- Lean binds that path to the pinned `LeanVerifier`; a student emits only a tactic proof and kernel
  rejection, infrastructure failure, and verified proof remain distinct outcomes;
- appellate student output is a content-only draft whose identity, scenario, role, and time are
  system-bound; deterministic integrity gates run before any semantic model call;
- `AppellateAdjudicationService` executes a strict typed assessment, gives each claim only its
  attached passages/rules/record facts, persists the request/response idempotently, rejects
  unbound evidence, and cannot decide currentness;
- Pydantic schemas are normalized to the strict Structured Outputs subset before an OpenAI or
  OpenAI-compatible Responses request, without mutating the durable source schema;
- the corpus integrity policy now distinguishes fixed-answer siblings from agentic siblings, so
  the latter are checked for distinct task/verifier identity instead of being quarantined merely
  because every `expected_answer` is intentionally absent; and
- domain verifier evidence is carried into the final developmental episode for downstream audit
  and training-product decisions.

The first appellate pack still has no admitted citator. ADR 0011 specifies the licensed-API or
governed-import boundary; until that provider and its retention rights are confirmed, currentness
remains `unknown_without_citator`. No Inkling, teacher, adjudicator, citator, Magellan, or trainer
service was called to establish this software checkpoint. Live acceptance still depends on the
Inkling-Small endpoint and a governed study; Magellan work remains deferred.

## Round 2 thesis

Round 1 established a durable, evidence-governed developmental loop for symbolic algebra. Round 2
turns that loop into a domain-general research system and makes its near-term product boundary
explicit:

> Padawan is an offline developmental control plane, environment suite, evidence ledger, and
> training-data compiler for frozen model checkpoints.

Padawan does not yet perform continual or online parameter learning. A checkpoint remains fixed
during a study. Validated evidence can change versioned inference-time memory, but a weight change
occurs only when an external, offline training process produces a new checkpoint. Round 2 must not
describe memory retrieval or lesson consolidation as parameter learning.

The intended lifecycle is:

```text
checkpoint N (frozen)
  -> governed domain episodes
  -> immutable evidence and reward components
  -> eligible mid/post-training views
  -> external offline training
  -> checkpoint N+1
  -> matched held-out, transfer, retention, and regression evaluation
  -> promote, reject, quarantine, or revoke
```

This is **batched continual development**, not continual parameter learning.

## Starting point

Round 1 already provides:

- persistent student state and transactional treatment/control forks;
- governed corpus lineage, exposure, contamination, and retirement;
- immutable local content-addressed artifacts and chained provenance;
- real Responses-API-first OpenAI and OpenAI-compatible adapters;
- an Anthropic teacher adapter;
- durable, resumable orchestration and idempotent external calls;
- a complete deterministic symbolic-algebra workflow;
- paired immediate-transfer experiments and versioned lesson memory;
- explicit refusal of unsupported parameter updates.

The principal Round 1 constraints are equally explicit:

- live composition and the episode handler are coupled to algebra;
- OpenAI can be configured as a `student`, even though it is useful here as a baseline;
- there is no Lean environment;
- artifacts are local only, while the available cloud store is GCS rather than S3;
- delayed-retention and interference studies are represented but not scheduled;
- separate episode experiments are not aggregated into one powered study;
- rewards are not yet first-class, versioned evidence objects;
- there is no compiler for SFT, preference, RLVR, or mid-training eligibility views;
- there is no external-checkpoint import, comparison, and promotion lifecycle;
- Inkling serving is not yet operational, so no live target-student success may be claimed.

## Decisions locked for Round 2

### 1. Provider and research role are separate concepts

The runtime model must record both a provider and a research role.

| Research role | Round 2 use | State and training authority |
| --- | --- | --- |
| `target` | Inkling-Small or a future open-weight student | May inherit target memory and produce candidate training evidence |
| `baseline` | OpenAI initially; other strong models later | Diagnostic only; cannot update target state or enter target training views by default |
| `teacher` | OpenAI or Anthropic | May propose interventions; never overrides deterministic evidence |
| `verifier` | Lean, SymPy, rule checker, record resolver, tests, environment | Produces objective evidence within its declared scope |
| `adjudicator` | Human or explicitly governed model review | Resolves uncertainty; cannot silently convert `unknown` into verified truth |

The OpenAI adapter remains. The `openai student` concept does not. Existing OpenAI execution is
reclassified as a baseline or teacher run. Any backwards-compatible CLI path must display and store
that reclassification explicitly rather than silently mixing it with Inkling episodes.

OOD status comes from a versioned split or task-distribution declaration, not from a frontier model.
A matched OpenAI baseline helps interpret OOD results:

- target fails, baseline succeeds: evidence of a target-specific capability gap;
- both fail: evidence that the task, environment, or shared capability may be defective;
- target succeeds, baseline fails: useful differential evidence, not proof that the baseline is bad;
- either run is contaminated or unmatched: no comparative conclusion.

### 2. Domains own task semantics; the core owns research semantics

Add a `padawan.domains` umbrella with concrete domain packages:

```text
padawan/domains/
  contracts.py
  registry.py
  algebra/
  lean_math/
  legal/appellate/
  magellan_improvement/
```

The core contracts should include:

- `DomainSpec`: identity, version, competencies, permitted teacher modes, evidence hierarchy;
- `TaskManifest`: prompt inputs, corpus lineage, split, source rights, difficulty, freshness rules;
- `EnvironmentSnapshot`: immutable world state, dependencies, tool surface, and fingerprint;
- `DomainAttempt`: domain-neutral action, observation, output, and capability records;
- `VerifierResult`: verifier identity/version, scope, disposition, evidence, and uncertainty;
- `RewardRecord`: hard gates, component vector, policy version, and optional derived utility;
- `TransferPolicy`: sibling, cross-family, cross-context, retention, and interference rules;
- `TrainingEligibilityDecision`: permitted training lanes and specific exclusion reasons.

Replace `AlgebraWorkflowHandler` at the composition boundary with a domain-neutral durable workflow
that dispatches through a registered domain package. Algebra is migrated first and must retain its
existing deterministic behavior and tests. No core orchestration, artifact, experiment, or report
module may import an algebra implementation after that migration.

Domain packages own prompting, item generation or admission, environment execution, graders,
transfer construction, and domain reward components. They do not own student lifecycle,
provenance, exposure policy, state forks, artifact retention, checkpoint promotion, or global
training eligibility.

### 3. Evidence, reward, and promotion remain distinct

Padawan must retain raw verifier observations before computing rewards. It must never persist only
a scalar reward.

Each attempt receives:

1. **hard gates** — integrity, authorization, contamination, and domain-invalidating failures;
2. **task reward vector** — domain-specific measured outcomes;
3. **developmental reward vector** — revision gain, transfer, retention, and interference;
4. **derived meta-utility** — a versioned, recomputable value for curriculum or intervention policy;
5. **promotion decision** — a lexicographic governance result, not a threshold on utility alone.

A default meta-utility may take the following form:

```text
R_meta =
    held_out_capability_delta
  + lambda_transfer * unseen_transfer
  + lambda_retention * delayed_retention
  - lambda_regression * interference_and_regression
  - lambda_harm * harmful_interventions
  - lambda_contamination * contamination
  - lambda_cost * normalized_cost
```

The component values, normalizers, coefficients, reward-policy version, and missing-data decisions
must all be stored. `unknown` is not zero. A policy change recomputes derived utilities without
rewriting original evidence.

Promotion remains lexicographic:

```text
integrity and safety
  > valid non-contaminated evidence
  > non-regression
  > held-out capability and transfer
  > retention
  > efficiency
```

No capability or efficiency gain can compensate for a fabricated authority, invalid proof,
unauthorized tool action, leaked sealed item, or corrupted environment snapshot.

### 4. Round 2 produces a training product bundle, not one dataset

The compiler emits immutable, lineage-preserving products:

| Product | Contents | Default use |
| --- | --- | --- |
| evidence ledger | exact prompts, responses, traces, observations, verifier evidence | audit and recomputation |
| normalized episodes | typed attempts, interventions, revisions, branches, rewards | analysis and dataset construction |
| SFT view | validated target trajectories and teacherless successful replay | post-training |
| preference view | matched stronger/weaker outputs with attributable evidence | preference optimization |
| RLVR view | task/environment manifest, action interface, verifier package, reward vector | verifiable reinforcement training |
| negative/process view | failed proofs, invalid citations, unsafe plans, repairs, tool traces | process and error training |
| continued-pretraining view | rights-cleared, deduplicated source corpora rather than raw episodes | mid-training/continued pretraining |
| sealed evaluation suite | anchor, transfer, retention, interference, and regression tasks | checkpoint comparison only |

Episode data is not automatically mid-training data. Continued pretraining needs its own source,
rights basis, deduplication, contamination, and quality gates. Teacher outputs are never promoted merely
because a frontier model produced them. Baseline outputs are excluded from target training views by
default and require a separately governed distillation decision if ever used.

Every bundle includes:

- schema and compiler versions;
- source episode and artifact identities;
- checkpoint and tokenizer identities;
- domain/environment/verifier fingerprints;
- reward-policy and eligibility-policy versions;
- split and contamination declarations;
- rights-basis and source manifests;
- included and excluded record counts with reason codes;
- content digests and a reproducible compiler invocation.

Sealed anchors, evaluation-only records, invalid environments, unauthorized data, and unresolved
unresolved rights records must be structurally ineligible for training exports.

### 5. Checkpoint updates are external and offline

Add a checkpoint registry and an import/promotion workflow. A checkpoint record must include:

- model and tokenizer identity plus content digests;
- parent checkpoint or pretraining lineage;
- architecture and runtime compatibility metadata;
- source training-bundle manifest;
- external trainer configuration and output attestations when available;
- evaluation suite and environment fingerprints;
- regression, safety, transfer, retention, and efficiency outcomes;
- lifecycle status: candidate, evaluating, promoted, rejected, quarantined, or revoked.

Padawan compiles data and environments, imports an externally produced candidate, evaluates it, and
governs promotion. `UnsupportedParameterUpdateBackend` remains the correct behavior until a real
backend can isolate, train, evaluate, commit, and restore a weight update. Round 2 must not add a
no-op or nominal trainer to make this lifecycle appear complete.

## Storage and credentials

### GCS artifact backend

Implement GCS as the production object backend; do not implement S3 merely for symmetry. Preserve
the local backend for development, hermetic tests, and disconnected work.

The current concrete `LocalArtifactStore` dependencies must be replaced with the `ArtifactBackend`
abstraction throughout composition, external-call storage, teaching, episode storage, manifests,
and exports. Because GCS performs network I/O, the production interface must not block the async
worker loop.

The GCS implementation must:

- address objects by content digest under a deterministic prefix;
- upload with a create-only generation precondition;
- treat a precondition conflict as deduplication only after verifying existing size and digest;
- validate upload and download checksums;
- record bucket, object, generation, checksum, size, and backend identity in metadata;
- verify content again on read before returning it;
- preserve restricted/raw-data classification;
- support dry-run garbage collection derived from database references;
- test retries, concurrent duplicate writes, interrupted uploads, and corrupt metadata;
- have a credentialed integration test against an explicitly configured test bucket.

Required runtime configuration should be explicit, such as `PADAWAN_ARTIFACT_BACKEND=gcs`,
`PADAWAN_GCS_BUCKET`, `PADAWAN_GCS_PREFIX`, and optional project/credential settings. No bucket,
project, or local credential path is committed as a default. Production access should use Google
Application Default Credentials or a workload identity rather than a JSON key stored in this
repository.

### Provider credentials

The user-designated external env file has been checked for variable names only. It contains
`OPENAI_API_KEY` and `ANTHROPIC_API_KEY`. Their values must not be copied, printed, persisted in
Padawan artifacts, or committed.

Round 2 adds an explicit, runtime-only `PADAWAN_ENV_FILE` or equivalent CLI option with these rules:

- the repository never scans sibling projects automatically;
- no VECL-QB path is present in committed defaults or examples;
- process environment values take precedence over an explicitly selected env file;
- only Padawan's allowlisted settings are consumed;
- provider keys remain `SecretStr` values and enter only authorization headers;
- manifests record credential presence, never values or value hashes;
- tests inject disposable fake keys and assert that logs, errors, provenance, and artifacts redact
  them;
- production mode warns or refuses when a selected secret file is group/world-readable.

The VECL model variables are not silently aliased to Padawan model settings. Model IDs remain an
explicit Padawan run choice so a sibling repository cannot change experimental identity by
accident.

OpenAI calls continue to use `POST /v1/responses`. Structured output stays under `text.format`.
Chat Completions remains available only as an explicitly enabled compatibility protocol for a
non-OpenAI server and is recorded in the run evidence.

## Domain tracks

### Track A: Lean mathematics

Lean is the first new hard-verifiable domain and is limited to mathematics during Round 2.

Implement a pinned Lean 4/Lake environment with:

- a committed toolchain declaration and dependency lock;
- a sandboxed verifier process with network disabled, fixed resources, and hard timeouts;
- immutable task imports and theorem statements;
- exact Lean source, stdout/stderr, exit status, diagnostics, and toolchain digest as evidence;
- final kernel acceptance as the authoritative correctness gate;
- structured infrastructure-failure versus proof-failure classification;
- theorem-family-aware train, transfer, shadow, and sealed splits;
- proof completion, proof repair, and theorem-transfer task families;
- tests covering valid proofs, invalid proofs, timeouts, prohibited imports, and environment drift.

Intermediate tactic states may later provide process rewards, but they never override final kernel
acceptance. A teacher can propose a proof or explanation; only the pinned Lean environment verifies
it.

Round 2 does not use Lean to formalize appellate law or Magellan business behavior. Lean may later
support verified code, algorithms, protocols, or hardware, but extending it outside mathematics is
not a Round 2 dependency.

### Track B: Appellate briefing RLVR

Build the first legal environment around a closed, versioned federal appellate record. Begin with
public, historical, or synthetic matters rather than live client work. Support one declared court
pack at a time; do not imply multi-jurisdiction validity merely because the contracts are generic.

Each environment snapshot must pin:

```text
court and jurisdiction
  + brief type and procedural posture
  + FRAP and local-rule effective dates
  + closed record digest
  + authority corpus and coverage declaration
  + governing-law cutoff
```

The task output should include both the brief and a machine-readable claim/citation map. Verification
is layered:

**Hard or deterministic gates**

- required sections, order, word limits, and certificates;
- record citations resolve to the supplied record;
- quotations match the cited record or authority;
- authority identifiers resolve within a declared source;
- no invented docket, opinion, record page, or source;
- no outside-record factual assertion represented as record evidence;
- no sealed-data or evaluation-answer leakage.

**Evidence-backed semantic components**

- the authority supports the proposition for which it is cited;
- the authority is applicable to the facts, posture, standard of review, and requested remedy;
- controlling and persuasive authorities are distinguished;
- adverse authority is identified and treated;
- issues, preservation, counterarguments, and remedy are adequately covered;
- claims are calibrated to the available record and law.

**Governed judgment components**

- organization, clarity, concision, and persuasive coherence;
- pedagogical quality of revisions and transfer across changed records or postures.

Citation existence, quotation fidelity, proposition support, applicability, and currentness are
separate verifier results. Absence from an incomplete public opinion corpus is not proof that an
authority is fabricated. Negative treatment or `good law` status requires a dependable citator;
without one the result is `unknown` and cannot receive a verified-currentness reward.

Any model-assisted semantic verifier must cite the exact proposition, authority passage, record
facts, and rule it used. Uncertain or conflicting results enter adjudication. Deterministic rule,
record, and quotation failures outrank teacher or judge-model confidence.

### Track C: Magellan Improvement

Create `magellan_improvement` as an agentic domain environment for the future Inkling-Small agent
in the user-designated Magellan repository. The repository root must be runtime configuration, not
a committed machine-local path. Padawan must not import Magellan internals or assume that an
uncommitted worktree is a stable environment.

The integration contract should provide:

- Magellan code revision plus a dirty-state/environment digest;
- database, tenant, user, and authorization snapshot identity;
- scenario manifest and permitted tool/capability set;
- typed tool calls, observations, approval decisions, and provenance;
- deterministic postconditions and forbidden-state invariants;
- reset/fork behavior for matched target, control, and baseline runs;
- idempotency and replay identifiers;
- explicit environment and agent failure classes.

Initial scenario families should cover the behavior already represented in Magellan's tests:

- intake and plan construction;
- negotiated-rate and pricing constraints;
- outreach pause/resume behavior;
- regulated or human-approved actions;
- unknown tools, invalid dependencies, tenant isolation, and idempotency.

The reward hierarchy is environment integrity and authorization first, then task completion,
constraint satisfaction, plan/tool efficiency, recovery behavior, and cost. A successful-looking
answer cannot compensate for an unauthorized mutation or fabricated tool result.

Successful held-out trajectories can become SFT or RLVR candidates; matched failed/successful plans
can become preference candidates. All checkpoint changes remain offline. Until the Inkling endpoint
exists, Padawan may complete protocol and sandbox integration tests but must report the target live
pilot as externally blocked.

## Study and measurement expansion

Round 2 extends the experiment engine beyond one immediate-transfer block:

- aggregate separately persisted episode blocks into one versioned study;
- schedule delayed retention against unseen items and immutable state snapshots;
- schedule interference probes for previously validated competencies after new learning;
- support cross-family, cross-context, and cross-domain transfer declarations;
- pair target and baseline runs on the same environment snapshot without sharing cognition;
- preserve assignment seeds and policy propensities for later offline policy evaluation;
- report missingness, infrastructure failures, contamination, floor, ceiling, and attrition;
- distinguish revision, immediate transfer, delayed retention, interference, and checkpoint gain;
- never impute missing paired outcomes as failure or success.

Meta-reward is initially used for analysis, curriculum comparison, and governed policy selection.
Round 2 does not need to introduce an online reinforcement learner to make the reward contract real.

## External inputs required at their execution gates

These inputs are intentionally not committed and do not block the earlier architectural work:

- explicit OpenAI and Anthropic model IDs for live baseline and teacher runs;
- the external env-file selection used for those runs;
- GCS project, test bucket, prefix, and Application Default Credentials;
- the first appellate court/jurisdiction pack and any licensed citator or authority source;
- a frozen Magellan revision or a recorded worktree/environment digest plus sandbox configuration;
- the Inkling endpoint, checkpoint identity, and runtime capabilities when serving is ready.

Absence of an input blocks only its corresponding credentialed or live acceptance gate. It does not
authorize a mock result or a hardcoded substitute.

## Implementation sequence and acceptance gates

### R2.1 — Role and domain kernel

Deliver:

- provider/role separation and migrations;
- domain contracts and registry;
- generic durable episode workflow;
- algebra migrated as the first registered domain;
- domain-neutral reports, manifests, and Heirloom export fields.

Accept when:

- all existing algebra behavior and causal controls still pass;
- core modules no longer import algebra implementations;
- OpenAI baseline runs cannot mutate target state or enter target training views;
- role, provider, model, checkpoint, and environment identities survive persistence and replay.

### R2.2 — Artifact and secret portability

Deliver:

- async-capable artifact abstraction across all consumers;
- production GCS backend and local backend parity;
- explicit runtime env-file selection with secret redaction tests;
- backend migration metadata and operational documentation.

Accept when:

- local and configured GCS stores pass the same content-addressed contract suite;
- concurrent duplicate GCS writes converge on one verified object;
- a credentialed test-bucket round trip verifies generation and checksums;
- no credential value appears in git, command output, logs, failures, provenance, or artifacts.

### R2.3 — Reward, study, and checkpoint contracts — implemented

Deliver:

- first-class verifier and reward records;
- versioned meta-utility policies;
- delayed-retention and interference scheduler;
- multi-block study aggregation;
- external checkpoint registry, comparison, and promotion states.

Accept when:

- raw evidence can recompute every reward and decision;
- hard-gate failures cannot be scalar-compensated;
- missing values remain explicit;
- checkpoint N and N+1 can be compared on identical sealed suite manifests;
- no code path claims that memory consolidation changed model weights.

### R2.4 — Lean mathematics domain

Deliver and validate the pinned sandbox, deterministic verifier, governed corpus, transfer families,
and a bounded target/baseline/teacher study manifest.

Accept when valid and invalid proofs are decided by the Lean kernel, environment drift is detected,
and the domain can run without any algebra-specific workflow code.

### R2.5 — Magellan Improvement domain

Deliver the environment handshake, state reset/fork adapter, scenario manifests, tool trace capture,
deterministic invariants, and offline-training eligibility rules.

Accept when protocol tests run against a pinned sandboxed Magellan snapshot and matched runs cannot
share mutable world state. Live Inkling evidence remains a separate gate dependent on the serving
endpoint.

### R2.6 — Appellate briefing domain

Deliver one closed-record court pack, rule and record verifiers, authority resolution, claim/citation
mapping, semantic adjudication contracts, and transfer families.

Accept when fabricated citations, quote mismatches, record mismatches, and rule failures are caught
deterministically; citation-support uncertainty is preserved; and the system makes no unsupported
`good law` claim.

### R2.7 — Training compiler and release study

Deliver reproducible evidence, SFT, preference, RLVR, negative/process, continued-pretraining, and
sealed-evaluation manifests with eligibility reason codes.

Accept when:

- every exported row traces to immutable evidence and a compiler version;
- sealed and contaminated records are structurally excluded;
- a bundle rebuild is digest-stable from the same snapshot;
- baseline and teacher data are separately labeled and excluded by default;
- Round 2 reports distinguish software verification, live provider verification, and externally
  blocked Inkling evaluation.

## Round 2 completion criteria

Round 2 is complete as software when:

1. algebra runs through the domain-general kernel without regression;
2. GCS is a real, verified artifact backend;
3. OpenAI is represented as baseline/teacher rather than target student;
4. rewards, gates, meta-utility, and promotion are separately persisted and recomputable;
5. delayed retention, interference, and multi-block studies are operational;
6. Lean mathematics, one appellate court pack, and Magellan Improvement each have a real verifier or
   environment path with honest capability boundaries;
7. Padawan compiles reproducible, governed training and evaluation products;
8. externally produced checkpoints can be registered, compared, promoted, rejected, and rolled
   back at the registry level;
9. all local, property, migration, concurrency, and configured external integration gates pass;
10. unavailable services remain explicit blockers rather than mock successes.

The empirical Inkling Round 2 pilot is complete only after the real Inkling-Small serving endpoint
is available and a matched, uncontaminated study runs against it. Software completion does not
authorize claiming that pilot result early.

## Explicit non-goals

- online or per-episode parameter updates;
- claiming inference-time memory is continual learning;
- a native pretrained Padawan model;
- an S3 backend when GCS is the available production store;
- using OpenAI as the developmental target by default;
- using Lean outside mathematics during Round 2;
- live-client legal work, legal advice, or unsupported citator claims;
- silently modifying or depending on a dirty Magellan worktree;
- a trainer-shaped placeholder that cannot produce, evaluate, and restore real weights;
- any committed API key, local secret-file path, bucket identity, or private endpoint.

## Principal risks and controls

| Risk | Control |
| --- | --- |
| domain abstraction hides domain errors | migrate algebra first; require domain-specific verifier contract suites |
| scalar reward encourages reward hacking | retain raw vectors and gates; lexicographic promotion; versioned recomputation |
| teacher/baseline contamination | role-aware exposure and training eligibility; separate state and reports |
| legal citation false confidence | split resolution, quotation, support, applicability, and currentness; preserve `unknown` |
| public legal corpus incompleteness | record source coverage; never infer fabrication solely from absence |
| Lean environment drift | pin toolchain/dependencies and persist environment digest |
| Magellan world-state leakage | snapshot/reset/fork contract and post-run state verification |
| GCS race or corruption | generation preconditions, checksum verification, and live concurrency tests |
| secret leakage | explicit env-file opt-in, allowlist, `SecretStr`, redaction tests, no committed path |
| training/eval leakage | eligibility policy, family-aware splits, sealed structural exclusion, lineage audit |
| false continual-learning claim | frozen-checkpoint studies and explicit external checkpoint lineage |

## Primary references

- OpenAI, [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses#6-update-structured-outputs-definitions)
- Lean, [Language Reference](https://lean-lang.org/doc/reference/latest/)
- Lean, [Lake build system](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Lake/)
- U.S. Courts, [Federal Rules of Appellate Procedure](https://www.uscourts.gov/forms-rules/current-rules-practice-procedure/federal-rules-appellate-procedure)
- Fourth Circuit, [FRAP Rule 28](https://www.ca4.uscourts.gov/rules/rule28.html) and [FRAP Rule 32](https://www.ca4.uscourts.gov/rules/Rule32.html)
- GovInfo, [United States Courts Opinions](https://www.govinfo.gov/app/collection/USCOURTS/)
- Google Cloud, [Request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)
- Google Cloud, [Data validation and change detection](https://docs.cloud.google.com/storage/docs/data-validation)
