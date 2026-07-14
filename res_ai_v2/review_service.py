from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, select

from .agent import run_until_event
from .db import get_engine
from .event_bus import publish_event
from .normalize import normalize_entity
from .pit_schema import pit_observations
from .review_policy import is_fundamental_decision, normalize_review_selection
from .review_queue import release_review_task, validate_review_lease
from .reviews import submit_review
from .schema import review_tasks
from .source_feedback import record_source_feedback


def _task_scope(task_id: int) -> dict[str, Any]:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                review_tasks.c.task_type,
                review_tasks.c.subject_type,
                review_tasks.c.subject_key,
                review_tasks.c.payload_json,
            ).where(review_tasks.c.id == task_id)
        ).first()
        if not row:
            return {"observation_ids": [], "task_type": "unknown"}
        payload = json.loads(str(row.payload_json) or "{}")
        observation_ids: set[int] = set()
        if payload.get("observation_id"):
            observation_ids.add(int(payload["observation_id"]))
        if row.subject_type == "observation" and str(row.subject_key).isdigit():
            observation_ids.add(int(row.subject_key))

        address = dict(payload.get("address") or {})
        if address.get("locality") or address.get("settlement") or address.get("territory"):
            keys = {
                key: normalize_entity(str(address.get(key, "")))
                for key in ("locality", "district", "settlement", "street")
            }
            conditions = [
                pit_observations.c.locality_key == keys["locality"],
                pit_observations.c.district_key == keys["district"],
                pit_observations.c.settlement_key == keys["settlement"],
                pit_observations.c.street_key == keys["street"],
            ]
            observation_ids.update(
                int(value)
                for value in conn.scalars(
                    select(pit_observations.c.id).where(and_(*conditions))
                )
            )

    return {
        "observation_ids": sorted(observation_ids),
        "task_type": str(row.task_type),
    }


def submit_review_and_update_agent(
    task_id: int,
    reviewer: str,
    selection: dict[str, Any],
    is_admin: bool,
    lease_token: str | None = None,
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    """Сохраняет ответ как директиву; рабочую базу изменяет только агент."""
    if lease_token:
        validate_review_lease(task_id, reviewer, lease_token)

    normalized_selection = normalize_review_selection(selection)
    scope = _task_scope(task_id)
    result = submit_review(task_id, reviewer, normalized_selection, is_admin)
    if not result.get("applied"):
        return result

    decision_id = int(result["decision_id"])
    record_source_feedback(
        scope["observation_ids"],
        normalized_selection["decision_type"],
    )
    full_rebuild = is_fundamental_decision(normalized_selection)
    event_id = publish_event(
        "human_confirmed",
        "review_decision",
        str(decision_id),
        {
            "task_id": task_id,
            "decision_id": decision_id,
            "reviewer": reviewer,
            "observation_ids": scope["observation_ids"],
            "force_full": full_rebuild,
            "task_type": scope["task_type"],
            "decision_type": normalized_selection["decision_type"],
        },
        deduplication_key=f"decision:{decision_id}",
    )
    if lease_token:
        release_review_task(task_id, reviewer, lease_token)

    if wait_for_agent:
        agent = run_until_event(event_id, max_events=500, worker_id="review-inline")
    else:
        agent = {
            "target_status": "queued",
            "processed": 0,
            "completed": 0,
            "failed": 0,
        }
    return {
        **result,
        "selection": normalized_selection,
        "recalculation_scope": "full" if full_rebuild else "local",
        "agent_event_id": event_id,
        "agent_status": agent["target_status"],
        "agent_processed": agent["processed"],
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
        },
    }
