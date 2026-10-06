"""Load reference data and demo content. Runs as the schema owner.

  python -m intake.seed           protocols (+ embeddings), demo users, gold set import
  python -m intake.seed --demo    the above, then reset live cases and load a demo queue

The demo reset deletes live cases and their history (including their audit rows). It is meant
for the public demo, which holds synthetic data only, and refuses to run unless
SEED_ALLOW_RESET=1.
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from intake.auth import hash_password
from intake.eval.gold import load_gold_set
from intake.llm.factory import make_llm
from intake.pipeline.orchestrator import PipelineDeps, file_sha256, process
from intake.protocols import load_protocol_book
from intake.retrieval import vector_literal
from intake.settings import get_settings
from intake.storage import get_storage

GOLD_NAMESPACE = uuid.UUID("5b0c9d6e-3f1a-4b8e-9c2d-7a6e1f0b2c3d")
DEMO_USERS = (
    ("intake", "Ines Okafor (intake)", "intake"),
    ("radiologist", "Dr. Priya Raman (radiologist)", "radiologist"),
    ("radiologist2", "Dr. Tomas Lindqvist (radiologist)", "radiologist"),
    ("admin", "Morgan Ellis (admin)", "admin"),
)
# Gold cases copied into the live queue for the demo, with how long ago each "arrived".
DEMO_CASES: tuple[tuple[str, timedelta], ...] = (
    ("gold-v1-041", timedelta(hours=2)),  # buried cauda equina red flag (P1)
    ("gold-v1-054", timedelta(minutes=20)),  # heavy fax, cord compression (P1)
    ("gold-v1-017", timedelta(hours=1, minutes=30)),  # TIA (P1)
    ("gold-v1-045", timedelta(hours=5)),  # eGFR 25 with contrast
    ("gold-v1-044", timedelta(hours=26)),  # missing eGFR (needs labs)
    ("gold-v1-046", timedelta(hours=3)),  # metformin + CT contrast
    ("gold-v1-049", timedelta(hours=8)),  # prompt injection attempt
    ("gold-v1-050", timedelta(days=2)),  # physician-marked urgent, rubric P3
    ("gold-v1-056", timedelta(hours=6)),  # two pages
    ("gold-v1-003", timedelta(days=3)),
    ("gold-v1-011", timedelta(hours=30)),
    ("gold-v1-022", timedelta(days=8)),
    ("gold-v1-030", timedelta(days=16)),
    ("gold-v1-036", timedelta(days=1)),
)


def owner_connection() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(get_settings().database_migration_url, row_factory=dict_row)


async def seed_protocols(conn: psycopg.Connection[dict[str, Any]]) -> int:
    settings = get_settings()
    book = load_protocol_book(settings.data_dir / "protocols.yaml")
    vectors, _ = await make_llm().embed([p.retrieval_document() for p in book.protocols])
    for protocol, vector in zip(book.protocols, vectors, strict=True):
        conn.execute(
            """INSERT INTO protocols
                 (id, modality, body_part, name, contrast, indications, slot_minutes, embedding,
                  version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
               ON CONFLICT (id) DO UPDATE SET
                 modality = EXCLUDED.modality, body_part = EXCLUDED.body_part,
                 name = EXCLUDED.name, contrast = EXCLUDED.contrast,
                 indications = EXCLUDED.indications, slot_minutes = EXCLUDED.slot_minutes,
                 embedding = EXCLUDED.embedding, updated_at = now()""",
            (
                protocol.id,
                protocol.modality,
                protocol.body_part,
                protocol.name,
                protocol.contrast,
                "; ".join(protocol.indications),
                protocol.slot_minutes,
                vector_literal(vector),
                book.version,
            ),
        )
    return len(book.protocols)


def seed_users(conn: psycopg.Connection[dict[str, Any]]) -> None:
    password = get_settings().seed_staff_password
    for user_id, name, role in DEMO_USERS:
        conn.execute(
            """INSERT INTO users (id, display_name, role, password_hash) VALUES (%s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name,
                 role = EXCLUDED.role, password_hash = EXCLUDED.password_hash""",
            (user_id, name, role, hash_password(password)),
        )


def gold_requisition_id(case_key: str) -> uuid.UUID:
    return uuid.uuid5(GOLD_NAMESPACE, case_key)


def seed_gold(conn: psycopg.Connection[dict[str, Any]], version: str = "v1") -> int:
    settings = get_settings()
    storage = get_storage()
    gold = load_gold_set(settings.data_dir, version)
    gold_root = settings.data_dir / "gold" / version
    for case in gold.cases:
        data = (gold_root / case.file).read_bytes()
        key = f"gold/{version}/{Path(case.file).name}"
        storage.put(key, data)
        case_id = gold_requisition_id(case.case_key)
        conn.execute(
            """INSERT INTO requisitions
                 (id, source, file_key, file_sha256, content_type, original_filename,
                  uploaded_by, status)
               VALUES (%s, 'gold', %s, %s, 'application/pdf', %s, 'system', 'uploaded')
               ON CONFLICT (id) DO NOTHING""",
            (case_id, key, file_sha256(data), Path(case.file).name),
        )
        conn.execute(
            """INSERT INTO eval_cases
                 (requisition_id, case_key, gold_version, gold_fields, gold_priority,
                  gold_protocol_id, gold_contrast_flags, as_of, difficulty, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (requisition_id) DO UPDATE SET
                 gold_fields = EXCLUDED.gold_fields, gold_priority = EXCLUDED.gold_priority,
                 gold_protocol_id = EXCLUDED.gold_protocol_id,
                 gold_contrast_flags = EXCLUDED.gold_contrast_flags,
                 as_of = EXCLUDED.as_of, difficulty = EXCLUDED.difficulty,
                 notes = EXCLUDED.notes""",
            (
                case_id,
                case.case_key,
                version,
                Jsonb(case.fields.model_dump(mode="json")),
                case.priority,
                case.protocol_id,
                Jsonb(case.contrast_flags),
                case.as_of,
                case.difficulty,
                case.notes,
            ),
        )
    return len(gold.cases)


def reset_live_cases(conn: psycopg.Connection[dict[str, Any]]) -> None:
    live = "SELECT id FROM requisitions WHERE source = 'live'"
    for table in ("pipeline_steps", "decisions", "field_corrections", "requisition_pages"):
        conn.execute(f"DELETE FROM {table} WHERE requisition_id IN ({live})")
    conn.execute(
        "DELETE FROM audit_log WHERE entity = 'requisition' AND entity_id IN "
        "(SELECT id::text FROM requisitions WHERE source = 'live')"
    )
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM requisitions WHERE source = 'live'")


def import_eval_snapshots(conn: psycopg.Connection[dict[str, Any]]) -> int:
    """Precomputed eval runs (data/eval_runs/*.json) so the dashboard is populated at once."""
    count = 0
    for path in sorted((get_settings().data_dir / "eval_runs").glob("*.json")):
        run = json.loads(path.read_text())
        conn.execute(
            """INSERT INTO eval_runs (id, gold_version, prompt_versions, models, label, started_by,
                 started_at, finished_at, status, metrics, per_case)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'done', %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                run["id"],
                run["gold_version"],
                Jsonb(run["prompt_versions"]),
                Jsonb(run["models"]),
                run.get("label"),
                run["started_by"],
                run["started_at"],
                run["finished_at"],
                Jsonb(run["metrics"]),
                Jsonb(run["per_case"]),
            ),
        )
        count += 1
    return count


async def load_demo_queue(conn: psycopg.Connection[dict[str, Any]]) -> list[uuid.UUID]:
    """Copy selected gold PDFs in as live uploads, then process them now."""
    settings = get_settings()
    storage = get_storage()
    gold = {c.case_key: c for c in load_gold_set(settings.data_dir, "v1").cases}
    now = datetime.now(UTC)
    ids: list[uuid.UUID] = []
    for case_key, age in DEMO_CASES:
        data = (settings.data_dir / "gold" / "v1" / gold[case_key].file).read_bytes()
        case_id = uuid.uuid4()
        key = f"requisitions/{case_id}/original.pdf"
        storage.put(key, data)
        conn.execute(
            """INSERT INTO requisitions (id, file_key, file_sha256, content_type,
                 original_filename, uploaded_by, uploaded_at, status_changed_at)
               VALUES (%s, %s, %s, 'application/pdf', %s, 'intake', %s, %s)""",
            (case_id, key, file_sha256(data), f"fax-{case_key[-3:]}.pdf", now - age, now - age),
        )
        ids.append(case_id)
    conn.commit()
    deps = PipelineDeps.create(make_llm())
    for case_id in ids:
        await process(case_id, deps)
    # Processing stamps status_changed_at; restore the arrival times so queue ages look real.
    for (_, age), case_id in zip(DEMO_CASES, ids, strict=True):
        conn.execute(
            "UPDATE requisitions SET status_changed_at = %s WHERE id = %s", (now - age, case_id)
        )
    return ids


async def main_async(demo: bool) -> int:
    with owner_connection() as conn:
        protocols = await seed_protocols(conn)
        seed_users(conn)
        gold = seed_gold(conn)
        conn.commit()
        print(f"Seeded {protocols} protocols, {len(DEMO_USERS)} users, {gold} gold cases")
        if demo:
            if os.environ.get("SEED_ALLOW_RESET") != "1":
                print("Refusing to reset live cases without SEED_ALLOW_RESET=1", file=sys.stderr)
                return 1
            reset_live_cases(conn)
            snapshots = import_eval_snapshots(conn)
            conn.commit()
            ids = await load_demo_queue(conn)
            conn.commit()
            print(f"Demo: {len(ids)} live cases processed, {snapshots} eval run snapshots")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.demo))


if __name__ == "__main__":
    sys.exit(main())
