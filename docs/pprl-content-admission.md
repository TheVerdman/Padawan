# PPRL structural content admission

Status: the nested-content portion of information-boundary checkpoint 3. Worker observations,
training/Atlas projections, and coordinated GC remain unfinished. This is offline broker validation,
not proof of semantic redaction, authentic authorship, or an execution sandbox.

## Contracts and paths

`ProcessContentBoundary` validates new state/event content under a broker-composed
`ProcessContentPolicy`. `ProcessContentSchema` retains the namespace, version, surface, exact JSON
schema, and digest of each registered extension. Workers cannot register Python models or select a
different policy. Returned policy objects are copies, so modifying a returned schema cannot mutate
the running boundary. Registration classes are trusted broker code, not executable worker input.

Core state fields retain their existing shape and historical digest calculation. Top-level artifact
references still require the separate reviewed evidence service. `memory_refs` and claim/hypothesis
evidence strings must name process IDs explicitly declared in that state's artifact list. Text that
names another process ID is denied too. A legacy storage reference cannot become a memory link.

New ordinary event payloads accept the closed public `summary`/`plan` shape, or an explicit
`{"schema_id": "namespace", "content": {...}}` envelope using an event schema registered by the
broker. Empty ordinary payloads remain valid. State extensions use their registered namespace as
the key and a closed record as the value. Fork interventions accept an explicit description, paired
control/treatment condition identities, an appropriate registered envelope, or an empty declaration.
The generated fork event's conditions, fork identity, and intervention have a separate closed shape.

The same namespace on a different surface grants no permission. Open objects, `Any`, unknown schemas,
remote/recursive schema references, and embedded storage-reference structures are denied in extension
definitions. Extensions link top-level process artifacts by admitted ID instead. Validation checks
that serializers have not changed the supplied canonical content. It does not rewrite the payload.

Traversal limits cover depth, nodes, string bytes, serialized state/event payload bytes, and digest
lookup candidates. Cycles, undeclared model fields, non-JSON values, and non-finite numbers are denied.
These are content bounds, not a complete fleet/resource accounting system.

Keys and string values are checked for process IDs and 64-hex digest candidates. Bounded indexed
lookups reject known raw/forensic or unclassified artifact digests, private classification digests,
and other known artifact digests without an explicitly declared process reference. A digest under
scientific study that does not identify stored material is not categorically forbidden. There is no
full forensic-corpus scan or automatic ingestion of raw traces into process content.

## Receipts and historical records

`process_content_admissions` retains immutable `ProcessContentAdmission` receipts keyed by record
kind and state/event ID. Each receipt binds the exact stored source digest, execution, complete
declared policy/digest, and admission time. The service verifies the source row and its rollout's
execution. Creation writes the receipt at the same declared time as the new record. The receipt is
separate from historical state/event envelopes, preserving their JSON and digests.

`ProcessStore` checks initial and resulting payloads, then creates state/event receipts with the
mutation and ownership in one transaction. Claims and relevant idempotent retries verify receipts,
current policy, source identity, references, and retained dependencies. A missing or corrupted receipt,
policy drift, or changed source denies new use and rolls back the lease or transition. Standalone
receipt writes also use savepoints. Worker-facing content failures and cancellations have fixed
messages with no private exception cause/context.

Migration `a84e61c39d20` adds an empty table; it does not backfill old records. `get_state` and `replay`
remain privileged reconstruction APIs. Neither an old schema version nor successful JSON parsing
admits legacy material. Routine writes do not silently upgrade old content into worker memory.

## Limits, failure, and rollback

The broker, schema classes, registry configuration, database, and artifact backend remain trusted.
The policy digest pins declared schemas, bounds, and the core-validator version tag; it does not
attest running code bytes, runtime identity, or the source of arbitrary text. Binding actual runtime
and workload identities remains a subsequent gate. Policy changes require deliberate versioning and
new admission; a mixed-policy experimental history is not evidence of matched conditions.

Structural validation and literal digest checks cannot prove that prose is free of private meaning,
encoded references, copied reasoning, or covert messages. Content must originate through the
normalized public worker/tool path or explicit reviewed evidence admission. This service does not
make a privileged broker object or the full historical event record suitable for worker hydration.
It also does not attest a filesystem, secret, network, GPU, or OS boundary.

Exact worker observations and training/Atlas products still require narrow projections. The existing
training compiler still includes full historical records; content receipts alone do not authorize
their training use. Communication authorization, authenticated identities, conserved resources,
forensic completeness, and concurrent GC are separate remaining requirements.

On failure, retain committed history and deny the new operation. Before rollback, disable new
consumers and preserve receipts, source records, and ownership; never replace missing receipts with
implicit approval or rewrite history. No production migration or destructive downgrade was run.
Tests in `tests/integration/test_process_content.py` retain synthetic nested-reference, schema,
serialization, receipt, scope, size, and rollback probes. Exact offline results are in the ledger.
