from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select, update

from .db import get_engine, initialize_database, utcnow
from .knowledge_agent import rebuild_knowledge
from .normalize import normalize_entity
from .pit_bootstrap import bootstrap_current_knowledge
from .pit_schema import pit_observations
from .schema import address_mappings, addresses, review_tasks


def _address_rows(address_ids: list[int]) -> list[dict[str, Any]]:
    ids = sorted({int(value) for value in address_ids if int(value) > 0})
    if not ids:
        return []
    with get_engine().connect() as conn:
        return [
            dict(row._mapping)
            for row in conn.execute(select(addresses).where(addresses.c.id.in_(ids)))
        ]


def _matching_observation_ids(rows: list[dict[str, Any]]) -> list[int]:
    conditions = [
        and_(
            pit_observations.c.locality_key
            == normalize_entity(str(row.get("locality", ""))),
            pit_observations.c.district_key
            == normalize_entity(str(row.get("district", ""))),
            pit_observations.c.settlement_key
            == normalize_entity(str(row.get("settlement", ""))),
            pit_observations.c.street_key
            == normalize_entity(str(row.get("street", ""))),
        )
        for row in rows
    ]
    if not conditions:
        return []
    with get_engine().connect() as conn:
        return sorted(
            {
                int(value)
                for value in conn.scalars(
                    select(pit_observations.c.id).where(or_(*conditions))
                )
            }
        )


def _cancel_legacy_mapping_tasks(mapping_ids: set[str]) -> int:
    if not mapping_ids:
        return 0
    now = utcnow()
    with get_engine().begin() as conn:
        result = conn.execute(
            update(review_tasks)
            .where(
                review_tasks.c.status == "open",
                review_tasks.c.task_type == "missing_context",
                review_tasks.c.subject_type == "mapping",
                review_tasks.c.subject_key.in_(sorted(mapping_ids)),
            )
            .values(status="cancelled", updated_at=now)
        )
    return int(result.rowcount or 0)


def analyze_changed_addresses(address_ids: list[int]) -> dict[str, int]:
    """Совместимый вход: выборочный пересчет выполняет только новое ядро агента."""
    initialize_database()
    rows = _address_rows(address_ids)
    if not rows:
        return {
            "rows": 0,
            "consistent": 0,
            "conflicts": 0,
            "missing_context": 0,
            "tasks": 0,
            "stale_closed": 0,
        }

    with get_engine().begin() as conn:
        bootstrap_current_knowledge(conn, utcnow())
        old_mapping_ids = {
            str(value)
            for value in conn.scalars(
                select(address_mappings.c.id).where(
                    address_mappings.c.address_id.in_([int(row["id"]) for row in rows])
                )
            )
        }

    observation_ids = _matching_observation_ids(rows)
    if not observation_ids:
        return {
            "rows": 0,
            "consistent": 0,
            "conflicts": 0,
            "missing_context": 0,
            "tasks": 0,
            "stale_closed": _cancel_legacy_mapping_tasks(old_mapping_ids),
        }

    result = rebuild_knowledge(
        observation_ids=observation_ids,
        full_rebuild=False,
        trigger_type="compat_incremental",
        trigger_key=",".join(str(value) for value in sorted(address_ids)),
    )
    stale_closed = _cancel_legacy_mapping_tasks(old_mapping_ids)
    scanned = int(result.get("rows_scanned", 0))
    conflicts = int(result.get("conflicts", 0))
    return {
        "rows": scanned,
        "consistent": max(0, scanned - conflicts),
        "conflicts": conflicts,
        "missing_context": int(result.get("missing_context", 0)),
        "tasks": int(result.get("tasks_created", 0)),
        "stale_closed": stale_closed,
    }
