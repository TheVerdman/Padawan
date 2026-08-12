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
| `PADAWAN_DOMAIN_ID` | active workflow domain (`math.algebra`, `math.lean`, `legal.appellate.fourth_circuit`, or `temporal.grounding`) |
| `PADAWAN_OPENAI_MODEL`, `OPENAI_API_KEY` | Responses API baseline and/or teacher |
| `PADAWAN_ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY` | Anthropic teacher |
| `PADAWAN_INKLING_BASE_URL`, `PADAWAN_INKLING_MODEL` | authenticated Inkling Responses edge and exact served-model alias |
| `INKLING_API_KEY` or `PADAWAN_INKLING_API_KEY` | bearer credential for the Inkling Responses edge |
| `PADAWAN_INKLING_RUNTIME_REVISION` | exact runtime source revision represented by the validated serving image |
| `PADAWAN_INKLING_TENSOR_PARALLEL_SIZE` | validated topology; currently exactly four |
| `PADAWAN_INKLING_TIMEOUT_SECONDS` | per-request Inkling timeout; currently exactly 3,600 seconds |
| `PADAWAN_COMPATIBLE_BASE_URL`, `PADAWAN_COMPATIBLE_MODEL` | other real endpoint |
| `PADAWAN_EXPORT_HMAC_KEY` | opaque Heirloom audit identifiers |
| `PADAWAN_LEAN_PROJECT_ROOT` | pinned Lake project (default `./lean`) |
| `PADAWAN_LEAN_LAKE_EXECUTABLE`, `PADAWAN_LEAN_ELAN_HOME` | workspace-local Lean runtime |
| `PADAWAN_LEAN_SANDBOX_MODE` | `required`, `best_effort`, or explicit `off` |
| `PADAWAN_MAGELLAN_REPOSITORY_ROOT` | explicit external Magellan Git worktree |
| `PADAWAN_MAGELLAN_HANDSHAKE_PATH` | external typed environment-handshake JSON |

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

### Validated Inkling-Small-Ampere contract

The Inkling student integration is pinned to the validated
`responses-256k-candidate-v1` profile, served model `w8a16-balanced-v1`, converted checkpoint
`conversion-e747e8121d5cd12c54c9`, TP4 topology, and EOS-hotfix runtime revision
`aa2e7dd0f8f5fd1be0e4449f802ae5b72ffc534a`. The configured runtime window is 262,144 tokens. The
promoted evidence is narrower: single-request, batch-one exact retrieval through a 240,000-token
target (239,997 measured input tokens). It does not establish literal 262,144-token input,
concurrency, batching, availability, or task quality.

Padawan calls the separately authenticated ordinary Responses edge. Do not configure the Vertex
dedicated-Endpoint DNS: Vertex `Invoke` is a Google RPC and is not an OpenAI base URL. The runtime
rejects that DNS shape, `/v1`-suffixed base URLs, non-HTTPS remote edges, wrong model/profile/runtime
identity, Chat Completions fallback, response storage, and continuation. Before its first generation
it authenticates `GET /v1/models` and `GET /v1/padawan/capabilities`; only a matching service then
receives the streaming `POST /v1/responses`. Each transport invocation makes one attempt and asks
for `text/event-stream`; durable workflow recovery remains a separate, recorded decision.

Select the consumer edge and secret at runtime:

```text
export PADAWAN_INKLING_BASE_URL='https://your-stable-responses-edge.example'
export PADAWAN_INKLING_MODEL='w8a16-balanced-v1'
export INKLING_API_KEY='secret-manager-supplied-value'
export PADAWAN_INKLING_TIMEOUT_SECONDS=3600
```

The edge must have a deployed model behind it. Static model/capability documents alone do not make
a live student run successful, and the external validation teardown did not leave GPU compute
running. Padawan itself does not deploy or warm the serving stack.

Student state is immutable. A student identity whose canonical state was created by an older
Padawan build with the served-model alias in place of the conversion checkpoint, or with runtime ID
`inkling` instead of `inkling-vllm`, is rejected rather than silently relabeled. Start the validated
runtime with a new `--student-id` so the old evidence lineage remains intact.

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

The same bounded loop selects Lean or appellate authority through process configuration. `--bootstrap`
registers three-sibling curriculum inventory for the selected domain before the run:

```text
PADAWAN_DOMAIN_ID=math.lean \
  padawan experiment run --blocks 1 --bootstrap \
    --student-provider inkling --student-model "$PADAWAN_INKLING_MODEL" \
    --teacher-provider openai --teacher-model "$PADAWAN_OPENAI_MODEL"

PADAWAN_DOMAIN_ID=legal.appellate.fourth_circuit \
  padawan experiment run --blocks 1 --bootstrap \
    --student-provider inkling --student-model "$PADAWAN_INKLING_MODEL" \
    --teacher-provider anthropic --teacher-model "$PADAWAN_ANTHROPIC_MODEL"

PADAWAN_DOMAIN_ID=temporal.grounding \
  padawan experiment run --blocks 1 --bootstrap \
    --student-provider inkling --student-model "$PADAWAN_INKLING_MODEL" \
    --teacher-provider openai --teacher-model "$PADAWAN_OPENAI_MODEL"
```

Temporal bootstrap scenarios are synthetic and include gap continuity, activity honesty,
observation freshness, duration calibration, and online ETA revision. They do not attach real user
timestamps or activity metadata to ordinary provider calls. See
[temporal grounding and duration calibration](temporal-grounding.md).

In the appellate composition, the configured teacher transport also executes separately identified
semantic-adjudicator calls. Those calls use their own durable purpose, strict assessment schema,
and `adjudicator` research role; their raw responses remain restricted evidence and are not target
student outputs. Deterministic hard-gate failures skip adjudication entirely. This does not supply a
citator: currentness remains unknown until a licensed provider or governed import is admitted in a
new court-pack version.

The official OpenAI path always uses `POST /v1/responses`, including structured output under
`text.format`; it has no Chat Completions fallback. Validated Inkling serving is also Responses-only.
Generic compatible endpoints use Responses by default. `supervisor run` alone exposes
`--allow-legacy-student-fallback`, and that option is accepted only for a generic compatible
student; using it is recorded in provider metadata. An OpenAI run in the student-shaped provider
slot is labeled `baseline`, never `target`.

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

The Lean developmental workflow sends the student only the pinned theorem statement and accepts
only a tactic proof beginning with `by`. Cold, revision, and matched treatment/control transfer
proofs all pass through this verifier; teacher or student confidence cannot substitute for kernel
acceptance.

On macOS the default `required` sandbox denies all network operations, confines filesystem writes
to the per-proof temporary directory, and denies common credential/key directories. macOS does not
honor a lowered `RLIMIT_AS`; each result therefore truthfully records
`memory_limit_enforced=false` there. On a platform without this sandbox, `required` returns an
infrastructure failure. `best_effort` or `off` must only be selected inside a separately enforced
container/VM boundary and the result records that network isolation was not provided.

## Magellan Improvement gate

Padawan never discovers or imports Magellan. Select its worktree and externally generated
handshake at runtime, then run the read-only assessment:

```text
export PADAWAN_MAGELLAN_REPOSITORY_ROOT=/operator/selected/magellan
export PADAWAN_MAGELLAN_HANDSHAKE_PATH=/operator/generated/magellan-handshake.json
padawan --json verify magellan-environment
```

Neither path is written to the command manifest. A missing handshake produces a successful
inspection with `ready=false`; a malformed file fails the command. Sensitive ignored files are
named but never read, and a ready runtime must record the exact secret environment-name allowlist
rather than mount those files. New bound corpus inventory is admitted only from a ready assessment:

```text
padawan corpus generate magellan --groups-per-family 1 --siblings-per-group 2 --seed 20260801
```

The current audited Magellan worktree is not ready: it has no matched-world reset/fork boundary,
uses process-local idempotency, does not enforce every declared capability identity, permits a
caller-constructed approval bypass, and still uses Chat Completions for OpenAI planning. See
[Magellan Improvement integration](magellan-improvement.md) for the full contract and audit.

## Internal training products

Compile the latest deterministic source watermark, then verify every restricted artifact and row:

```text
padawan --json training compile
padawan --json training verify BUNDLE_ID
padawan --json training inspect BUNDLE_ID
```

The compiler never calls a trainer. Baseline and teacher outputs remain in the evidence ledger but
are excluded from target-training views by default. Source rights, explicit lane eligibility,
checkpoint/tokenizer registration, contamination state, and split all gate admission. Continued
pretraining accepts only separately admitted source documents. Verifier-backed project-authored
gold appears in the separate `authored_sft` product and never impersonates a successful student
attempt. See [internal training products](training-products.md) for rights manifests, source
admission, reproduction timestamps, and the complete exclusion policy.

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
padawan report study STUDY_ID
padawan report reward REWARD_ID
padawan report checkpoint CHECKPOINT_ID
padawan report operations
padawan provenance verify --stream-id global
```

Manual forks use `padawan state fork STATE_ID --experiment-id EXPERIMENT_ID`. Pause or release a
governance hold with `padawan supervisor pause RUN_ID` and `padawan supervisor resume RUN_ID`.

## Reward, study, and checkpoint operations

R2.3 lifecycle mutations are service boundaries used by governed workflows rather than ad hoc CLI
switches:

- `RewardEngine` registers immutable reward policies and verifier results, computes records, checks
  training eligibility, and recomputes stored utilities;
- `StudyEngine` registers immutable manifests and aggregates original experiment blocks;
- `EvaluationScheduler` schedules, claims, completes, and recovers retention/interference work;
- `CheckpointRegistry` registers external checkpoint and suite manifests, records evaluations,
  recomputes comparisons, and applies auditable promotion, rejection, quarantine, or revocation.

The report commands are intentionally read-only. `report reward` includes policy/evidence-backed
recomputation and training eligibility. `report study` includes the immutable manifest, condition
aggregation, attrition, and trial outcomes without lease secrets. `report checkpoint` includes its
lineage, frozen-suite evaluations, comparisons, lifecycle decisions, and current integrity checks.
`report operations` adds counts for rewards, study/trial states, and checkpoint states.

A worker must persist prompt exposure before completing a student retention/interference outcome.
If the provider or environment fails before any student outcome exists, record an explicit
infrastructure failure with missing reasons and no exposure rather than fabricating a failure score.
Expired evaluation leases are safe to recover: the trial returns to `scheduled` and its exact corpus
item returns to `active` in the same transaction.

Checkpoint promotion never calls a trainer. Import the externally produced artifact digest, keep
both N and N+1 frozen, evaluate both against the same registered suite digest, then compare under a
versioned policy. Optional missing efficiency evidence does not become zero; missing required
metrics, invalid hard gates, or regression breaches block promotion. Revocation changes only the
registry decision and preserves all evidence and lineage for rollback/audit.

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
