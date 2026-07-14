from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from .agent_monitor import agent_status
from .background_worker import background_worker_status
from .daily_audit import latest_daily_audit
from .db import get_engine, initialize_database
from .pit_schema import knowledge_generations, pit_observations, pit_occurrences
from .review_queue import active_leases
from .schema import address_mappings, addresses, review_tasks


def _count(conn, table, *conditions) -> int:
    return int(conn.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)


def system_overview() -> dict[str, Any]:
    initialize_database()
    with get_engine().connect() as conn:
        generation = conn.execute(
            select(knowledge_generations)
            .order_by(knowledge_generations.c.id.desc())
            .limit(1)
        ).first()
        generation_data = dict(generation._mapping) if generation else None
        if generation_data:
            generation_data["stats"] = json.loads(
                str(generation_data.pop("stats_json", "{}")) or "{}"
            )
        values = {
            "pit_observations": _count(conn, pit_observations),
            "pit_occurrences": _count(conn, pit_occurrences),
            "pit_new": _count(conn, pit_observations, pit_observations.c.state == "new"),
            "addresses": _count(conn, addresses),
            "mappings": _count(
                conn,
                address_mappings,
                address_mappings.c.active.is_(True),
            ),
            "open_tasks": _count(conn, review_tasks, review_tasks.c.status == "open"),
            "latest_generation": generation_data,
        }
    values["active_leases"] = active_leases()
    values["daily_audit"] = latest_daily_audit()
    values["agent"] = agent_status()
    values["worker"] = background_worker_status()
    return values
