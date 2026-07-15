from __future__ import annotations

from typing import Any
from uuid import uuid4

from .agent import run_until_event
from .event_bus import publish_event


def _run_admin_event(
    event_type: str,
    subject_type: str,
    subject_key: str,
    payload: dict[str, Any],
    *,
    wait_for_agent: bool,
) -> dict[str, Any]:
    event_id = publish_event(
        event_type,
        subject_type,
        subject_key,
        payload,
        deduplication_key=f"manual:{uuid4().hex}",
    )
    if wait_for_agent:
        agent = run_until_event(event_id, max_events=500, worker_id="admin-service")
        target = agent.get("target_result") or {}
        status = agent["target_status"]
        result = target.get("result")
    else:
        agent = {"processed": 0, "completed": 0, "failed": 0}
        status = "queued"
        result = None
    return {
        "event_id": event_id,
        "status": status,
        "result": result,
        "agent": {
            "processed": agent["processed"],
            "completed": agent["completed"],
            "failed": agent["failed"],
        },
    }


def request_full_analysis(
    actor: str = "Администратор",
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    return _run_admin_event(
        "full_analysis_requested",
        "database",
        "knowledge",
        {"actor": actor},
        wait_for_agent=wait_for_agent,
    )


def request_training(
    actor: str = "Администратор",
    *,
    wait_for_agent: bool = True,
) -> dict[str, Any]:
    return _run_admin_event(
        "training_requested",
        "model",
        "candidate",
        {"actor": actor, "manual": True},
        wait_for_agent=wait_for_agent,
    )


def request_analysis_and_training(actor: str = "Администратор") -> dict[str, Any]:
    analysis = request_full_analysis(actor, wait_for_agent=True)
    if analysis["agent"]["failed"]:
        return {"analysis": analysis, "training": None}
    training = request_training(actor, wait_for_agent=True)
    return {"analysis": analysis, "training": training}
