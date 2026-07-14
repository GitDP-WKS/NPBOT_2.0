from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .analyzer import analyze_database
from .event_bus import AgentEvent, claim_next_event, complete_event, fail_event, worker_identity

EventHandler = Callable[[AgentEvent], dict[str, Any]]


@dataclass(frozen=True)
class AgentCycleResult:
    processed: int
    completed: int
    failed: int
    results: list[dict[str, Any]]


def _handle_file_imported(event: AgentEvent) -> dict[str, Any]:
    analysis = analyze_database()
    return {
        "event": event.event_type,
        "source_file": event.subject_key,
        "analysis": analysis,
    }


def _handle_knowledge_changed(event: AgentEvent) -> dict[str, Any]:
    analysis = analyze_database()
    return {
        "event": event.event_type,
        "subject": event.subject_key,
        "analysis": analysis,
    }


HANDLERS: dict[str, EventHandler] = {
    "file_imported": _handle_file_imported,
    "address_changed": _handle_knowledge_changed,
    "human_confirmed": _handle_knowledge_changed,
}


def register_handler(event_type: str, handler: EventHandler) -> None:
    HANDLERS[event_type] = handler


def process_event(event: AgentEvent) -> dict[str, Any]:
    handler = HANDLERS.get(event.event_type)
    if handler is None:
        raise ValueError(f"Для события {event.event_type!r} не зарегистрирован обработчик.")
    return handler(event)


def run_agent_cycle(*, max_events: int = 20, worker_id: str | None = None) -> AgentCycleResult:
    worker = worker_id or worker_identity()
    processed = completed = failed = 0
    results: list[dict[str, Any]] = []
    for _ in range(max(0, max_events)):
        event = claim_next_event(worker)
        if event is None:
            break
        processed += 1
        try:
            result = process_event(event)
        except Exception as exc:
            failed += 1
            fail_event(event.id, exc)
            results.append(
                {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue
        completed += 1
        complete_event(event.id, result)
        results.append(
            {
                "event_id": event.id,
                "event_type": event.event_type,
                "status": "completed",
                "result": result,
            }
        )
    return AgentCycleResult(
        processed=processed,
        completed=completed,
        failed=failed,
        results=results,
    )
