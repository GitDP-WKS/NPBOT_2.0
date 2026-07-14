from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update

from .agent import run_until_event
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .event_bus import publish_event
from .importer import ImportPlan, canonical_row_key
from .normalize import row_hash, stable_json
from .pit_store import ingest_pit_rows, observation_key
from .repositories import audit
from .schema import source_files, source_rows
from .structure import canonical_executor

BATCH = 1000


def chunks(values: list[Any], size: int = BATCH):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def import_plan(
    plan: ImportPlan,
    actor: str = "Администратор",
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    """Сохраняет файл в яму и передает обработку агенту."""
    initialize_database()
    engine, now = get_engine(), utcnow()
    with engine.connect() as conn:
        if conn.execute(
            select(source_files.c.id).where(source_files.c.file_hash == plan.file_hash)
        ).first():
            return {
                "already_loaded": True,
                "seen": plan.detected_rows,
                "imported": 0,
                "duplicates": plan.detected_rows,
                "issues": 0,
                "analysis": None,
            }

    rows: list[dict[str, Any]] = []
    for sheet in plan.sheets:
        for source in sheet.rows:
            row = dict(source)
            row["branch"], row["res"], row["known_res"] = canonical_executor(
                row.get("branch"), row.get("res")
            )
            row["sheet_name"] = sheet.sheet_name
            row["canonical_hash"] = canonical_row_key(row)
            rows.append(row)

    with engine.begin() as conn:
        source_id = int(
            conn.execute(
                insert(source_files).values(
                    file_hash=plan.file_hash,
                    file_name=plan.file_name,
                    source_kind=plan.source_kind,
                    row_count=len(rows),
                    imported_rows=0,
                    status="importing",
                    imported_at=now,
                )
            ).inserted_primary_key[0]
        )
        raw_payload = [
            {
                "source_file_id": source_id,
                "sheet_name": str(row.get("sheet_name", "")),
                "row_number": int(row.get("row_number", 0)),
                "raw_json": stable_json(row.get("raw", {})),
                "raw_hash": row_hash(row.get("raw", {})),
                "canonical_hash": str(row["canonical_hash"]),
                "created_at": now,
            }
            for row in rows
        ]
        for batch in chunks(raw_payload):
            if batch:
                conn.execute(insert(source_rows), batch)

        source_row_ids = {
            (str(item.canonical_hash), int(item.row_number), str(item.sheet_name)): int(item.id)
            for item in conn.execute(
                select(
                    source_rows.c.id,
                    source_rows.c.canonical_hash,
                    source_rows.c.row_number,
                    source_rows.c.sheet_name,
                ).where(source_rows.c.source_file_id == source_id)
            )
        }
        pit = ingest_pit_rows(
            conn,
            source_file_id=source_id,
            rows=rows,
            source_row_ids=source_row_ids,
            now=now,
        )
        conn.execute(
            update(source_files)
            .where(source_files.c.id == source_id)
            .values(imported_rows=pit["observations"], status="imported")
        )

    bump_data_version()
    event_id = publish_event(
        "pit_ingested",
        "source_file",
        str(source_id),
        {
            "file_hash": plan.file_hash,
            "observation_ids": pit["observation_ids"],
            "occurrences": pit["occurrences"],
        },
        deduplication_key=plan.file_hash,
    )
    if wait_for_agent:
        agent = run_until_event(event_id, max_events=500, worker_id="import-inline")
        event_result = agent.get("target_result") or {}
        analysis = (event_result.get("result") or {}).get("analysis")
    else:
        agent = {
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "target_status": "queued",
        }
        analysis = None

    duplicate_rows = len(rows) - len({observation_key(row) for row in rows})
    audit(
        actor,
        "import_file",
        "source_file",
        plan.file_hash,
        {},
        {
            "file_name": plan.file_name,
            "rows": len(rows),
            "observations": pit["observations"],
            "duplicate_rows": duplicate_rows,
            "event_id": event_id,
        },
    )
    return {
        "already_loaded": False,
        "seen": len(rows),
        "imported": pit["observations"],
        "duplicates": duplicate_rows,
        "issues": int((analysis or {}).get("tasks_created", 0)),
        "text_examples": 0,
        "analysis": analysis,
        "pit": pit,
        "event_id": event_id,
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
            "target_status": agent["target_status"],
        },
    }
