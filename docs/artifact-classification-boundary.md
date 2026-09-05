# Artifact classification at the broker storage boundary

This is the storage-read checkpoint of the offline PPRL information-boundary stage. It does not
establish a worker sandbox, authenticated principals, process admission, or safe hydration. The
remaining work and execution authority are tracked in `pprl-execution-ledger.md`.

## Authoritative local metadata

`LocalArtifactStore` persists the complete canonical `ArtifactRef` at
`metadata/sha256/{prefix}/{digest}` alongside `blobs/sha256/{prefix}/{digest}`. All reference fields,
including explicit `restricted` and `raw_data` values, must match this broker-owned metadata.
Missing, malformed, noncanonical, or conflicting metadata denies access. Schema defaults cannot
silently classify an incomplete metadata record as public.

Ordinary reads deny both restricted and raw-only objects. The privileged `allow_restricted` option
does not bypass metadata matching or byte-integrity verification. It is an internal broker option,
not an authenticated worker permission or an attested filesystem boundary.

Publication and garbage collection use a POSIX advisory lock shared by cooperating store instances.
Publication records and flushes classification before atomically publishing blob bytes. An
interruption before blob publication may leave a classification reservation; an exact retry can
complete it, but a conflicting retry fails. No failed write returns an artifact reference.

GC retains classification tombstones after deleting unreferenced content, so writing identical bytes
later cannot downgrade their classification. Copying or backing up only blobs does not transfer
classification authority. Broker backups and restores must include metadata. Local records without
metadata are legacy/unclassified and fail closed, including ordinary attempts to rewrite identical
bytes. No automatic legacy reclassification or production-data migration is part of this change.

The storage lock serializes local file operations; it is not a database/GC transaction protocol and
does not make a stale external set of ownership pins safe. Atomic process ownership and coordinated
GC are separate stage-1 work. Direct modification of the broker-owned files or lock by an adversary
is outside this checkpoint's trusted-filesystem assumption.

## Catalog, GCS, and export

Catalog registration checks authoritative backend metadata even for an existing catalog row and
also compares URI identity. Caller metadata cannot override the backend's storage evidence.
GCS metadata admission revalidates the stored object rather than accepting a cached digest as
authority for caller-supplied flags. GCS reads also deny raw-only objects unless restricted access
is explicit. These paths are verified with a fake GCS client; no live bucket operation is authorized
or claimed.

`ExportPolicy` now requires the restricted-export opt-in and role for raw-only records as well as
records with the restricted flag. The policy remains a decision over supplied context; exporters
must use verified references and backend reads. Authenticating principals and preventing access to
broker credentials remain subsequent requirements.

## Evidence boundary

Regression fixtures exercise restart, classification forgery, conflicting duplicate and concurrent
writes, unknown legacy data, missing metadata fields, publication failures, recreation after GC,
catalog revalidation, GCS metadata tampering/cache bypass, and raw-only reads/exports. Inputs are
synthetic and remain in the tracked tests. Commands and outcomes are recorded in the execution ledger.

These results prove an application storage boundary under trusted broker storage. They do not prove
semantic classification of arbitrary text, removal of ambient channels, process/forensic service
isolation, reviewed evidence admission, or worker/training projection safety.
