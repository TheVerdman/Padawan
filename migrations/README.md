# Database migrations

Alembic owns the production schema. `PADAWAN_DATABASE_URL` selects the target;
SQLite is suitable for local development, while PostgreSQL is the concurrency
authority. Every revision must remain upgradeable from an empty database.
