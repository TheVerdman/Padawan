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

## Retention and integrity

Normalized research records, provenance events, student states, and artifact metadata are
immutable. Raw-artifact retention may be configured only as an age threshold, and deletion is
permitted only when the artifact has no database reference. Garbage collection defaults to dry-run.
Backups must preserve the database and blob store together; provenance verification detects chain
tampering but does not restore missing blobs.

## Generated content and tools

The implemented algebra workflow does not execute student-generated code and does not expose its
SymPy grader as a student tool. Future executable environments must run untrusted output in a
separate sandbox with resource, network, filesystem, and secret isolation. Tool results must be raw
artifacts plus typed observations; a model's claim that a tool ran is not evidence.

## Operational review

Use TLS for remote PostgreSQL and model endpoints, restrict egress to configured providers, rotate
provider and export keys, and verify provenance before publishing a report. Review-required and
terminal failures must not be reclassified as success. The system does not currently implement
database row-level security, encrypted blob storage, S3, or a network service authentication layer;
operators must not infer those controls from the in-process policy hooks.
