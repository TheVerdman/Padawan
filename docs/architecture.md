# Architecture

Padawan is an independent process and database. It coordinates model runtimes; it is not embedded
in a checkpoint server, a teacher provider, Heirloom, or VECL-QB. The implemented research path is
symbolic algebra, but its state, evidence, orchestration, and adapter boundaries are model-neutral.

## Runtime composition

`padawan.config.composition.build_live_application` is the only live composition root. It creates
one async database engine, local artifact backend, domain authorities, external-call executors, the
algebra workflow handler, and a supervisor. The authorities remain separate:

| Authority | Implemented owner | Role |
| --- | --- | --- |
| student runtime | `adapters.*` | real async generation and capability reporting |
| corpus | `CorpusRegistry` | lineage, leasing, exposure, retirement, quarantine |
| grader | `AlgebraGrader` | deterministic SymPy outcome and first-invalid-step evidence |
| teacher | `TeacherService` plus provider adapter | structured intervention generation |
| comment validation | `CommentValidator` | evidence, contradiction, span, and leakage checks |
| state | `StateStore` | immutable lineage, symmetric forks, canonical promotion |
| episode | `EpisodeStore` | typed attempts, grades, interventions, trials, final episodes |
| memory | `LessonMemory` | versioned lessons, retrieval decisions, conflicts, rollback |
| experiment | `ExperimentEngine` | deterministic blocks, counterbalancing, paired analysis |
| governance | `governance.*` | access, retention, export, and command-manifest policy |
| consolidation | `MemoryConsolidationBackend` | evidence-gated lesson consolidation and rollback |

The OpenAI adapter is fixed to `POST /v1/responses`. OpenAI-compatible and Inkling clients also
start with the Responses protocol; the legacy Chat Completions path is used only when the operator
explicitly enables compatibility fallback. Anthropic is a distinct `/v1/messages` implementation.

## Durable action loop

A `RunRow` is a state machine, not a call stack. One worker claim protects exactly one action. The
worker persists the next transition and releases the lease before another action may begin. The
happy path contains 20 transitions from `CREATED` through `COMPLETE`; retryable, terminal, and
review states are explicit. A database uniqueness constraint permits only one active run for a
student, while terminal transitions release that slot.

Before network I/O, `IdempotentGenerationExecutor` commits an external-call intent and restricted
request artifact. After I/O it commits a restricted response envelope before the run transition.
Replaying the same request ID returns the stored response and rejects a different request body.
Thus a crash after a provider response but before a state transition does not generate twice.
The corpus registry also commits a conservative student exposure before each student transport call;
a terminal failure releases its leases but the exposed sibling group remains ineligible for that
student.

The implemented episode flow is:

1. lease one three-item matched sibling group and create an active episode;
2. record the immutable base state and fork treatment/control states transactionally;
3. run and deterministically grade the cold item;
4. request a strict evidence-citing teacher intervention and validate it;
5. run and grade a revision on the treatment branch;
6. run unseen sibling transfer on both inherited branches;
7. record the matched block and decide whether evidence permits a lesson-memory write;
8. atomically record exposures and retirement;
9. append the selected branch state and promote it as canonical;
10. commit the typed episode and complete the run.

Treatment must show a strict transfer advantage to become canonical; a tie retains control. Both
branches and all failed work remain stored.

## Storage

PostgreSQL is the production concurrency target. PostgreSQL claims use row locks with `SKIP LOCKED`;
SQLite uses conditional updates and exists for local work and tests. Alembic owns schema creation.

The database stores normalized, indexed records and immutable artifact references. Raw provider
bytes, exact rendered requests, private traces when actually exposed, and command manifests live in
the local SHA-256 content-addressed store. Writes use a same-directory temporary file, `fsync`, and
atomic replacement. Reads recheck URI, size, and digest. Garbage collection defaults to dry-run and
accepts the database-derived referenced digest set.

Provenance is a separate append-only hash chain. Each event commits a canonical payload hash, prior
chain hash, actor, code revision, environment, state lineage, and episode link. Verification
recomputes the stream rather than trusting a stored boolean.

## Repository integrations

- Inkling remains an independently served vLLM/OpenAI-compatible runtime. Padawan records its
  configured checkpoint, runtime revision, quantization availability, tensor parallelism, and any
  server telemetry; it never invents unavailable token or reasoning data.
- Heirloom is a restricted audit export. Padawan emits a signed, policy-filtered bundle that the
  sibling Heirloom structural validator can inspect. This does not upgrade structural validation
  into semantic correctness.
- VECL-QB is not imported. Its provenance, artifact, and episodic-store ideas were independently
  reimplemented to avoid coupling Padawan to its experimental trainer.

## Present boundaries

The complete operational workflow is algebra. There is no S3 artifact backend and no parameter
update implementation. `UnsupportedParameterUpdateBackend` fails explicitly because no backend can
yet isolate, evaluate, commit, and restore a real weight update. Delayed-retention timestamps and
router telemetry are represented by contracts, but no scheduler or instrumented Inkling extension
currently produces them.
