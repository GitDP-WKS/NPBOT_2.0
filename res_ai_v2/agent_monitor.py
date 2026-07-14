from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from .db import get_engine, initialize_database, utcnow
from .event_schema import agent_events, agent_runs


EVENT_LABELS = {
    "file_imported": "Загружен файл",
    "address_changed": "Изменены адресные данные",
    "human_confirmed": "Принято решение человека",
    "training_requested": "Подготовка новой модели",
}

STATUS_LABELS = {
    "pending": "Ожидает обработки",
    "processing": "Обрабатывается",
    "retry": "Ожидает повторной попытки",
    "completed": "Завершено",
    "failed": "Ошибка",
}


def recover_stale_events(max_age_minutes: int = 15) -> int:
    """Возвращает зависшие события в очередь после истечения срока блокировки."""
    initialize_database()
    cutoff = utcnow() - timedelta(minutes=max(1, max_age_minutes))
    now = utcnow()
    with get_engine().begin() as conn:
        stale_ids = list(
            conn.scalars(
                select(agent_events.c.id).where(
                    agent_events.c.status == "processing",
                    agent_events.c.locked_at.is_not(None),
                    agent_events.c.locked_at < cutoff,
                )
            )
        )
        if not stale_ids:
            return 0
        conn.execute(
            update(agent_events)
            .where(agent_events.c.id.in_(stale_ids))
            .values(
                status="retry",
                available_at=now,
                locked_at=None,
                locked_by=None,
                last_error="Обработка была прервана. Событие возвращено в очередь.",
                updated_at=now,
            )
        )
        conn.execute(
            update(agent_runs)
            .where(
                agent_runs.c.event_id.in_(stale_ids),
                agent_runs.c.status == "processing",
            )
            .values(
                status="failed",
                error_text="Обработка была прервана и возвращена в очередь.",
                finished_at=now,
            )
        )
    return len(stale_ids)


def agent_status() -> dict[str, Any]:
    initialize_database()
    with get_engine().connect() as conn:
        counts = {
            str(status): int(count)
            for status, count in conn.execute(
                select(agent_events.c.status, func.count())
                .group_by(agent_events.c.status)
            )
        }
        last_event = conn.execute(
            select(agent_events)
            .order_by(agent_events.c.id.desc())
            .limit(1)
        ).first()
        last_run = conn.execute(
            select(agent_runs)
            .order_by(agent_runs.c.id.desc())
            .limit(1)
        ).first()
    return {
        "counts": counts,
        "last_event": dict(last_event._mapping) if last_event else None,
        "last_run": dict(last_run._mapping) if last_run else None,
        "healthy": counts.get("failed", 0) == 0 and counts.get("processing", 0) <= 1,
    }


def recent_agent_events(limit: int = 100) -> list[dict[str, Any]]:
    initialize_database()
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(agent_events)
            .order_by(agent_events.c.id.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row._mapping)
        item["event_name"] = EVENT_LABELS.get(str(item["event_type"]), str(item["event_type"]))
        item["status_name"] = STATUS_LABELS.get(str(item["status"]), str(item["status"]))
        try:
            item["payload"] = json.loads(str(item.pop("payload_json")) or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        result.append(item)
    return result


def recent_agent_runs(limit: int = 100) -> list[dict[str, Any]]:
    initialize_database()
    query = (
        select(
            agent_runs.c.id,
            agent_runs.c.event_id,
            agent_runs.c.worker_id,
            agent_runs.c.status,
            agent_runs.c.result_json,
            agent_runs.c.error_text,
            agent_runs.c.started_at,
            agent_runs.c.finished_at,
            agent_events.c.event_type,
            agent_events.c.subject_key,
        )
        .select_from(agent_runs.join(agent_events, agent_runs.c.event_id == agent_events.c.id))
        .order_by(agent_runs.c.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row._mapping)
        item["event_name"] = EVENT_LABELS.get(str(item["event_type"]), str(item["event_type"]))
        item["status_name"] = STATUS_LABELS.get(str(item["status"]), str(item["status"]))
        result.append(item)
    return result
