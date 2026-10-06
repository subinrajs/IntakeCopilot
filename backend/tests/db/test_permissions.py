"""The app role can append to the trails but never rewrite them (design doc: audit_log)."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from intake.api.app import app
from intake.settings import get_settings

pytestmark = pytest.mark.db


@pytest.fixture
def app_conn(db_ready: None) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(get_settings().database_url) as conn:
        yield conn
        conn.rollback()


def test_app_role_can_insert_audit_rows(app_conn: psycopg.Connection[tuple[object, ...]]) -> None:
    row = app_conn.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id) "
        "VALUES ('test', 'probe', 'test', %s) RETURNING id",
        (str(uuid.uuid4()),),
    ).fetchone()
    assert row is not None


@pytest.mark.parametrize("table", ["audit_log", "pipeline_steps", "decisions", "field_corrections"])
@pytest.mark.parametrize("statement", ["UPDATE {t} SET id = id", "DELETE FROM {t}"])
def test_app_role_cannot_rewrite_trails(
    app_conn: psycopg.Connection[tuple[object, ...]], table: str, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(statement.format(t=table))


def test_app_role_cannot_read_migration_bookkeeping(
    app_conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("SELECT * FROM schema_migrations")


def test_override_without_reason_is_rejected_by_the_database(
    app_conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    case_id = uuid.uuid4()
    app_conn.execute(
        "INSERT INTO requisitions (id, file_key, file_sha256, content_type, uploaded_by) "
        "VALUES (%s, 'k', 'h', 'application/pdf', 'test')",
        (case_id,),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        app_conn.execute(
            "INSERT INTO decisions (requisition_id, kind, action, reason, decided_by) "
            "VALUES (%s, 'priority', 'override', '  ', 'test')",
            (case_id,),
        )


def test_readyz_reaches_the_database(db_ready: None) -> None:
    with TestClient(app) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
