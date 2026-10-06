"""Database connection pool for the API and worker (connects as the app role)."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from intake.settings import get_settings

_pool: ConnectionPool[psycopg.Connection[dict[str, object]]] | None = None


def get_pool() -> ConnectionPool[psycopg.Connection[dict[str, object]]]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            connection_class=psycopg.Connection[dict[str, object]],
            open=True,
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection[dict[str, object]]]:
    """A pooled connection; the block runs in one transaction, committed on success."""
    with get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
