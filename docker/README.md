# Local service container

The root Compose file starts the production-target database and runs Alembic as a one-shot,
non-root migration container. It deliberately does not start an autonomous worker: a worker must
receive an explicit real student endpoint, model identifier, and teacher credential at runtime.

Use `docker compose up -d postgres`, then `docker compose run --rm migrate`. The checked-in password
is restricted to the loopback-bound local-development service and must not be reused elsewhere.
