"""Minimal forward-only migration runner.

Applies backend/migrations/*.sql in filename order, each in its own transaction, recording
applied files in schema_migrations. Runs as the schema owner (DATABASE_MIGRATION_URL).
Usage: python -m intake.migrate
"""

import sys
from pathlib import Path

import psycopg

from intake.settings import get_settings

# Arbitrary constant: serializes concurrent deploys running migrations at the same time.
ADVISORY_LOCK_KEY = 7_204_312


def migrate(connection_string: str, migrations_dir: Path) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(connection_string, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                     filename   text PRIMARY KEY,
                     applied_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )
            done = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
            for path in sorted(migrations_dir.glob("*.sql")):
                if path.name in done:
                    continue
                try:
                    with conn.transaction():
                        conn.execute(path.read_text())
                        conn.execute(
                            "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                        )
                except psycopg.Error as error:
                    raise RuntimeError(f"Migration {path.name} failed") from error
                applied.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
    return applied


def main() -> int:
    settings = get_settings()
    applied = migrate(settings.database_migration_url, settings.migrations_dir)
    print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
