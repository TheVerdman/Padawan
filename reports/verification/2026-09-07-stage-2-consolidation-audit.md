# Stage 2 consolidation audit

Date: 2026-09-07. Baseline: `9c1ac816f1154052d799ff76012012435886a866`.
Branch: `codex/pprl-information-boundary`. Status: implementation in progress.

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
The current navigation and architecture will be maintained in `docs/architecture.md` after this
audit. This table is the dated audit snapshot, not an additional ongoing status authority.

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

Locations in this table refer to the preservation checkpoint. Each correction will be recorded
with its validation and final disposition below. P1 blocks dependable ordinary validation;
P2 is a bounded maintenance or reproducibility defect. This is a scoped source audit, not a claim
that every possible correctness or security defect has been excluded.

| ID | Priority and source evidence | Effect and minimal correction | Required validation | Disposition |
| --- | --- | --- | --- | --- |
| C01 | P1: `.github/workflows/ci.yml` installs `.[dev]`; Make/README install `.[dev,gcs]`; `test_gcs_artifacts.py` imports GCS dependencies during collection. CI/Make/offline selectors differ. | Align full-development extras and ordinary offline selection, retain gated jobs, and validate a fresh installation. | Clean development install, collection, full offline suite, base installed CLI. | Fixed; clean-install checks below |
| C02 | P2: `test_coding_judge.py:12` silently skips all cases without YAML; coding entry points import optional libraries without a feature-specific error. | Require expected test dependencies; load optional coding dependencies at the selected feature boundary with an actionable installation error. | Missing-extra probes, coding tests, base-package CLI smoke. | Fixed; clean-install checks below |
| C03 | P2: `prepare_atlas_local.py:14`, `run_atlas_local.py:24`, `run_padawan_local.py:94`, and launch/validation scripts import shared implementation from other scripts. `atlas/local_host.py` is used by both Atlas and developmental work. | Extract reused identity, inventory, harness, control, and reporting code into package owners; preserve command and compatibility entry points. | Strict typing, import/help checks, request/digest equivalence, dispatch/guard/accounting/cleanup regressions. | Open |
| C04 | P2: Atlas, PPRL, checkpoint, Interaction Lab, and PostgreSQL tests import helpers and fixtures from test-case modules. | Move reused setup into shared subsystem helpers and fixture modules without changing assertions, clocks, or isolation. | Full collection, unchanged original test identities, affected tests and full offline suite. | Open |
| C05 | P2: `test_atlas_local_host.py` inspects the child identity before entering `try/finally`. | Guarantee cleanup if setup or process inspection fails while preserving production ownership checks. | Injected setup failure and real disposable-process cleanup cases. | Open |
| C06 | P2: README and architecture navigation repeat dated status; the complexity review still describes an earlier consolidation hold and retired pilot sequencing. | Put the current subsystem map and navigation in architecture/README; label dated proposals and link their successors without rewriting evidence. | Link checks, source-backed capability review, preserved historical report/configuration bytes. | Open |
| C07 | P2: `prepare_atlas_local.py:87` reads a specific ignored prior campaign input; local preparation also depends on pinned sibling runtime/model/capsule paths. | Document exact prerequisites and distinguish historical recipes from generally reproducible offline setup. Preserve the original frozen inputs and refusal behavior. | Entry-point help/import checks and source/configuration-drift regressions; no dataset fallback or campaign launch. | Open |

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

Further corrections and final validation are pending. No GitHub push, new model campaign,
external trainer, or cloud/GPU resource activation is included in this stage.
