from __future__ import annotations

import base64
import json
from typing import Any

from sqlalchemy import select

from .db import get_engine, initialize_database
from .event_schema import agent_effects, agent_events, agent_runs
from .pit_schema import (
    agent_daily_runs,
    knowledge_directives,
    knowledge_generations,
    pit_observations,
    pit_occurrences,
    review_task_leases,
)
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
    knowledge_directives,
    knowledge_generations,
    agent_daily_runs,
    addresses,
    address_aliases,
    address_mappings,
    mapping_evidence,
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
]


def backup_snapshot() -> str:
    initialize_database()
    data: dict[str, Any] = {
        "format": "res_ai_v3_full_backup",
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
