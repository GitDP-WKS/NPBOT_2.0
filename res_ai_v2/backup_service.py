from __future__ import annotations

import base64
import json
from typing import Any

from sqlalchemy import insert, select

from .agent import run_until_event
from .db import get_engine, initialize_database, utcnow
from .domain_schema import (
    canonical_observations,
    conditional_rules,
    evidence_claims,
    mapping_explanations,
    recalculation_log,
    source_evidence,
    source_quality_history,
    source_registries,
)
from .event_bus import publish_event
from .event_schema import agent_effects, agent_events, agent_runs
from .pit_schema import (
    agent_daily_runs,
    agent_locks,
    knowledge_directives,
    knowledge_generations,
    pit_observations,
    pit_occurrences,
    review_task_leases,
)
from .restore_schema import restore_requests
from .schema import (
    PREFIX,
    address_aliases,
    address_mappings,
    addresses,
    audit_events,
    executor_structure,
    mapping_evidence,
    model_versions,
    prediction_events,
    query_rules,
    review_decisions,
    review_tasks,
    review_votes,
    schema_migrations,
    settings,
    source_files,
    source_rows,
    text_examples,
)

TABLES = [
    schema_migrations,
    settings,
    executor_structure,
    source_files,
    source_rows,
    pit_observations,
    pit_occurrences,
    canonical_observations,
    source_registries,
    evidence_claims,
    source_evidence,
    source_quality_history,
    knowledge_directives,
    knowledge_generations,
    conditional_rules,
    recalculation_log,
    agent_daily_runs,
    addresses,
    address_aliases,
    address_mappings,
    mapping_evidence,
    mapping_explanations,
    text_examples,
    query_rules,
    review_tasks,
    review_task_leases,
    review_votes,
    review_decisions,
    model_versions,
    prediction_events,
    audit_events,
    agent_events,
    agent_runs,
    agent_effects,
    agent_locks,
]


def backup_snapshot() -> str:
    initialize_database()
    data: dict[str, Any] = {
        "format": "res_ai_v3_full_backup",
        "created_at": utcnow().isoformat(),
        "tables": {},
    }
    with get_engine().connect() as conn:
        for table in TABLES:
            rows = [dict(row._mapping) for row in conn.execute(select(table))]
            if table is model_versions:
                for row in rows:
                    blob = row.get("model_blob")
                    if blob is not None:
                        row["model_blob"] = base64.b64encode(bytes(blob)).decode("ascii")
            data["tables"][table.name.removeprefix(PREFIX)] = rows
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def inspect_backup(snapshot_json: str) -> dict[str, Any]:
    payload = json.loads(snapshot_json)
    if payload.get("format") != "res_ai_v3_full_backup":
        raise ValueError("Неподдерживаемый формат резервной копии.")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("В резервной копии отсутствуют таблицы.")
    return {
        "format": payload["format"],
        "created_at": str(payload.get("created_at", "")),
        "table_count": len(tables),
        "row_count": sum(len(rows) for rows in tables.values() if isinstance(rows, list)),
    }


def request_restore(
    snapshot_json: str,
    actor: str = "Администратор",
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    initialize_database()
    info = inspect_backup(snapshot_json)
    now = utcnow()
    with get_engine().begin() as conn:
        request_id = int(
            conn.execute(
                insert(restore_requests).values(
                    actor=actor,
                    snapshot_json=snapshot_json,
                    status="pending",
                    result_json="{}",
                    error_text="",
                    created_at=now,
                    finished_at=None,
                )
            ).inserted_primary_key[0]
        )
    event_id = publish_event(
        "restore_requested",
        "restore_request",
        str(request_id),
        {"request_id": request_id, "actor": actor},
        deduplication_key=f"restore:{request_id}",
    )
    if wait_for_agent:
        agent = run_until_event(event_id, max_events=1000, worker_id="restore-inline")
    else:
        agent = {
            "target_status": "queued",
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "target_result": None,
        }
    return {
        "request_id": request_id,
        "event_id": event_id,
        "status": agent["target_status"],
        "backup": info,
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
        },
        "result": agent.get("target_result"),
    }
