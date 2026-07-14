from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import bindparam, insert, select, update
from sqlalchemy.engine import Connection

from .db import get_engine, initialize_database, utcnow
from .normalize import stable_json
from .schema import review_tasks

BATCH_SIZE = 1000


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def sync_review_tasks_in_connection(
    conn: Connection,
    tasks: list[dict[str, Any]],
    *,
    task_types: Iterable[str],
    keep_keys: set[str],
    close_stale: bool,
) -> dict[str, int]:
    now = utcnow()
    unique = {str(task["task_key"]): task for task in tasks}
    keys = list(unique)
    inserted_count = updated_count = closed_count = 0

    existing: dict[str, int] = {}
    for batch in _chunks(keys):
        existing.update(
            {
                str(row.task_key): int(row.id)
                for row in conn.execute(
                    select(review_tasks.c.id, review_tasks.c.task_key).where(
                        review_tasks.c.task_key.in_(batch)
                    )
                )
            }
        )

    inserts = []
    updates = []
    for key, task in unique.items():
        values = {
            "task_type": str(task["task_type"]),
            "subject_type": str(task["subject_type"]),
            "subject_key": str(task["subject_key"]),
            "title": str(task["title"]),
            "payload_json": stable_json(task.get("payload") or {}),
            "priority": int(task.get("priority", 0)),
            "status": "open",
            "updated_at": now,
        }
        if key in existing:
            updates.append(
                {
                    "b_id": existing[key],
                    **{f"b_{name}": value for name, value in values.items()},
                }
            )
        else:
            inserts.append({"task_key": key, "created_at": now, **values})

    for batch in _chunks(inserts):
        if batch:
            conn.execute(insert(review_tasks), batch)
            inserted_count += len(batch)

    if updates:
        statement = (
            update(review_tasks)
            .where(review_tasks.c.id == bindparam("b_id"))
            .values(
                task_type=bindparam("b_task_type"),
                subject_type=bindparam("b_subject_type"),
                subject_key=bindparam("b_subject_key"),
                title=bindparam("b_title"),
                payload_json=bindparam("b_payload_json"),
                priority=bindparam("b_priority"),
                status=bindparam("b_status"),
                updated_at=bindparam("b_updated_at"),
            )
        )
        for batch in _chunks(updates):
            conn.execute(statement, batch)
            updated_count += len(batch)

    if close_stale:
        query = select(review_tasks.c.id, review_tasks.c.task_key).where(
            review_tasks.c.status == "open",
            review_tasks.c.task_type.in_(list(task_types)),
        )
        stale_ids = [
            int(row.id)
            for row in conn.execute(query)
            if str(row.task_key) not in keep_keys
        ]
        for batch in _chunks(stale_ids):
            if batch:
                result = conn.execute(
                    update(review_tasks)
                    .where(review_tasks.c.id.in_(batch))
                    .values(status="cancelled", updated_at=now)
                )
                closed_count += int(result.rowcount or 0)

    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "closed": closed_count,
    }


def sync_review_tasks(
    tasks: list[dict[str, Any]],
    *,
    task_types: Iterable[str],
    keep_keys: set[str],
    close_stale: bool,
) -> dict[str, int]:
    initialize_database()
    with get_engine().begin() as conn:
        return sync_review_tasks_in_connection(
            conn,
            tasks,
            task_types=task_types,
            keep_keys=keep_keys,
            close_stale=close_stale,
        )
