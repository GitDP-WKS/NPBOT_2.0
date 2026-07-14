from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update

from .db import bump_data_version, get_engine, get_setting, initialize_database, utcnow
from .knowledge_plan import build_knowledge_plan
from .knowledge_writer import write_knowledge
from .normalize import stable_json
from .pit_schema import knowledge_directives, knowledge_generations, pit_observations
from .pit_store import load_observations
from .schema import review_tasks
from .synchronization import agent_lock

KNOWLEDGE_LOCK = "knowledge-rebuild"


def _load_scope(observation_ids: list[int] | None) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        if observation_ids is None:
            return load_observations(conn)
        changed = load_observations(conn, observation_ids)
        if not changed:
            return []
        localities = {
            str(row.get("locality_key", ""))
            for row in changed
            if row.get("locality_key")
        }
        settlements = {
            str(row.get("settlement_key", ""))
            for row in changed
            if row.get("settlement_key")
        }
        conditions = []
        if localities:
            conditions.append(pit_observations.c.locality_key.in_(localities))
        if settlements:
            conditions.append(pit_observations.c.settlement_key.in_(settlements))
        if not conditions:
            return changed
        return [
            dict(row._mapping)
            for row in conn.execute(select(pit_observations).where(or_(*conditions)))
        ]


def _load_directives() -> dict[str, dict[str, Any]]:
    query = (
        select(
            knowledge_directives,
            review_tasks.c.task_key,
            review_tasks.c.task_type,
            review_tasks.c.subject_type.label("task_subject_type"),
            review_tasks.c.subject_key.label("task_subject_key"),
            review_tasks.c.title,
            review_tasks.c.payload_json,
            review_tasks.c.priority,
        )
        .select_from(
            knowledge_directives.join(
                review_tasks,
                knowledge_directives.c.task_id == review_tasks.c.id,
            )
        )
        .where(knowledge_directives.c.active.is_(True))
        .order_by(knowledge_directives.c.id)
    )
    result: dict[str, dict[str, Any]] = {}
    with get_engine().connect() as conn:
        for row in conn.execute(query):
            item = dict(row._mapping)
            item["selection"] = json.loads(str(item["selection_json"]) or "{}")
            result[str(item["task_key"])] = item
    return result


def _start_generation(trigger_type: str, trigger_key: str, full_rebuild: bool) -> int:
    now = utcnow()
    with get_engine().begin() as conn:
        return int(
            conn.execute(
                knowledge_generations.insert().values(
                    generation_key=uuid4().hex,
                    status="building",
                    trigger_type=trigger_type,
                    trigger_key=trigger_key,
                    source_version=int(get_setting("data_version", "1")),
                    full_rebuild=full_rebuild,
                    rows_scanned=0,
                    rows_changed=0,
                    tasks_created=0,
                    stats_json="{}",
                    started_at=now,
                    finished_at=None,
                )
            ).inserted_primary_key[0]
        )


def _finish_generation(generation_id: int, status: str, stats: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(knowledge_generations)
            .where(knowledge_generations.c.id == generation_id)
            .values(
                status=status,
                rows_scanned=int(stats.get("rows_scanned", 0)),
                rows_changed=int(stats.get("rows_changed", 0)),
                tasks_created=int(stats.get("tasks_created", 0)),
                stats_json=stable_json(stats),
                finished_at=utcnow(),
            )
        )


def _rebuild_locked(
    observation_ids: list[int] | None,
    full_rebuild: bool,
    trigger_type: str,
    trigger_key: str,
) -> dict[str, Any]:
    generation_id = _start_generation(trigger_type, trigger_key, full_rebuild)
    try:
        rows = _load_scope(None if full_rebuild else observation_ids)
        directives = _load_directives()
        current_version = int(get_setting("data_version", "1"))
        plan = build_knowledge_plan(rows, directives, current_version)
        write_result = write_knowledge(
            plan,
            directives,
            full_rebuild=full_rebuild,
        )
        bump_data_version()
        stats = {
            "generation_id": generation_id,
            "rows_scanned": len(plan.rows),
            "rows_changed": len(plan.mappings) + write_result["directives_applied"],
            "tasks_created": len(plan.keep_keys),
            "conflicts": sum(task["task_type"] == "mapping_conflict" for task in plan.tasks),
            "duplicates": sum(
                task["task_type"] == "duplicate_observation" for task in plan.tasks
            ),
            "missing_context": sum(
                task["task_type"] == "missing_context" for task in plan.tasks
            ),
            "invalid": sum(task["task_type"] == "import_issue" for task in plan.tasks),
            "directive_challenges": sum(
                task["task_type"] == "directive_challenge" for task in plan.tasks
            ),
            "directives_applied": write_result["directives_applied"],
            "tasks_inserted": write_result["tasks_inserted"],
            "tasks_updated": write_result["tasks_updated"],
            "stale_closed": write_result["tasks_closed"],
            "full_rebuild": full_rebuild,
        }
        _finish_generation(generation_id, "completed", stats)
        return stats
    except Exception as exc:
        _finish_generation(
            generation_id,
            "failed",
            {
                "rows_scanned": 0,
                "rows_changed": 0,
                "tasks_created": 0,
                "error": str(exc),
            },
        )
        raise


def rebuild_knowledge(
    *,
    observation_ids: list[int] | None = None,
    full_rebuild: bool = False,
    trigger_type: str = "agent",
    trigger_key: str = "automatic",
) -> dict[str, Any]:
    """Единственная синхронизированная точка формирования рабочей базы знаний."""
    initialize_database()
    with agent_lock(
        KNOWLEDGE_LOCK,
        lease_seconds=1800,
        wait_seconds=0.0,
    ):
        return _rebuild_locked(
            observation_ids,
            full_rebuild,
            trigger_type,
            trigger_key,
        )
