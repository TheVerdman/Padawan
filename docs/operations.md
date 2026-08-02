# Operations

Padawan requires Python 3.12 or 3.13. PostgreSQL is the concurrency target; SQLite is suitable for a
single local process and the non-PostgreSQL test suite.

## Installation and configuration

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,gcs]"
cp .env.example .env
.venv/bin/padawan db migrate
```

Configuration is loaded once at the CLI composition boundary. Important variables are:

| Variable | Purpose |
| --- | --- |
| `PADAWAN_DATABASE_URL` | async PostgreSQL or SQLite URL |
| `PADAWAN_ARTIFACT_BACKEND` | `local` or `gcs` |
| `PADAWAN_ARTIFACT_ROOT` | local content-addressed store root |
| `PADAWAN_GCS_PROJECT`, `PADAWAN_GCS_BUCKET`, `PADAWAN_GCS_PREFIX` | GCS identity and object namespace |
| `PADAWAN_CODE_REVISION` | revision committed into provenance |
| `PADAWAN_WORKER_ID` | stable worker identity |
| `PADAWAN_LEASE_SECONDS` | item and run lease period |
| `PADAWAN_OPENAI_MODEL`, `OPENAI_API_KEY` | Responses API baseline and/or teacher |
| `PADAWAN_ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY` | Anthropic teacher |
| `PADAWAN_INKLING_BASE_URL`, `PADAWAN_INKLING_MODEL` | Inkling Responses endpoint |
| `PADAWAN_COMPATIBLE_BASE_URL`, `PADAWAN_COMPATIBLE_MODEL` | other real endpoint |
| `PADAWAN_EXPORT_HMAC_KEY` | opaque Heirloom audit identifiers |
| `PADAWAN_LEAN_PROJECT_ROOT` | pinned Lake project (default `./lean`) |
| `PADAWAN_LEAN_LAKE_EXECUTABLE`, `PADAWAN_LEAN_ELAN_HOME` | workspace-local Lean runtime |
| `PADAWAN_LEAN_SANDBOX_MODE` | `required`, `best_effort`, or explicit `off` |

`PADAWAN_OPENAI_API_KEY` and `PADAWAN_ANTHROPIC_API_KEY` are accepted aliases. Do not put `.env` in
version control. Command manifests contain only redacted URLs and credential-presence booleans.

An external dotenv file is opt-in only. Select it as a process bootstrap variable; Padawan never
searches sibling repositories:

```text
PADAWAN_ENV_FILE=/absolute/operator-controlled/path/.env padawan --json corpus inspect
```

Process variables override values from that file. Development emits a warning if the file is
group/world-readable; production refuses it. Use mode `0600`. The file path is runtime state and
must not be committed to configuration, examples, provenance, or command arguments.

## GCS artifacts

GCS uses Application Default Credentials and never needs a service-account JSON path in this
repository:

```text
export PADAWAN_ARTIFACT_BACKEND=gcs
export PADAWAN_GCS_PROJECT='your-project-id'
export PADAWAN_GCS_BUCKET='your-existing-bucket'
export PADAWAN_GCS_PREFIX='padawan/production'
padawan --json report operations
```

Objects are addressed as `PREFIX/blobs/sha256/AA/REST`. Uploads use a create-only generation
precondition and CRC32C; a duplicate is accepted only after the existing object's immutable
metadata and size agree. Reads are generation-pinned and recheck SHA-256. Artifact metadata records
bucket, object, generation, metageneration, ETag, checksums, and project. Restricted/raw
classification cannot be weakened by deduplication. Garbage collection is dry-run unless an
operator explicitly requests deletion and supplies the database-derived live digest set.

The live GCS test uses a unique prefix and deletes its tiny object in `finally`:

```text
PADAWAN_TEST_GCS_PROJECT='your-project-id' \
PADAWAN_TEST_GCS_BUCKET='your-existing-bucket' \
  .venv/bin/pytest -m 'live and gcs' tests/integration/test_gcs_live.py
```

## PostgreSQL

For a local database:

```text
docker compose up -d postgres
docker compose run --rm migrate
export PADAWAN_DATABASE_URL='postgresql+psycopg://padawan:padawan-local-only@127.0.0.1:5432/padawan'
```

The migration container is one-shot. No model worker starts automatically because it would need
explicit real endpoint and credential authorization. In deployed environments, run Alembic before
workers and use a secret manager rather than Compose credentials.

## Corpus and live execution

Generate three-sibling curriculum groups and inspect them:

```text
padawan corpus generate algebra --groups-per-family 2 --siblings-per-group 3 --seed 20260801
padawan corpus validate
padawan corpus inspect
```

Generate matched Lean mathematics inventory independently of live model serving:

```text
padawan corpus generate lean-math --groups-per-family 2 --siblings-per-group 2 --seed 20260801
```

Run one real Inkling/OpenAI-teacher episode after the endpoint and model are known:

```text
padawan experiment run --blocks 1 --bootstrap \
  --student-provider inkling --student-model "$PADAWAN_INKLING_MODEL" \
  --teacher-provider openai --teacher-model "$PADAWAN_OPENAI_MODEL"
```

The official OpenAI path always uses `POST /v1/responses`, including structured output under
`text.format`; it has no Chat Completions fallback. Inkling and generic compatible endpoints also
use Responses by default. `supervisor run` alone exposes `--allow-legacy-student-fallback`; using
it is an explicit non-OpenAI compatibility decision and is recorded in provider metadata. An
OpenAI run in the student-shaped provider slot is labeled `baseline`, never `target`.

`supervisor run` creates or resumes work for one student until the episode budget or a stop
condition. `worker run` processes a bounded number of already-created durable actions. Multiple
workers may share PostgreSQL. Use a unique `PADAWAN_WORKER_ID` per process.

Every CLI success and failure emits a content-addressed command manifest. With `--json`, place the
global flag before the subcommand: `padawan --json corpus inspect`.

## Lean mathematics verifier

The committed Lake project pins Lean `4.32.2`, Mathlib `v4.32.2`, and the exact Mathlib revision in
`lean/lake-manifest.json`. The downloaded toolchain, dependency checkout, and binary cache remain
ignored under `.tools/` and `lean/.lake/`.

After downloading and inspecting the official `elan-init` installer, reproduce the workspace-local
setup from the repository root (the first command is the inspected installer, not a pipe from the
network):

```text
export ELAN_HOME="$PWD/.tools/elan"
sh /path/to/elan-init.sh -y --no-modify-path --default-toolchain none
"$ELAN_HOME/bin/elan" toolchain install leanprover/lean4:v4.32.2
cd lean
../.tools/elan/bin/lake update
MATHLIB_CACHE_DIR="$PWD/../.tools/mathlib-cache" ../.tools/elan/bin/lake exe cache get
../.tools/elan/bin/lake build
```

A proof file contains only a tactic term beginning with `by`, not imports or a theorem declaration:

```text
padawan --json verify lean \
  --statement '∀ x : Int, 3 * x + 2 = 17 → x = 5' \
  --proof-file ./candidate-proof.lean
```

The verifier resolves `LEAN_PATH` from the pinned Lake closure, then invokes the pinned Lean binary
directly. It generates the import and theorem wrapper, rejects command/metaprogramming escape
tokens, supplies an allowlisted environment with no provider credentials, caps wall/CPU/output/file
descriptors, and distinguishes proof rejection from timeout, sandbox, or environment failure.

On macOS the default `required` sandbox denies all network operations, confines filesystem writes
to the per-proof temporary directory, and denies common credential/key directories. macOS does not
honor a lowered `RLIMIT_AS`; each result therefore truthfully records
`memory_limit_enforced=false` there. On a platform without this sandbox, `required` returns an
infrastructure failure. `best_effort` or `off` must only be selected inside a separately enforced
container/VM boundary and the result records that network isolation was not provided.

## Recovery and inspection

Provider intent is committed before I/O and response before transition. Restarting a worker is the
normal crash-recovery procedure: the same request ID returns persisted bytes. Expired run/item
leases and stale heartbeats are recovered automatically. Retryable runs resume from the recorded
`retry_from_state`; terminal and review states require a new governed decision.

Useful commands:

```text
padawan episode inspect EPISODE_ID
padawan state inspect --student-id STUDENT_ID
padawan memory inspect
padawan report experiment EXPERIMENT_ID
padawan report operations
padawan provenance verify --stream-id global
```

Manual forks use `padawan state fork STATE_ID --experiment-id EXPERIMENT_ID`. Pause or release a
governance hold with `padawan supervisor pause RUN_ID` and `padawan supervisor resume RUN_ID`.

## Heirloom export

```text
PADAWAN_EXPORT_HMAC_KEY='secret-from-a-secret-manager' \
  padawan export heirloom EPISODE_ID --output-root ./restricted-audit

/path/to/Heirloom/target/debug/heirloom padawan validate \
  --episodes ./restricted-audit/episodes/PADAWAN_EPISODE.jsonl \
  --artifact-root ./restricted-audit/padawan
```

The export is restricted metadata evidence, not a semantic attestation. Raw provider bytes and
private reasoning remain excluded by default.

## Quality gates

`make check` runs formatting/lint, schema drift, strict mypy, and the hermetic local suite.
`make test-lean` exercises the installed real kernel and sandbox separately. `make test-postgres`
requires a dedicated URL whose database name contains `test`:

```text
export PADAWAN_TEST_POSTGRES_URL='postgresql+psycopg://padawan:secret@localhost/padawan_test'
make test-postgres
```

Credentialed live tests are never counted as passed when unavailable. Schema drift is checked with
`python scripts/generate_schemas.py --check`.
