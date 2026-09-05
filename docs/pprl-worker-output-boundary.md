# PPRL normalized worker output and privileged invocation evidence

Status: the model-result portion of information-boundary checkpoint 3. The reference/ownership
portion is described in `pprl-reference-ingress-boundary.md`, and structural content admission in
`pprl-content-admission.md`. Exact hydration and training projections remain unfinished.

`ProcessGenerationExecutor.execute` returns a broker `ProcessGenerationResult` containing an
invocation ID and `ProcessWorkerOutput`. The latter contains only public `output_text` and validated
integer input/output token counts. Worker prompt assembly may consume this normalized output; the
causal invocation ID belongs to broker control and evidence joins. There is no complete generation
object, raw artifact reference, arbitrary usage metadata, provider metadata, private reasoning,
summary, token/logprob array, or telemetry in the result interface.

The public output field is the adapter's designated public channel. This boundary does not establish
that a dishonest adapter classified its content correctly, or semantically filter arbitrary model
text. An authorized adapter and independently enforced worker containment remain prerequisites.

Requests and full generation results remain protected raw artifacts behind `ExternalCallRow` and
`ProcessWorkerInvocationRow`. Successfully finalized PPRL invocations classify both artifacts as
forensic and pin their retention ownership before returning. The lower-level
`IdempotentGenerationExecutor` retains its complete-result interface for trusted broker operations;
workers must not receive that object or the raw backend.

`ProcessStore.append_event` now joins the admitted worker invocation through its causal ID instead
of requiring raw artifacts in event `artifact_refs`. It checks completed invocation identity,
chronology, distinct request/response records, forensic classification, catalog identity, and
retention ownership. If an invocation exists for the Amber decision, the event must cite it. Omitting
the ID cannot bypass the source checks or artifact-byte reservation. Retained request/response
bytes count together with new process artifacts against the action's declared reservation.

New initial/event/state top-level references require reviewed admission, including duplicate
attempts. Generic references, even when non-raw, grant no new process use. This is not the complete
ingress proof: nested dictionaries and memory/evidence strings now have structural admission, but
semantic provenance and worker/training projections remain unfinished. Privileged replay is distinct
from new worker use.

Ordinary generation failures expose one `ProcessGenerationUnavailableError` with a fixed message
and no private exception cause/context. Cancellation remains cancellation but uses a fresh generic
exception. Reviewed artifact reads follow the same cancellation hygiene. Provider failures after
durable intent retain their detailed invocation/call records in the privileged plane. Preflight
failures, failed forensic persistence, and unavailable observations are not upgraded into complete
capture claims. The scheduler still needs independent failure classification and reconciliation.

The broker, adapter composition, database, and broker-owned storage are trusted for this checkpoint.
These interfaces are not a service credential boundary or an OS sandbox. The current budget checks
also do not establish fleet-wide conserved resource reservations or eliminate dispatch races.

Tests in `test_pprl_generation.py` retain synthetic private fields in the raw result while checking
their absence from normalized output and newly committed event/state references. They reject raw
reference injection, missing invocation ownership, omitted invocation IDs, and combined artifact
usage beyond the reservation without advancing state. `test_process_evidence.py` also checks provider
errors, invalid token counts, and cancellation without forwarding private exception payloads.

Rollback must disable consumers of the new result interface before reverting it and preserve
privileged invocation evidence. Do not restore automatic raw-reference insertion into process
events or use legacy records as worker-safe data. No real inference or live worker activation is
required by, or performed for, these validations.
