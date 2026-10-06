import psycopg
import pytest

from intake.settings import get_settings


def _database_reachable() -> bool:
    try:
        with psycopg.connect(get_settings().database_migration_url, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip database tests, with a clear reason, when the local Postgres is not running."""
    if any("db" in item.keywords for item in items) and not _database_reachable():
        skip = pytest.mark.skip(reason="Postgres not reachable: run `make db-up migrate`")
        for item in items:
            if "db" in item.keywords:
                item.add_marker(skip)
