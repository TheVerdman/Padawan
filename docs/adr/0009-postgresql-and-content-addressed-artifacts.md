# ADR 0009: PostgreSQL plus content-addressed artifact storage

- Status: Accepted
- Date: 2026-08-01

## Context

Workers need transactional leases and forks, while raw provider evidence is too large and sensitive
to treat as ad hoc JSON fields.

## Decision

PostgreSQL is the production concurrency target, with SQLAlchemy 2 and Alembic. SQLite supports
local/test semantics. Normalized records and references live in SQL; raw bytes live in an atomic,
verified SHA-256 filesystem store. PostgreSQL claims use `SKIP LOCKED`.

## Consequences

Database and artifact backups must be coordinated. Local storage is complete but not distributed;
an S3-compatible backend is deferred until it can be implemented and tested as a real backend.
