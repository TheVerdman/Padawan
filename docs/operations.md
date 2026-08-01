# Operations

Padawan requires Python 3.12 or 3.13. PostgreSQL is the concurrency target; SQLite is suitable for a
single local process and the non-PostgreSQL test suite.

## Installation and configuration

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/padawan db migrate
```

Configuration is loaded once at the CLI composition boundary. Important variables are:

| Variable | Purpose |
| --- | --- |
| `PADAWAN_DATABASE_URL` | async PostgreSQL or SQLite URL |
| `PADAWAN_ARTIFACT_ROOT` | local content-addressed store root |
| `PADAWAN_CODE_REVISION` | revision committed into provenance |
| `PADAWAN_WORKER_ID` | stable worker identity |
| `PADAWAN_LEASE_SECONDS` | item and run lease period |
| `PADAWAN_OPENAI_MODEL`, `OPENAI_API_KEY` | Responses API student and/or teacher |
| `PADAWAN_ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY` | Anthropic teacher |
| `PADAWAN_INKLING_BASE_URL`, `PADAWAN_INKLING_MODEL` | Inkling Responses endpoint |
| `PADAWAN_COMPATIBLE_BASE_URL`, `PADAWAN_COMPATIBLE_MODEL` | other real endpoint |
| `PADAWAN_EXPORT_HMAC_KEY` | opaque Heirloom audit identifiers |

`PADAWAN_OPENAI_API_KEY` and `PADAWAN_ANTHROPIC_API_KEY` are accepted aliases. Do not put `.env` in
version control. Command manifests contain only redacted URLs and credential-presence booleans.

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

Run one real Inkling/OpenAI-teacher episode after the endpoint and model are known:

```text
padawan experiment run --blocks 1 --bootstrap \
  --student-provider inkling --student-model "$PADAWAN_INKLING_MODEL" \
  --teacher-provider openai --teacher-model "$PADAWAN_OPENAI_MODEL"
```

The OpenAI path always uses the Responses API. Inkling and generic compatible endpoints also use
Responses by default. `supervisor run` alone exposes `--allow-legacy-student-fallback`; using it is
an explicit compatibility decision and is recorded in provider metadata.

`supervisor run` creates or resumes work for one student until the episode budget or a stop
condition. `worker run` processes a bounded number of already-created durable actions. Multiple
workers may share PostgreSQL. Use a unique `PADAWAN_WORKER_ID` per process.

Every CLI success and failure emits a content-addressed command manifest. With `--json`, place the
global flag before the subcommand: `padawan --json corpus inspect`.

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

`make check` runs formatting/lint, strict mypy, and the local suite. `make test-postgres` requires a
dedicated URL whose database name contains `test`:

```text
export PADAWAN_TEST_POSTGRES_URL='postgresql+psycopg://padawan:secret@localhost/padawan_test'
make test-postgres
```

Credentialed live tests are never counted as passed when unavailable. Schema drift is checked with
`python scripts/generate_schemas.py --check`.
