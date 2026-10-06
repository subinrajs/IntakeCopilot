"""Test configuration.

Database tests run against a separate database (<name>_test) on the same server, created and
migrated once per session, so tests never touch development data. File storage goes to a
temporary directory. Tests needing Postgres are marked `db` and skipped if it is unreachable.
"""

import asyncio
import os
import tempfile
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from intake.settings import Settings


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


_base = Settings()
_db_name = urlsplit(_base.database_migration_url).path.lstrip("/")
TEST_DB = f"{_db_name}_test" if not _db_name.endswith("_test") else _db_name
os.environ["DATABASE_URL"] = _with_db(_base.database_url, TEST_DB)
os.environ["DATABASE_MIGRATION_URL"] = _with_db(_base.database_migration_url, TEST_DB)
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="intake-test-storage-")
os.environ["LLM_BACKEND"] = "dev-oracle"
os.environ["OPENAI_API_KEY"] = ""
os.environ["RUN_WORKER"] = "false"

from intake.settings import get_settings  # noqa: E402

get_settings.cache_clear()


def _server_reachable() -> bool:
    try:
        with psycopg.connect(_with_db(_base.database_migration_url, "postgres"), connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if any("db" in item.keywords for item in items) and not _server_reachable():
        skip = pytest.mark.skip(reason="Postgres not reachable: run `make db-up`")
        for item in items:
            if "db" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def test_database() -> Iterator[None]:
    """Fresh test database: migrated, with protocols (hash embeddings), users and gold cases."""
    from intake.migrate import migrate
    from intake.seed import seed_gold, seed_protocols, seed_users

    admin_url = _with_db(_base.database_migration_url, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    settings = get_settings()
    migrate(settings.database_migration_url, settings.migrations_dir)
    from intake.seed import owner_connection

    with owner_connection() as conn:
        asyncio.run(seed_protocols(conn))
        seed_users(conn)
        seed_gold(conn)
        conn.commit()
    yield
    from intake import db

    db.close_pool()


@pytest.fixture
def db_ready(test_database: None) -> None:
    """Depend on this in any test that needs the seeded database."""


@pytest.fixture
def owner_conn(test_database: None) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    from intake.seed import owner_connection

    with owner_connection() as conn:
        yield conn
