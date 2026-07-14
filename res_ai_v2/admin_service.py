from __future__ import annotations

from typing import Any
from uuid import uuid4

from .agent import run_agent_cycle
from .event_bus import publish_event


def _run_admin_event(
    event_type: str,
    subject_type: str,
    subject_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event_id = publish_event(
        event_type,
        subject_type,
        subject_key,
        payload,
        deduplication_key=f"manual:{uuid4().hex}",
    )
    cycle = run_agent_cycle(max_events=50, worker_id="admin-service")
    event_result = next((item for item in cycle.results if item.get("event_id") == event_id), None)
    return {
        "event_id": event_id,
        "status": (event_result or {}).get("status", "queued"),
        "result": (event_result or {}).get("result"),
        "agent": {
            "processed": cycle.processed,
            "completed": cycle.completed,
            "failed": cycle.failed,
        },
    }


def request_full_analysis(actor: str = "Администратор") -> dict[str, Any]:
    return _run_admin_event(
        "full_analysis_requested",
        "database",
        "knowledge",
        {"actor": actor},
    )


def request_training(actor: str = "Администратор") -> dict[str, Any]:
    return _run_admin_event(
        "training_requested",
        "model",
        "candidate",
        {"actor": actor, "manual": True},
    )


def request_analysis_and_training(actor: str = "Администратор") -> dict[str, Any]:
    analysis = request_full_analysis(actor)
    if analysis["agent"]["failed"]:
        return {"analysis": analysis, "training": None}
    training = request_training(actor)
    return {"analysis": analysis, "training": training}
