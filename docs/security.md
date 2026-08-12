# Security and data handling

Padawan treats prompts, raw model traffic, private reasoning, tool observations, and provider
metadata as sensitive research data even when inference runs locally.

## Secrets

Credentials enter only at the composition boundary and become HTTP headers inside provider
clients. Generation request artifacts do not contain authorization headers. Settings use
`SecretStr`; command manifests omit all provider keys, redact database passwords, and record only
whether each credential is configured. Known values and common bearer/key/token/password patterns
are redacted from manifest text and failure provenance.

Never pass credentials in model prompts, item metadata, command-line arguments, or provenance
payloads. `.env` and runtime artifact directories are ignored by Git. Production deployments should
inject secrets from a dedicated manager and use separate least-privilege database/provider
identities.
Provider base URLs should not contain userinfo. Research-control endpoint identity nevertheless
reconstructs only the parsed scheme, hostname, and port, so an accidental URL username/password is
not persisted in execution records.

`PADAWAN_ENV_FILE` is a bootstrap-only, explicit selection. Padawan does not scan sibling
repositories, and the selected path is not written into settings manifests. Process environment
variables win over dotenv values. Broad file permissions warn in development and fail in
production. Provider values are neither copied into this repository nor hashed as identifiers.

## Artifact classification and access

Raw external requests/responses and private traces are stored as `restricted=true, raw_data=true`.
Local files are SHA-256 addressed; restricted new blobs receive mode `0600`. Reads deny restricted
references unless the caller explicitly opts in and always verify URI, digest, and size.

`ExportPolicy` requires an allowed purpose plus explicit roles for restricted or private-reasoning
material. The default policy denies both. Paths are normalized as safe relative POSIX paths and
export size is bounded. Heirloom audit identifiers are HMAC-derived so internal identifiers are not
directly disclosed.

The local store is an application control, not a multi-tenant security boundary. It does not encrypt
at rest or authenticate operating-system users. Put its root on encrypted storage with restrictive
directory ownership when traces are sensitive.

The GCS backend uses Application Default Credentials, create-only generations, CRC32C, and
generation-pinned reads. Existing objects are deduplicated only after immutable metadata and size
validation, and all returned bytes are rehashed with SHA-256. Bucket/object/generation/checksum
metadata is evidence, not a credential. Restricted/raw classification cannot be downgraded by a
second writer. Cloud IAM, retention policy, encryption, audit logging, and lifecycle rules remain
operator controls outside Padawan.

Future citator reports are restricted legal-research evidence. An adapter may retain raw provider
material only when the subscription permits automated access and internal retention; otherwise the
result cannot satisfy the automated audit boundary and remains unknown or enters governed human
review. Commercial UI scraping is out of scope. Citator credentials follow the same composition-
boundary and secret-manager rules as model credentials and never enter prompts, court packs, or
the repository.

## Retention and integrity

Normalized research records, provenance events, student states, and artifact metadata are
immutable. Raw-artifact retention may be configured only as an age threshold, and deletion is
permitted only when the artifact has no database reference. Garbage collection defaults to dry-run.
Backups must preserve the database and blob store together; provenance verification detects chain
tampering but does not restore missing blobs.

## Generated content and tools

The algebra workflow does not execute student-generated code and does not expose its SymPy grader
as a student tool. Lean mathematics is the first executable verifier boundary. It fixes imports and
the theorem declaration, admits only a tactic term beginning with `by`, rejects command,
metaprogramming, IO, and markdown/comment escape surfaces, and supplies a minimal environment with
no provider credentials. The pinned Lean binary runs with wall/CPU/output/file-descriptor limits.

On macOS, required `sandbox-exec` policy denies network access, confines writes to a unique
per-proof directory, and denies common credential/key directories. Candidate execution invokes
Lean directly with a pre-resolved pinned `LEAN_PATH`; it cannot ask Lake to update dependencies.
macOS does not enforce the configured address-space rlimit, so results explicitly record that
limitation. A missing required sandbox, timeout, signal, output overflow, or dependency drift is an
infrastructure failure—not an incorrect proof. Only Lean-kernel exit success produces `verified`.
Other executable domains still require their own independent containment and authorization model.

Magellan Improvement therefore does not execute a sibling checkout directly. Its read-only
inspector hashes the Git revision, binary tracked diff, bounded non-sensitive untracked sources,
dependency manifests, and migrations without serializing the local root. Untracked env, key, and
credential-like files are excluded without reading. A valid handshake requires content-addressed
source materialization, allowlisted environment-only secret injection, PostgreSQL world isolation,
verified reset and matched-world independence, restricted networking, blocked or recorded external
effects, the Responses protocol, enforced tenant identity, durable idempotency, and validators on
every mutating tool. Captured traces reject cross-tenant/user context, caller-declared approval
bypass, unapproved regulated mutation, read/propose mutation, broken provenance/state chains, and
non-idempotent replay.

## Operational review

Use TLS for remote PostgreSQL and model endpoints, restrict egress to configured providers, rotate
provider and export keys, and verify provenance before publishing a report. Review-required and
terminal failures must not be reclassified as success. The system does not currently implement
database row-level security, application-managed blob encryption, S3, or a network service
authentication layer; operators must not infer those controls from the in-process policy hooks.
