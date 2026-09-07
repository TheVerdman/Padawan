# Stage 2 consolidation audit

Date: 2026-09-07. Baseline: `9c1ac816f1154052d799ff76012012435886a866`.
Branch: `codex/pprl-information-boundary`. Status: complete as validated local consolidation.
GitHub publication and remote CI verification remain the separate Stage 3.

## Purpose and method

Padawan develops and evaluates smaller open-weight models and the processes in which they act.
This audit covers the complete research system, then corrects concrete installation, ownership,
fixture, and documentation problems. It preserves current commands, experiment settings,
serialized contracts, grading and admission authorities, and the preservation checkpoint.

The review follows composition roots, their stores and consumers, and executable boundary tests.
It distinguishes implementation from offline validation, retained live observations, and scientific
evidence. The [preservation report](2026-09-07-stage-1-preservation-checkpoint.md) establishes the
initial checks: 1,065 selected cases have passing results across the full run and a targeted host
permission rerun; lint, typing, and 189 generated schemas passed. Those are baseline results,
not validation of the changes made in this stage.

## Subsystem map at the preservation checkpoint

Source names below identify implementation owners; test names identify executable evidence.
Current navigation and ownership are maintained in [architecture](../../docs/architecture.md).
This table is the dated audit snapshot, not an additional ongoing status authority.

| Subsystem and purpose | Owner and input/output connection | Evidence and remaining gap |
| --- | --- | --- |
| Developmental learning loop | `config/composition.py`, `domains/developmental/workflow.py`, and the specialized `domains/algebra/workflow.py` turn governed tasks, parent states, target calls, and teacher interventions into durable episodes, matched transfers, and candidate lesson decisions. Experiments and compilation consume the evidence. | `tests/system/test_algebra_workflow.py`, `test_domain_developmental_workflow.py`, and `test_failure_storage.py` exercise completion, restart, teaching, and failures. An episode or memory change is not a parameter update. |
| Corpus, teaching, and lesson memory | `corpus/registry.py`, `teaching/service.py`, comment validation, `memory/lessons.py`, and `updates/backends.py` govern exposure, evidence-citing interventions, branch retrieval, lesson admission, and rollback. The loop consumes admitted lessons. | Corpus/teaching/memory tests and the system loop cover lineage and transfer decisions. Teacher-influenced attempts retain their distinct training exclusions. |
| Mathematics | The builtin registry installs algebra and Lean. SymPy and the pinned Lean kernel own correctness. `domains/graduate_algebra.py` is a separately composed local pilot authority with authored review inputs. | Algebra and Lean unit/system fixtures cover their contracts. Real Lean execution is gated; graduate-algebra pilot reviews are not kernel proofs or independent stochastic efficacy evidence. |
| Appellate briefing | `domains/legal/appellate` binds closed records, typed claims, deterministic citation/quotation/rule gates, and an evidence-bound semantic adjudicator into the shared workflow. Rewards and compilation consume qualified evidence. | `test_appellate_verifier.py` and the shared workflow test preserve hard-gate precedence. Citator-backed currentness remains unconfigured; a semantic score is not unconditional deterministic correctness. |
| Temporal grounding | `temporal`, `telemetry`, and `domains/temporal_grounding` supply authoritative frames, operation durations, freshness policy, and matched counterfactual tasks to the developmental path. | `test_temporal_grounding.py` and `test_temporal_grounding_workflow.py` validate clocks, actions, and workflow composition. Authored tasks do not establish learned temporal competence. |
| Magellan | `domains/magellan_improvement` supplies corpus, environment handshake, trace, authorization, and verifier contracts. The domain registry deliberately does not install an autonomous workflow. | `test_magellan_verifier.py` and environment tests exercise refusals and evidence interpretation. External sandbox/runtime acceptance remains blocked. |
| Capability Atlas | `atlas/registry.py`, `orchestration.py`, campaign/adapter code, and coding runners turn pinned suites and model/harness conditions into retained trial results and failure evidence. `atlas/studies.py` binds fixed coordinates into studies; `atlas/evidence.py` supports separately reviewed process derivatives. | Atlas registry, activation, orchestration, study-bridge, and process-evidence tests cover those connections. Eligibility flags do not materialize training data; institutional subjects and MI interchange remain incomplete. |
| PPRL and Amber | `pprl/composition.py`, coordinator/store, observations, identities, resources, recovery, tasks, and `governance/amber_store.py` bind distributions and reviewed actions to persistent state, accounting, replay, and addressed worker views. | Coordinator, admission, recovery, projection, and scripted-continuity fixtures exercise the control plane. Generic scheduling/replacement, live coordination, physical attestation, and scientific institutional results remain separate gaps. |
| Training compilation | `training/compiler.py`, `training/pprl.py`, source rights, and learning projections compile admitted snapshots into immutable, separately identified products and exclusions. Registered external checkpoints can cite these bundles. | `test_training_compiler.py` and PPRL compiler/projection tests cover deterministic products, provenance, private-data exclusions, and replication gates. `UnsupportedParameterUpdateBackend` explicitly refuses weight updates. |
| Studies and checkpoint evaluation | `experiments`, `studies/engine.py`, evaluation scheduling, and `checkpoints/registry.py` consume exact condition/result identities and sealed evidence for comparison, retention, promotion, and revocation. | Research-control, study, scheduler, checkpoint, and Atlas study-bridge tests validate lineage and comparability. No external training execution is implied by checkpoint registration. |
| Interaction Lab | `interaction/composition.py`, service/store, and web routes produce exploratory conversations and traces from explicit histories and target descriptors. They reuse evidence infrastructure without constructing developmental episodes. | Interaction service, composition, and web tests preserve branching and trace separation. Chat is not controlled benchmark evidence or automatic memory/training admission. |
| Shared infrastructure and adapters | Artifact classification/catalogs, provenance, SQL stores, `orchestration/external_calls.py`, and provider adapters retain exact requests/results and expose admitted outputs to the appropriate consumers. Heirloom is a governed export boundary. | Artifact, prepared-request, external-call, governance, and provider mock-HTTP tests cover integrity and information flow. Local enforcement and recorded authority do not attest a provider, host, or physical sandbox. |

The connected paths are concrete: task -> attempt -> verified episode -> memory decision;
Atlas trial -> fixed study result -> sealed checkpoint evidence; explicitly reviewed Atlas
derivative -> process evidence; admitted developmental/PPRL snapshot -> training product.
No automatic Atlas-to-training, exploratory-chat-to-experiment, or forensic-to-worker connection
is inferred. These absences are intentional use boundaries or unimplemented capabilities.

## Prioritized findings

Locations in this table refer to the preservation checkpoint. Each correction is recorded
with its validation and final disposition below. P1 blocks dependable ordinary validation;
P2 is a bounded maintenance or reproducibility defect. This is a scoped source audit, not a claim
that every possible correctness or security defect has been excluded.

| ID | Priority and source evidence | Effect and minimal correction | Required validation | Disposition |
| --- | --- | --- | --- | --- |
| C01 | P1: `.github/workflows/ci.yml` installs `.[dev]`; Make/README install `.[dev,gcs]`; `test_gcs_artifacts.py` imports GCS dependencies during collection. CI/Make/offline selectors differ. | Align full-development extras and ordinary offline selection, retain gated jobs, and validate a fresh installation. | Clean development install, collection, full offline suite, base installed CLI. | Fixed; clean-install checks below |
| C02 | P2: `test_coding_judge.py:12` silently skips all cases without YAML; coding entry points import optional libraries without a feature-specific error. | Require expected test dependencies; load optional coding dependencies at the selected feature boundary with an actionable installation error. | Missing-extra probes, coding tests, base-package CLI smoke. | Fixed; clean-install checks below |
| C03 | P2: `prepare_atlas_local.py:14`, `run_atlas_local.py:24`, `run_padawan_local.py:94`, and launch/validation scripts import shared implementation from other scripts. `atlas/local_host.py` is used by both Atlas and developmental work. | Extract reused identity, inventory, harness, control, and reporting code into package owners; preserve command and compatibility entry points. | Strict typing, import/help checks, request/digest equivalence, dispatch/guard/accounting/cleanup regressions. | Fixed; package ownership and relocation checks below |
| C04 | P2: Atlas, PPRL, checkpoint, Interaction Lab, and PostgreSQL tests import helpers and fixtures from test-case modules. | Move reused setup into shared subsystem helpers and fixture modules without changing assertions, clocks, or isolation. | Full collection, unchanged original test identities, affected tests and full offline suite. | Fixed; fixture preservation checks below |
| C05 | P2: `test_atlas_local_host.py` inspects the child identity before entering `try/finally`. | Guarantee cleanup if setup or process inspection fails while preserving production ownership checks. | Injected setup failure and real disposable-process cleanup cases. | Fixed; both failure paths pass |
| C06 | P2: README and architecture navigation repeat dated status; the complexity review still describes an earlier consolidation hold and retired pilot sequencing. | Put the current subsystem map and navigation in architecture/README; label dated proposals and link their successors without rewriting evidence. | Link checks, source-backed capability review, preserved historical report/configuration bytes. | Fixed; documentation and preservation checks below |
| C07 | P2: `prepare_atlas_local.py:87` reads a specific ignored prior campaign input; local preparation also depends on pinned sibling runtime/model/capsule paths. | Document exact prerequisites and distinguish historical recipes from generally reproducible offline setup. Preserve the original frozen inputs and refusal behavior. | Entry-point help/import checks and source/configuration-drift regressions; no dataset fallback or campaign launch. | Documented; historical inputs remain required |

## Deferred capabilities and protected distinctions

Trainer integration, a new distillation admission product, Atlas learning materialization,
institutional Atlas subjects, general PPRL scheduling/coordination, history-integrity optimization,
independent adjudication/currentness, and new scientific campaigns remain separate work. Module
size alone is not a finding. Similar-looking authority checks with different trust roots remain
independent. Compiler policy v1/v2, raw/private projections, model-call idempotency, unknown-effect
holds, grading, and experiment settings retain their existing meaning.

## Implementation and validation ledger

### Installation and optional dependencies

- Both fresh Python 3.12.14 environments installed the built wheel successfully: base only and
  `.[dev,gcs,coding]`. `pip check` passed in each. Public PyPI access was required for build/runtime
  dependencies after the sandbox's DNS restriction blocked the initial build.
- The base interpreter ran `scripts/validate_base_install.py` with `-I` from outside the checkout.
  Installed core composition imports and CLI help passed; coding/GCS packages were absent, and
  selecting their functionality produced the explicit extra-installation errors. CI now includes
  the same isolated base-package check.
- The 30 packaging, optional-dependency, coding-judge, GCS-fake-backend, and settings cases passed
  in both the existing environment and the fresh development environment. Fresh collection
  selected 1,068 of 1,107 cases, with the same 39 runtime-gated cases deselected and no missing-extra
  module skips. This is collection evidence; the final complete suite is recorded separately.
- Ruff passed for changed Python files. Strict mypy passed for 213 package modules.
- No dependency version constraint changed. Optional coding libraries now load when their feature
  is selected, so importing a command or asking for help does not require those extras.

### Shared implementation ownership

- Runtime inventory now belongs to the local adapter package; source-file inventory and local
  process controls belong to orchestration. Atlas owns common manifest construction, local
  registration/reporting, and the finite Vertex controller. Script commands and arguments remain;
  compatibility exports preserve the moved helpers where applicable. Package and script consumers
  no longer import implementation from sibling script modules.
- The source-body comparison checked 41 moved definitions after removing type annotations.
  Thirty-nine remained identical. The local report's two empty-list declarations were separated
  for typing; its behavior is covered by the native-ledger report test. The controller's only
  changed methods are construction and guard launch: it accepts the original script entry point
  explicitly, and its existing readiness hash now pins the canonical implementation module.
  All other controller methods retain their normalized bodies. The old readiness hash is refused;
  the new hash still launches the original guard command. No historical receipt was edited.
- The 107 targeted guard, request-preparation, compiler, problem-lock, developmental-adapter, and
  support cases passed. Added checks exercise stale source/configuration refusal before client
  construction, separate unresolved/unrun results, and controller identity/launch binding.
- All 13 retained campaign/preparation/validation commands returned successful `--help` using
  base dependencies without coding extras. An initial probe accidentally resolved the venv
  interpreter symlink to its base executable; using the venv executable path corrected that
  validation-command error. No application change was needed for it.
- All three real disposable process-ownership cases passed with the required host inspection
  permission. Strict mypy passed for 219 package modules; Ruff and all 189 schemas passed.
- The operations guide records the precise historical dataset/runtime/capsule/receipt dependencies
  and the source-pin behavior. These recipes remain evidence-bound; ordinary offline setup does
  not invent or recreate their unavailable private inputs.

### Shared fixtures and setup failure

- Thirteen subsystem modules under `tests/support` now own reused Atlas, research-control,
  Interaction Lab, process, and PostgreSQL setup. Test-case modules no longer import each other.
  Existing shared helpers remain in place; the fixed process clock and isolated database fixtures
  are unchanged. The PostgreSQL fixture retains its dedicated-test-database check and cleanup.
- The move preserves 74 helper function/class bodies and decorators plus 12 constant assignments.
  Of 666 original test definitions, only the two process cases that needed cleanup protection
  changed beyond imports. Their existing assertions remain. Collection retains all 1,075 original
  selected cases and adds exactly two process-fixture regression cases.
- The fixture's `Popen` session now has cleanup protection before identity inspection. Its fallback
  terminates only the disposable group it directly created and reaps its child without relying on
  `ps`. Production ownership and refusal rules are unchanged. All five real process cases pass,
  including injected identity-inspection failure before yield and injected failure after the leader
  exits while a descendant remains. These cases require host process inspection permission.
- Collection initially exposed two incorrect prefix substitutions in helper imports. They were
  corrected before execution; the final collection and source-body comparison pass.
- The complete selected suite passed in the fresh Python 3.12.14 development environment:
  **1,077 passed, 39 deselected, no skips**, in 236.59 seconds. This includes prepared dispatch,
  interrupted/unknown effects, guard pause/resume, accounting, deterministic compiler products,
  private-data admission/projection, and the moved fixture families. PostgreSQL tests were collected
  but not executed; their runtime validation remains separate. Ruff passed for all 140 test files.

### Documentation and preservation

- README now leads with improving smaller open-weight models, concise subsystem navigation, and
  reproducible developer setup. Architecture maps all twelve subsystem groups, their code owners,
  connections, executable evidence, and remaining gaps. It preserves the detailed action, evidence,
  checkpoint, and storage contracts and links historical reports separately.
- Six historical design/review records gained status prefaces without changes to their original
  bodies. The Round 2 change is limited to its status/navigation; the four-fabric chronology moved
  below its unchanged active contracts. All original README/architecture link targets remain in
  the new navigation. The local link check found no unresolved public paths or anchors; three
  retained links intentionally point to ignored private campaign evidence.
- SHA-256 comparison with the preservation checkpoint confirms **294 protected files unchanged**:
  configuration, schemas, migrations, retained live evidence, compiler implementation, public
  contract modules, repository instructions, and the checked policy/ADR files. Project metadata and
  all dependency constraints are semantically identical; the TOML difference only aligns whitespace.
  Historical v1/v2 records and prepared pins were not rewritten. Existing digest/refusal and
  compilation fixtures remain passing.

### Final validation and secret review

| Check | Final result |
| --- | --- |
| Fresh Python 3.12.14 development environment, complete ordinary selector | 1,077 passed; 39 deselected; no skips. Tests use the current checkout and disposable fixtures. |
| Formatting and lint | Ruff passes across 478 Python files. |
| Strict package typing | mypy passes for 219 source files. |
| Generated contracts | All 189 schemas match; serialized contract files remain unchanged. |
| Final wheel | Rebuilt from the consolidated source and installed into both fresh environments; `pip check` passes in each. |
| Isolated installed base package | Core composition imports, local artifact backend, CLI help, actionable missing-extra errors, and all extracted shared modules pass with `python -I` outside the checkout. Coding/GCS extras are absent. |
| Retained script commands | All 13 affected entry points pass `--help` with base dependencies; targeted request, accounting, source-drift, and control tests pass as recorded above. |
| Information and failure boundaries | The full suite includes private-record admission/projection exclusions, deterministic compilation, unresolved-call holds, interrupted dispatch, guard pause/resume, accounting, and process setup failure. |
| Historical preservation | Protected file hashes, original fixture bodies/constants, original test identities, historical document bodies, and retained navigation targets pass the scoped comparisons. |

Gitleaks 8.30.1 scanned the prospective tracked tree and all-ref Git history with complete redaction
and inline allow comments disabled. Each scan reported only the previously reviewed
`tokenizer_sha256` value in `configs/pprl/nemotron-local-pilot-v1.json:65`. Rehashing the actual
17,077,484-byte tokenizer confirms that it is a file checksum, not a credential. This is a triaged
false positive, not a raw zero-finding scanner result. No broad suppression was added.

An independent local comparison checked the two configured credential values and their base64
encodings against the prospective tracked files and all Git objects, including unreachable objects;
it found no matches and emitted no credential values. `.env` remains mode `0600`, ignored, untracked,
and absent from all-ref file history. No new runtime, credential, private-run, or live-evidence path
was added to tracking. Staged scans cover each implementation commit and the final documentation.
Validation logs and comparison inventories remain in ignored
`runs/stage2-consolidation-20260907/`.

## Disposition and Stage 3 handoff

C01–C06 are fixed and validated. C07 is documented: historical recipes still require their exact
private datasets, runtime checkouts, capsules, and prior receipts. No unresolved critical
correctness or information-boundary finding was identified by this scoped consolidation audit.
The deferred capabilities above remain gaps, not publication claims or newly authorized work.

The preservation commit remains intact. The local sequence is:

1. `65cd480` — record the whole-repository audit and subsystem ownership.
2. `ac66217` — align offline dependencies, selectors, optional errors, and installed-package checks.
3. `c2c960f` — give shared experiment support package ownership and preserve entry points.
4. `5d04405` — decouple shared fixtures and guarantee disposable-process cleanup.
5. The final documentation/audit commit — simplify navigation, label historical records, and close
   this ledger. Resolve the exact sequence with `git log --reverse --oneline 9c1ac81..HEAD`.

The accompanying handoff verifies a clean local tree and repeats secret/artifact checks after the
final commit. GitHub push and remote CI have not run in this stage. PostgreSQL, Docker, Lean, live GCS, real provider/model,
GPU, trainer, and scientific-campaign validation were not executed here; their separate runtime
requirements and gates remain. The retained PostgreSQL CI job is ready for its later remote run.
