"""Create a fresh, seeded database for end-to-end tests or a clean local demo.

  python -m intake.devdb [--demo]

Drops and recreates the database named in DATABASE_MIGRATION_URL (refuses unless its name ends
in _e2e or _test), migrates it, seeds protocols, users and the gold set, and with --demo loads
the demo queue. Uses whatever LLM_BACKEND is configured (dev-oracle for tests).
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg

from intake.migrate import migrate
from intake.seed import load_demo_queue, owner_connection, seed_gold, seed_protocols, seed_users
from intake.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    parts = urlsplit(settings.database_migration_url)
    name = parts.path.lstrip("/")
    if not name.endswith(("_e2e", "_test")):
        print(f"Refusing to recreate {name!r}: name must end in _e2e or _test", file=sys.stderr)
        return 1
    admin = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    migrate(settings.database_migration_url, settings.migrations_dir)
    with owner_connection() as conn:
        asyncio.run(seed_protocols(conn))
        seed_users(conn)
        seed_gold(conn)
        conn.commit()
        if args.demo:
            asyncio.run(load_demo_queue(conn))
            conn.commit()
    print(f"Database {name} ready (backend={settings.llm_backend}, pid={os.getpid()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
