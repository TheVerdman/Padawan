# Padawan Interaction Lab verification

## Dependency and scope

This slice is based on control-foundation revision
`c2e7a25b273e70b0055d18e7386b27a9116314e9` (`feat: make research attribution fail closed`) and
was integration-validated on Capability Atlas revision
`d492f8c49c96336feb22d9a709a1ead33559d423` (`feat: add capability atlas v0`). Its migration
`f91c2a7d4e30` has the Atlas migration `c9e8f4a1d2b3` as its sole parent, which in turn descends
from the completed control migration `a7c4e91d2b6f`; Alembic reports one head. The Lab reuses the
foundation's model-serving identity builder, external-invocation ledger, artifact catalog,
operation telemetry, harness profile, and extracted fail-closed Inkling admission gate. It creates
no synthetic experiment episode.

## Genuinely functional

- A loopback-only, access-token-authenticated, CSRF-protected single-user web chat is packaged with
  the Python wheel. Student-serving credentials stay in the server composition root.
- Durable conversations use immutable parent-linked session/message/turn records. Message, retry,
  branch, exact retry, and declared single-axis replay paths resend explicit selected history with
  `store=false` and no `previous_response_id`.
- A provider event iterator forwards public deltas before `response.completed`. Cancellation after
  streaming begins seals both the interaction trace and invocation/operation records.
- Model-neutral target selection supports the validated Inkling deployment and an additional
  Responses-compatible target. Each turn binds checkpoint/state, quantization, runtime, serving
  and transport artifacts, endpoint origin and full route, harness/profile, selected ancestry,
  rendered-context digest, prompt/tools, sampling/seed, consent, and retention.
- Exploratory traces are database-constrained to
  `exploratory_not_controlled_benchmark`, `controlled_benchmark_eligible=false`, memory
  `not_implemented`, and training `not_admitted`.
- The trace inspector exposes attribution, timing, usage, event summaries, artifact identities, and
  an explicit raw restricted-artifact disclosure. Model-emitted private reasoning is a distinct
  restricted artifact. A collapsed **Reasoning** disclosure directly above each durable answer
  fetches only that artifact on expansion; reasoning is absent from persisted transcript messages
  and later model context.
- Consent is append-only and snapshotted per turn. Personal and consented feedback/corrections are
  separate immutable records. Conversation deletion removes personal rows and references while
  reporting retained consented evidence explicitly.
- Temporary Chat calls the target stream without creating Padawan session, turn, trace, invocation,
  telemetry, artifact, memory, or training rows. Its browser-only history is cleared when leaving
  temporary mode.
- Readiness is explicit. Startup performs no endpoint probe, deployment, scale, wake, or GPU/cloud
  action.

## Validation

- `ruff format --check .`: 260 files formatted.
- `ruff check .`: passed.
- `node --check padawan/interaction/static/app.js`: passed.
- `PYTHONPATH=. python scripts/generate_schemas.py --check`: 105 generated schemas match their
  Pydantic contracts.
- `mypy padawan`: strict type checking passed for 146 source files.
- `pytest -m "not postgres and not live and not lean"`: 288 collected, 282 passed, 6 explicitly
  deselected.
- Alembic reports `f91c2a7d4e30` as the single head and migration tests cover upgrade, downgrade,
  and model/metadata parity.
- A real Playwright browser drove login, personal session creation, streaming chat, the collapsed
  response-level Reasoning disclosure, dedicated private-reasoning fetch, public trace inspection,
  raw restricted disclosure, and Temporary Chat state clearing against a deterministic local fake
  target. The browser console reported zero errors and zero warnings. No live target was contacted.
- The combined wheel includes both Capability Atlas and Interaction Lab modules, Lab static assets,
  and both migrations. Wheel SHA-256:
  `2b71bea3b598b27ad866440971c46949587cebd4056acfd096b3c1f2d09380f5`.

## Explicitly deferred or limited

Dreaming V3, memory proposals/review/retrieval, memory ablations, training-candidate grading or
admission, trainers, and weight updates are not implemented. No exploratory conversation can be
promoted in place to controlled benchmark evidence.

This slice does not provide remote or multi-user authentication, database row-level security,
application-managed blob encryption, immediate secure erase, or synchronous blob deletion.
Conversation deletion is logical: unreferenced content-addressed bytes remain until a separate
reference-safe garbage-collection lifecycle runs, and backups or target-side logs have independent
retention.

PostgreSQL-, Lean-, and live-provider-marked tests were not run. No serving endpoint, GPU, cloud
deployment, trainer, checkpoint, or sibling Inkling repository was changed or invoked.
