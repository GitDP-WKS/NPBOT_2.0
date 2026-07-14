from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .agent_monitor import recover_stale_events
from .config import load_settings
from .daily_audit import mark_daily_finished, mark_daily_started
from .db import get_engine, get_setting, set_setting
from .event_bus import AgentEvent, claim_next_event, complete_event, fail_event, publish_event, worker_identity
from .event_schema import agent_events
from .knowledge_agent import rebuild_knowledge
from .modeling import train_candidate

EventHandler = Callable[[AgentEvent], dict[str, Any]]


@dataclass(frozen=True)
class AgentCycleResult:
    processed: int
    completed: int
    failed: int
    results: list[dict[str, Any]]


def _positive_ids(event: AgentEvent, key: str) -> list[int]:
    values = event.payload.get(key) or []
    return sorted({int(value) for value in values if str(value).isdigit() and int(value) > 0})


def _schedule_training_if_needed(event: AgentEvent) -> int | None:
    if event.event_type != "human_confirmed":
        return None
    decisions = int(get_setting("human_decisions_since_training", "0"))
    threshold = load_settings().retrain_after_human_decisions
    if decisions < threshold:
        return None
    data_version = get_setting("data_version", "1")
    return publish_event(
        "training_requested",
        "model",
        "candidate",
        {
            "human_decisions": decisions,
            "threshold": threshold,
            "data_version": data_version,
        },
        deduplication_key=f"decisions:{decisions}:data:{data_version}",
    )


def _pit_event(event: AgentEvent) -> dict[str, Any]:
    observation_ids = _positive_ids(event, "observation_ids")
    analysis = rebuild_knowledge(
        observation_ids=observation_ids or None,
        full_rebuild=not bool(observation_ids),
        trigger_type=event.event_type,
        trigger_key=event.subject_key,
    )
    training_event_id = _schedule_training_if_needed(event)
    return {
        "event": event.event_type,
        "subject": event.subject_key,
        "scope": "changed_observations" if observation_ids else "full_pit",
        "observation_ids": observation_ids,
        "analysis": analysis,
        "training_event_id": training_event_id,
    }


def _human_event(event: AgentEvent) -> dict[str, Any]:
    analysis = rebuild_knowledge(
        full_rebuild=True,
        trigger_type=event.event_type,
        trigger_key=event.subject_key,
    )
    training_event_id = _schedule_training_if_needed(event)
    return {
        "event": event.event_type,
        "subject": event.subject_key,
        "scope": "full_pit",
        "analysis": analysis,
        "training_event_id": training_event_id,
    }


def _full_analysis_event(event: AgentEvent) -> dict[str, Any]:
    return {
        "event": event.event_type,
        "subject": event.subject_key,
        "scope": "full_pit",
        "analysis": rebuild_knowledge(
            full_rebuild=True,
            trigger_type=event.event_type,
            trigger_key=event.subject_key,
        ),
    }


def _daily_audit_event(event: AgentEvent) -> dict[str, Any]:
    run_date = str(event.payload.get("run_date") or event.subject_key)
    mark_daily_started(run_date, event.id)
    try:
        result = _full_analysis_event(event)
    except Exception as exc:
        mark_daily_finished(
            run_date,
            event.id,
            {"error": str(exc)},
            failed=True,
        )
        raise
    mark_daily_finished(run_date, event.id, result)
    return result


def _train_candidate_event(event: AgentEvent) -> dict[str, Any]:
    actor = str(event.payload.get("actor") or "Агент РЭС AI")
    result = train_candidate(actor=actor)
    set_setting("human_decisions_since_training", "0")
    return {
        "event": event.event_type,
        "version": result["version"],
        "gate_passed": result["gate_passed"],
        "metrics": result["metrics"],
        "confusion": result.get("confusion", []),
        "published": False,
        "message": "Кандидат подготовлен. Публикация выполняется администратором.",
    }


HANDLERS: dict[str, EventHandler] = {
    "file_imported": _pit_event,
    "pit_ingested": _pit_event,
    "address_changed": _human_event,
    "human_confirmed": _human_event,
    "knowledge_directive_revoked": _human_event,
    "full_analysis_requested": _full_analysis_event,
    "daily_full_audit": _daily_audit_event,
    "training_requested": _train_candidate_event,
}


def register_handler(event_type: str, handler: EventHandler) -> None:
    HANDLERS[event_type] = handler


def process_event(event: AgentEvent) -> dict[str, Any]:
    handler = HANDLERS.get(event.event_type)
    if handler is None:
        raise ValueError(f"Для события {event.event_type!r} не зарегистрирован обработчик.")
    return handler(event)


def run_agent_cycle(*, max_events: int = 20, worker_id: str | None = None) -> AgentCycleResult:
    recover_stale_events()
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


def run_until_event(
    event_id: int,
    *,
    max_events: int = 200,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Обрабатывает очередь по порядку, пока целевое событие не завершится."""
    processed = completed = failed = 0
    results: list[dict[str, Any]] = []
    for _ in range(max(1, max_events)):
        with get_engine().connect() as conn:
            status = conn.scalar(select(agent_events.c.status).where(agent_events.c.id == event_id))
        if status in {"completed", "failed"}:
            break
        cycle = run_agent_cycle(max_events=1, worker_id=worker_id)
        processed += cycle.processed
        completed += cycle.completed
        failed += cycle.failed
        results.extend(cycle.results)
        if cycle.processed == 0:
            break

    target = next((item for item in results if item.get("event_id") == event_id), None)
    with get_engine().connect() as conn:
        final_status = conn.scalar(select(agent_events.c.status).where(agent_events.c.id == event_id))
    return {
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "target_status": str(final_status or "unknown"),
        "target_result": target,
        "results": results,
    }
