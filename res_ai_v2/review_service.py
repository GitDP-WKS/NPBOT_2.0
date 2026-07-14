from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .agent import run_until_event
from .db import get_engine, initialize_database
from .event_bus import publish_event
from .reviews import submit_review
from .schema import address_mappings, review_decisions, review_tasks


def _affected_address_ids(task_id: int) -> list[int]:
    initialize_database()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(review_tasks.c.payload_json).where(review_tasks.c.id == task_id)
        ).first()
        if not row:
            return []
        payload = json.loads(str(row.payload_json) or "{}")
        mapping_ids: set[int] = set()
        if payload.get("mapping_id"):
            mapping_ids.add(int(payload["mapping_id"]))
        for option in payload.get("options") or []:
            if option.get("mapping_id"):
                mapping_ids.add(int(option["mapping_id"]))
        if not mapping_ids:
            return []
        return sorted(
            {
                int(value)
                for value in conn.scalars(
                    select(address_mappings.c.address_id).where(
                        address_mappings.c.id.in_(mapping_ids)
                    )
                )
            }
        )


def _active_decision_id(task_id: int) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(review_decisions.c.id)
            .where(
                review_decisions.c.task_id == task_id,
                review_decisions.c.active.is_(True),
            )
            .order_by(review_decisions.c.id.desc())
            .limit(1)
        ).first()
    if not row:
        raise RuntimeError("Принятое решение не найдено.")
    return int(row.id)


def submit_review_and_update_agent(
    task_id: int,
    reviewer: str,
    selection: dict[str, Any],
    is_admin: bool,
) -> dict[str, Any]:
    result = submit_review(task_id, reviewer, selection, is_admin)
    if not result.get("applied"):
        return result

    address_ids = _affected_address_ids(task_id)
    decision_id = _active_decision_id(task_id)
    event_id = publish_event(
        "human_confirmed",
        "review_decision",
        str(decision_id),
        {
            "task_id": task_id,
            "decision_id": decision_id,
            "address_ids": address_ids,
            "reviewer": reviewer,
        },
        deduplication_key=f"decision:{decision_id}",
    )
    agent = run_until_event(event_id, max_events=200, worker_id="review-inline")
    return {
        **result,
        "decision_id": decision_id,
        "agent_event_id": event_id,
        "agent_status": agent["target_status"],
        "agent_processed": agent["processed"],
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
        },
    }
