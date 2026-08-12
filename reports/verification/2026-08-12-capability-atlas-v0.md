# Inkling-Small-Ampere W8A16 Capability Atlas v0 verification

Campaign digest: `sha256:68f8cabe7912b68ef08924e4d04514d5a1c68bd6a5da8e66405b0d8e6fe5ba92`

Ontology digest: `sha256:810f07fac633e51b88f321c1589579141c706ea9c1db21c604feb026c46a7016`

Offline verification digest: `sha256:04944ef22b7c0bc0efb82125968adfd8575215b83bf057fa63eba0c7808cc4e4`

## Outcome

Capability Atlas v0 generated and validated 100 project-authored item manifests, structurally
checked 45 matched neighborhoods, executed 70 deterministic oracle payloads, 17 suite manifests, 14 upstream claim records, 17
dataset-governance records, a versioned failure ontology, 13 factor conditions, fixed stop rules,
three disjoint campaign partitions, and an exact three-stage live allocation plan. It made zero
external requests, deployed or woke no GPU, accepted no provider/dataset terms, and incurred $0.

Three suites are locally content-ready:

- `padawan-algebra-adaptive-v0` (32 items);
- `padawan-temporal-adaptive-v0` (30 items);
- `padawan-algebra-training-candidates-v0` (8 items, digest-disjoint from adaptive content).

The remaining suites are blocked with provenance-bound reasons. Lean needs its pinned kernel;
appellate needs semantic/currentness authority; Magellan needs a bound isolated environment; media
needs the sibling complete runtime gate; public benchmarks need content, rights, and evaluator
admission; and promotion needs unexposed items plus the core `EvaluationSuiteManifest` bridge.

## Evidence status

- Reported upstream: 14 official Thinking Machines claims. July 15 preview and July 30 release are
  different model revisions. These are campaign priors only.
- Locally reproduced model observations: none.
- Extrapolations: none.
- Unknown: W8A16 behavioral capability, source-BF16 comparison, media grounding, public benchmark
  results, and checkpoint promotion outcome.

The existing 240k Inkling record remains transport/exact-retrieval evidence only. It is not reported
as long-horizon reasoning or usable working memory.

Sibling serving evidence exercised only `reasoning.effort='none'`. The vendor's numeric effort
labels are not assumed to be current Responses wire values, and every non-`none` mapping and tool
structure remains blocked pending adapter registration, edge preflight, and behavioral validation.

## Offline checks

- PASS — campaign partitions are pairwise disjoint;
- PASS — each condition request ceiling equals its complete bound item × trial allocation;
- PASS — external benchmark content is absent;
- PASS — every local item, task, corpus, and suite digest validates;
- PASS — matched-neighborhood content and fixed axes are structurally validated (no independent
  metamorphic-relation result is claimed);
- PASS — every media registration fails closed;
- PASS — no external request or cost occurred;
- PASS — promotion is blocked, empty, and not falsely sealed;
- PASS — source claims are never local observations;
- PASS — the 240k transport control is never reasoning evidence.

## Local validation matrix

- PASS — `ruff format --check .` (247 files already formatted);
- PASS — `ruff check .`;
- PASS — strict `mypy padawan` (139 source files);
- PASS — generated-schema check (97 schemas match their Pydantic contracts);
- PASS — `pytest -m 'not postgres and not live and not lean'` (269 passed, 6 deselected),
  including migration upgrade/check/downgrade/upgrade coverage;
- PASS — `git diff --check`.

Postgres, live endpoint/provider, live GCS, and Lean-kernel tests were not executed. They remain
environment- or authorization-gated and are not silently counted as local evidence.

## Exact externally gated allocation plan

The content-ready W8A16 stage is the only presently materialized model-execution subset. The real
command below is explicitly preparation-only: it rebuilds the campaign, validates the exact
allocation and ceilings, requires a non-placeholder authorization reference, and emits a
content-addressed activation envelope plus evidence-artifact plan. It performs no network call,
database write, artifact write, provider request, GPU action, or spend. The reference is recorded
but not verified, and no model execution is permitted:

```text
padawan atlas campaign prepare --preparation-only --campaign-digest sha256:68f8cabe7912b68ef08924e4d04514d5a1c68bd6a5da8e66405b0d8e6fe5ba92 --allocation-set w8a16-content-ready --authorization-ref REPLACE_WITH_AUTHORIZATION_REFERENCE --max-requests 1306 --max-input-tokens 51642368 --max-output-tokens 19111936 --max-actions 3196 --max-cost-usd 2049.00 --max-runtime-minutes 16660
```

Ceiling: 1,306 requests, 51,642,368 input tokens, 19,111,936 output tokens, 3,196 actions,
$2,049, and 16,660 serial minutes.

The blocked W8A16 expansion cannot run until every included suite clears its recorded gates:

```text
padawan atlas campaign prepare --preparation-only --campaign-digest sha256:68f8cabe7912b68ef08924e4d04514d5a1c68bd6a5da8e66405b0d8e6fe5ba92 --allocation-set w8a16-blocked-expansion --authorization-ref REPLACE_WITH_AUTHORIZATION_REFERENCE --max-requests 4192 --max-input-tokens 139850240 --max-output-tokens 82706432 --max-actions 29654 --max-cost-usd 8440.00 --max-runtime-minutes 72300
```

The source-BF16 comparison also requires a separately registered reference endpoint/path and
quantization-only comparability:

```text
padawan atlas campaign prepare --preparation-only --campaign-digest sha256:68f8cabe7912b68ef08924e4d04514d5a1c68bd6a5da8e66405b0d8e6fe5ba92 --allocation-set source-bf16-reference --authorization-ref REPLACE_WITH_AUTHORIZATION_REFERENCE --max-requests 1740 --max-input-tokens 57016320 --max-output-tokens 28508160 --max-actions 1740 --max-cost-usd 4350.00 --max-runtime-minutes 17400
```

The full predeclared ceiling is 7,238 requests, 248,508,928 input tokens, 130,326,528 output
tokens, 34,590 actions, $14,839, and 106,360 serial minutes. These are future hard ceilings, not
spend or runtime already incurred.

Actual campaign execution remains unavailable and fails closed. A later governed gateway must
verify authorization, bind exact endpoints and `ResearchExecutionManifest` records, persist the
allocation/run envelopes, and independently enforce these ceilings before any provider or GPU work
can begin.

Every authorized stage is expected to produce exact `ResearchExecutionManifest`, harness,
`CampaignExecutionBinding`, request/response, grader/verifier, timing, token, retry, tool/action,
cost, missingness, failure, result, curve, cluster, comparison, and snapshot evidence.

## Closed-loop seams

Interaction Lab traces require consent, redaction, proposal deduplication, independent Atlas item
generation, repeated reproduction, and human challenge admission. Raw chats never become benchmark
evidence or training content automatically.

Mechanistic work joins behavioral evidence only through content-addressed phenomenon/probe-set
manifests, item digests, exact execution identity, and outcome digests. Activation/router telemetry
and interventions stay in the Inkling repository and cannot alter behavioral authority.

Training candidates require independently reproduced stable model failures, harness and verifier
clearance, appropriate rights, contamination clearance, and exclusion from adaptive/challenge/
promotion lanes. Direct compiler ingestion remains forbidden; governed corpus materialization is
required.

## Conclusions not supported

- W8A16 reproduces any preview or released source-BF16 score.
- Inkling-Small-Ampere has validated image, audio, video, or mixed-media capability.
- The 240k transport ladder demonstrates long-horizon reasoning.
- Any blocked public suite has been approximately or locally scored.
- Checkpoint N+1 improved or avoided regressions.
