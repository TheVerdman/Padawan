# Padawan Interaction Lab

The Padawan Interaction Lab is the private, single-user interface for exploratory conversations
with a selected student deployment. It is a Padawan product surface. Student deployments are data:
the first configured target may display as `Inkling-Small-Ampere`, while session, message, turn,
trace, route, and service contracts remain model-neutral.

This slice establishes trustworthy interaction and trace infrastructure. It does not implement
Dreaming V3, memory synthesis or retrieval, experiment episodes, training-row compilation, a
trainer, or weight updates.

## Run it locally

Install and migrate first, choose a long random Lab access token, and configure at least one
student target:

```text
.venv/bin/padawan db migrate
export PADAWAN_INTERACTION_ACCESS_TOKEN='secret-manager-or-random-local-value'
export PADAWAN_INKLING_MODEL='w8a16-balanced-v1'
export INKLING_API_KEY='serving-edge-credential'
export PADAWAN_INKLING_EDGE_DEPLOYMENT_REVISION='verified-edge-deployment-revision'
.venv/bin/padawan interaction serve
```

The default address is `http://127.0.0.1:8765`. The command refuses non-loopback bind addresses.
It composes configured clients but does not deploy, wake, scale, or otherwise start model or GPU
infrastructure. The browser receives neither student-serving credentials nor authorization
headers. An explicit **Check readiness** action performs only the target's authenticated capability
preflight.

## Interaction semantics

Every durable generation creates first-class interaction records, not an experiment episode:

1. The user selects an immutable assistant message as the branch parent.
2. Padawan walks that message's exact parent chain and renders the selected transcript plus the new
   user message.
3. The request always sets `store=false` and `previous_response_id=null`; server-side continuation
   is not used.
4. A target-specific event iterator yields public deltas as they arrive. Padawan does not wait for
   `response.completed` before forwarding text.
5. The invocation ledger, timing, events, serving identity, artifacts, and immutable assistant
   message are sealed under an exploratory trace.

Messages are append-only and carry parent IDs. Retry reuses the source turn's user text and parent;
replay may change declared axes. For a comparison replay, declared and observed axis changes must
match exactly. This preserves identical-transcript branches without relabeling exploratory chat as
controlled benchmark evidence.

Each exploratory manifest binds:

- selected target, model, checkpoint, quantization, runtime, serving artifact, transport artifact
  when present, endpoint origin, protocol, route, and serving-path components;
- harness/profile identity, parent turn, source comparison trace, and source manifest digest;
- selected message identities and digests, input digest, and exact rendered-context digest;
- the memory-snapshot digest (`null` in this slice), system-prompt identity, and no-tools identity;
- complete sampling settings, including seed when supplied;
- the consent-event snapshot and retention classification.

The evidence class is always `exploratory_not_controlled_benchmark`, and database constraints keep
`controlled_benchmark_eligible=false`, memory status `not_implemented`, and training status
`not_admitted`.

## Four separate data lanes

| Lane | First-slice behavior |
| --- | --- |
| Personal conversation | Useful durable session/message/turn data and personal traces; logically deletable. |
| Consented research evidence | Immutable, attributed turn traces that survive deletion of the personal conversation view. |
| Synthesized memory | No writes. The manifest has an explicit nullable snapshot seam for the proposal/review/retrieval phase. |
| Training candidates | No admission. Conversation content, feedback, and private reasoning never become training rows here. |

Consent is append-only and snapshotted on each turn. Changing the toggle affects future turns; it
does not rewrite earlier traces. A consented future trace retains its complete rendered request,
including any selected earlier messages, as immutable evidence. The UI calls this out because
selecting personal history into a consented request moves that copied request context into the
research-evidence lane.

Feedback and corrections are separate records linked to a turn and their current consent event.
They do not mutate the original user or assistant messages and are not training admission.
Personal feedback is deletable; feedback submitted while research consent is enabled is retained as
independent consented evidence even if its source conversation is later deleted.

## Temporary chat

Temporary chat keeps its transcript only in the current browser tab. Its server path calls the
target event iterator directly and does not create a session, message, turn, trace, external-call
intent, operation span, artifact, memory record, or training candidate. Reloading or closing the
tab loses the transcript. Target and reverse-proxy infrastructure may still have independent logs;
Padawan cannot promise deletion outside the configured serving boundary.

## Trace and private-reasoning access

The ordinary trace inspector shows the rendered-request and history artifact identities, manifest,
model/checkpoint/runtime/serving identity, sampling, usage, timing, event summary, telemetry, and
artifact metadata. Raw requests and events remain restricted.

Model-emitted private reasoning is captured only when the selected runtime exposes a distinct
reasoning channel. It is stored as a separate `restricted=true, raw_data=true` artifact. It is
never persisted in a transcript message and is not used as later context, memory, feedback, or
training data. Each durable assistant response has a collapsed **Reasoning** disclosure directly
above its public text. Expanding it is the explicit research-view action: only then does the browser
request only the separate private-reasoning artifact with the research-trace header. The trace
inspector has an independent collapsed disclosure for the full raw request/event view. Neither
restricted view is loaded by default.

## Deletion and retention

Deleting a conversation removes its personal session, consent, message, turn, and personal
feedback rows. It removes personal trace rows, their invocation rows, operation telemetry, and
artifact ownership references. Consented research traces, consented feedback, and their evidence
references are retained and returned explicitly in the deletion result.

The artifact backend is content-addressed. Removing the last ownership reference makes a personal
blob eligible for garbage collection but does not synchronously erase the bytes. Garbage
collection must use the database-derived live digest set and is dry-run by default. Until that
separate lifecycle runs, an operator with direct filesystem or bucket access may recover an orphan
blob. Backups and target-side logs have their own retention. This slice therefore provides logical
deletion, not cryptographic erasure or immediate physical purge.

## Security boundary

The Lab is loopback-only and requires a separate access token. Authentication creates an
HTTP-only, same-site process-local browser session; state-changing requests also require a CSRF
token. Responses send a restrictive CSP, deny framing, disable sniffing, and disable caching.
OpenAPI and documentation routes are off.

This is a single-user local control, not multi-tenant authorization. Browser sessions disappear on
server restart, have no distributed revocation store, and are served over local HTTP by default.
Use encrypted local storage and restrictive OS ownership. Do not expose the Lab through a remote
proxy in this slice.

## Extension seams

The next memory phase can attach proposal-only memory snapshots by digest, add sourced and
reversible review records, and compare no-memory/manual-memory/synthesized-memory branches through
declared axes. Those additions must remain independent from conversation storage and training
admission. Controlled benchmark promotion requires the existing research-control contracts and a
separate governed workflow; an exploratory trace cannot be relabeled in place.
