# Stage 1 preservation checkpoint

Date: 2026-09-07. Branch: `codex/pprl-information-boundary`.
Parent: `92e04f725bac884a5b8ec511d0681898903d7524`.

This checkpoint preserves the accumulated Atlas campaign and local developmental
work before a separate consolidation audit. Stage 1 adds this validation record;
the 71 pre-existing changed/new files retain exactly their inventoried bytes. No
source, test, configuration, or instruction changes were needed during validation.
Consolidation and GitHub publication are subsequent stages.

## Preserved inventory

The starting tree contained 14 modified tracked files and 57 new files. The new
files contained 14,262 text lines; the tracked diff contained 322 additions and
122 deletions. This report is the checkpoint's additional file.

| Area | Changed/new files | Preserved work |
| --- | ---: | --- |
| Repository configuration | 3 | Existing `.gitignore`, `AGENTS.md`, and optional-dependency changes |
| Experiment configurations | 7 | Atlas campaign and runtime plans |
| Documentation | 12 | Frontier, compiler, local endurance, graduate algebra, and PPRL design/evidence records |
| Model adapters | 4 | Local Metal/developmental and Vertex/credential boundaries |
| Atlas | 16 | Activation, dispatch, coding tools/judge/runners, local host controls, and existing harness/registry changes |
| Domain packages | 2 | Graduate algebra task construction and review contracts |
| Experiments | 2 | Local developmental setup and Atlas execution-control binding |
| Shared orchestration | 1 | Captured-call activation, deadline, transaction, and replay handling |
| Teaching | 1 | File-authored intervention adapter |
| Scripts | 13 | Preparation, preflight, launch, execution, control, and validation entry points |
| Tests | 10 | Atlas, credential, process-control, coding, and native developmental coverage |

An initial byte inventory and compressed source snapshot were retained locally
before validation. The original candidate hashes and parent commit were rechecked
after validation with no drift. Existing `AGENTS.md` changes are included unchanged;
this checkpoint makes no additional instruction-policy edit.

## Validation

Validation used the existing Python 3.12.14 development environment with the GCS
and coding dependencies available. No model, cloud, GCS, PostgreSQL, Lean, or Docker
workload was launched for this checkpoint.

| Command | Result |
| --- | --- |
| `.venv/bin/ruff format --check .` | Passed; 451 Python files already formatted |
| `.venv/bin/ruff check . --output-format=json` | Passed; no lint findings |
| `.venv/bin/mypy padawan` | Passed; 212 source files |
| `PYTHONPATH=. .venv/bin/python scripts/generate_schemas.py --check` | Passed; 189 schemas match their contracts |
| `PYTHONPATH=. .venv/bin/pytest -m 'not postgres and not live and not lean and not docker and not gcs'` | 1,063 passed, 2 environment-permission failures, 39 deselected; 228.10 seconds |
| `PYTHONPATH=. .venv/bin/pytest tests/unit/test_atlas_local_host.py -q` with host process-inspection permission | All 3 passed; 0.91 seconds |
| `.venv/bin/padawan --help` | Passed |
| `git diff --check` | Passed for the original tracked diff |

Both initial failures occurred when `/bin/ps` was denied by the execution sandbox,
before process identity could be established. The targeted rerun exercised all
three disposable process-control tests with the required permission, including
the two previously blocked tests. No source changes or test weakening occurred.
All 1,065 selected cases therefore have passing results across the original run
and targeted rerun; this is not reported as one uninterrupted passing suite run.
The original failed fixtures' process groups had no remaining live members.

The 39 deselected tests retain their dependency/runtime gates. In particular, the
new coding runner's Docker-backed official-judge integration is not validated by
the ordinary offline suite alone.

## Secret and runtime-evidence checks

Gitleaks 8.30.1 was obtained from its
[official release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1).
The Darwin arm64 archive matched the published SHA-256 checksum before execution.
The scanner ran locally with full redaction, default rules, no configured baseline,
and inline allow comments ignored. Repository contents and credentials were not
sent to the download source or another scanning service.

- The prospective tree scan covered 706 files and 7,286,894 bytes before this report.
- The history scan covered all 38 commits reachable from all local refs.
- Each scan reported the same single `generic-api-key` finding at
  `configs/pprl/nemotron-local-pilot-v1.json:65`, already present in the parent commit.
  This is the `model.tokenizer_sha256` field. Rehashing the existing 17,077,484-byte
  `tokenizer.json` reproduced that value and the independently retained model capsule.
  It is a verified checksum false positive, not a credential. No suppression rule
  was added to the repository.
- A separate local comparison checked the two available provider credential values
  and their base64 encodings against the prospective tree and all 2,252 Git objects,
  including unreachable objects, totaling 24,989,321 bytes. There were no matches.
  Credential values were neither printed nor retained in the audit reports.
- `.env` remains mode `0600`, ignored, untracked, and absent from all-ref path history.
  `.env.*`, `runs/`, and the root artifact directory remain ignored.
- The 71 original candidates include no raw run directory, raw response file,
  credential file, database, or runtime-log candidate. Two pre-existing files under
  `reports/live/artifacts/blobs/` are command-manifest JSON records; no new such
  artifacts are included in this checkpoint.

These checks found no credential leak. The scanner's one reviewed finding is
retained as evidence rather than relabeled as a zero-finding raw scan.

## Known limitations for the consolidation audit

1. **CI dependencies and test selection need reconciliation before publication.**
   `.github/workflows/ci.yml` still installs only `.[dev]` in both jobs. Local
   validation includes GCS and coding dependencies. Its quality test selector also
   differs from the offline selector above. A clean CI installation and GitHub
   execution were not validated in this stage.
2. **This is the complete accumulated implementation snapshot.** Separate local,
   Vertex, coding, and developmental runners and multiple experiment-design records
   remain intact. Their consolidation, shared ownership, and active-versus-historical
   documentation are work for the subsequent audit.
3. **Pilot outcomes retain their original scientific limits.** The graduate algebra
   64K episode used thinking disabled, session-authored teaching and grading, and
   distinct transfer problems. It admitted no lesson and updated no weights. Its
   [results report](../../docs/graduate-algebra-64k-results.md) preserves those limits.
4. **Training and process readiness are unchanged.** The repository prepares
   evidence/training products but has no operational parameter-update backend.
   Atlas training materialization and general PPRL scheduling/coordination remain
   incomplete; offline tests do not establish learned capability or a live institution.
5. **Private execution evidence stays local.** Source inventories, redacted scanner
   reports, exact credential-comparison results, test logs/XML, and the starting
   snapshot are retained under ignored `runs/stage1-checkpoint-20260907/`. The staged
   and committed content must be checked against that source inventory before the
   checkpoint is reported complete.

No consolidation refactor, branch merge, history rewrite, GitHub push, deployment,
or new live model campaign is part of this checkpoint.
