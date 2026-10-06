"""Shared API dependencies."""

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends
from psycopg import Connection

from intake import db


def get_conn() -> Iterator[Connection[dict[str, Any]]]:
    """One pooled connection per request; the request runs in one transaction."""
    with db.connection() as conn:
        yield conn


Conn = Annotated[Connection[dict[str, Any]], Depends(get_conn)]
