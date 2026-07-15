from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update

from .agent import run_until_event
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .domain_ingest import reconcile_domain_evidence
from .event_bus import publish_event
from .importer import ImportPlan, canonical_row_key
from .normalize import row_hash, sha256_parts, stable_json
from .pit_store import ingest_pit_rows
from .repositories import audit
from .schema import source_files, source_rows
from .structure import canonical_executor
from .synchronization import agent_lock

BATCH = 1000


def chunks(values: list[Any], size: int = BATCH):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _already_loaded(plan: ImportPlan) -> dict[str, Any]:
    return {
        "already_loaded": True,
        "seen": plan.detected_rows,
        "imported": 0,
        "duplicates": plan.detected_rows,
        "technical_duplicates": plan.detected_rows,
        "independent_evidence": 0,
        "issues": 0,
        "analysis": None,
        "event_id": None,
    }


def _prepare_rows(plan: ImportPlan) -> list[dict[str, Any]]:
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
    return rows


def _store_plan(
    plan: ImportPlan,
    rows: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]] | None:
    engine = get_engine()
    lock_key = f"file-import:{sha256_parts([plan.file_hash])}"
    with agent_lock(
        lock_key,
        lease_seconds=900,
        wait_seconds=30.0,
        poll_seconds=0.03,
    ):
        now = utcnow()
        with engine.begin() as conn:
            if conn.execute(
                select(source_files.c.id).where(source_files.c.file_hash == plan.file_hash)
            ).first():
                return None

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
            domain = reconcile_domain_evidence(
                conn,
                source_file_id=source_id,
                file_hash=plan.file_hash,
                source_kind=plan.source_kind,
                rows=rows,
                source_row_ids=source_row_ids,
                now=now,
            )
            pit.update(domain)
            pit["occurrences"] = int(domain["accepted_evidence"])
            conn.execute(
                update(source_files)
                .where(source_files.c.id == source_id)
                .values(imported_rows=domain["accepted_evidence"], status="imported")
            )
        return source_id, pit


def _idle_agent() -> dict[str, Any]:
    return {
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "target_status": "not_required",
    }


def import_plan(
    plan: ImportPlan,
    actor: str = "Администратор",
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    """Сохраняет файл в яму и передает агенту только новые доказательства."""
    initialize_database()
    rows = _prepare_rows(plan)
    stored = _store_plan(plan, rows)
    if stored is None:
        return _already_loaded(plan)
    source_id, pit = stored

    accepted = int(pit.get("accepted_evidence", 0))
    event_id: int | None = None
    analysis: dict[str, Any] | None = None
    agent = _idle_agent()
    if accepted > 0:
        bump_data_version()
        event_id = publish_event(
            "pit_ingested",
            "source_file",
            str(source_id),
            {
                "file_hash": plan.file_hash,
                "observation_ids": pit["observation_ids"],
                "accepted_evidence": accepted,
                "independent_evidence": pit.get("independent_evidence", 0),
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

    duplicate_rows = int(pit.get("technical_duplicates", 0))
    audit(
        actor,
        "import_file",
        "source_file",
        plan.file_hash,
        {},
        {
            "file_name": plan.file_name,
            "rows": len(rows),
            "accepted_evidence": accepted,
            "independent_evidence": pit.get("independent_evidence", 0),
            "technical_duplicates": duplicate_rows,
            "event_id": event_id,
        },
    )
    return {
        "already_loaded": False,
        "seen": len(rows),
        "imported": accepted,
        "duplicates": duplicate_rows,
        "technical_duplicates": duplicate_rows,
        "independent_evidence": int(pit.get("independent_evidence", 0)),
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
