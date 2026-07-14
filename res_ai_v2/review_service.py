from __future__ import annotations

from typing import Any

from .agent import run_until_event
from .event_bus import publish_event
from .review_queue import release_review_task, validate_review_lease
from .reviews import submit_review


def submit_review_and_update_agent(
    task_id: int,
    reviewer: str,
    selection: dict[str, Any],
    is_admin: bool,
    lease_token: str | None = None,
) -> dict[str, Any]:
    """Принимает одно решение и передает перестройку знаний агенту."""
    if lease_token:
        validate_review_lease(task_id, reviewer, lease_token)

    result = submit_review(task_id, reviewer, selection, is_admin)
    if not result.get("applied"):
        return result

    decision_id = int(result["decision_id"])
    event_id = publish_event(
        "human_confirmed",
        "review_decision",
        str(decision_id),
        {
            "task_id": task_id,
            "decision_id": decision_id,
            "reviewer": reviewer,
        },
        deduplication_key=f"decision:{decision_id}",
    )
    agent = run_until_event(event_id, max_events=500, worker_id="review-inline")
    if lease_token:
        release_review_task(task_id, reviewer, lease_token)
    return {
        **result,
        "agent_event_id": event_id,
        "agent_status": agent["target_status"],
        "agent_processed": agent["processed"],
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
        },
    }
