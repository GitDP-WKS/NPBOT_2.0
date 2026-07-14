from __future__ import annotations

from typing import Any

from .agent import run_agent_cycle
from .daily_audit import ensure_daily_audit
from .event_bus import queue_status


def run_opportunistic_tick(max_events: int = 2) -> dict[str, Any]:
    """Планирует дневной аудит и обрабатывает небольшую часть очереди."""
    daily_event_id = ensure_daily_audit()
    counts = queue_status()
    available = int(counts.get("pending", 0)) + int(counts.get("retry", 0))
    if available <= 0:
        return {
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "skipped": True,
            "daily_event_id": daily_event_id,
        }
    cycle = run_agent_cycle(max_events=max(1, max_events), worker_id="application-tick")
    return {
        "processed": cycle.processed,
        "completed": cycle.completed,
        "failed": cycle.failed,
        "skipped": False,
        "daily_event_id": daily_event_id,
    }
