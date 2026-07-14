from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .db import get_engine, initialize_database, utcnow
from .event_schema import agent_events, agent_runs
from .normalize import sha256_parts, stable_json


@dataclass(frozen=True)
class AgentEvent:
    id: int
    event_key: str
    event_type: str
    subject_type: str
    subject_key: str
    payload: dict[str, Any]
    attempts: int


def worker_identity() -> str:
    return socket.gethostname() or "res-ai-worker"


def publish_event(
    event_type: str,
    subject_type: str,
    subject_key: str,
    payload: dict[str, Any] | None = None,
    *,
    deduplication_key: str = "",
) -> int:
    """Сохраняет событие один раз и возвращает его идентификатор."""
    initialize_database()
    body = payload or {}
    event_key = sha256_parts(
        [event_type, subject_type, subject_key, deduplication_key or stable_json(body)]
    )
    now = utcnow()
    values = {
        "event_key": event_key,
        "event_type": event_type,
        "subject_type": subject_type,
        "subject_key": subject_key,
        "payload_json": stable_json(body),
        "status": "pending",
        "attempts": 0,
        "available_at": now,
        "locked_at": None,
        "locked_by": None,
        "last_error": "",
        "created_at": now,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(agent_events.c.id).where(agent_events.c.event_key == event_key)
        ).first()
        if existing:
            return int(existing.id)
        try:
            return int(conn.execute(insert(agent_events).values(**values)).inserted_primary_key[0])
        except IntegrityError:
            existing = conn.execute(
                select(agent_events.c.id).where(agent_events.c.event_key == event_key)
            ).first()
            if not existing:
                raise
            return int(existing.id)


def claim_next_event(worker_id: str | None = None) -> AgentEvent | None:
    """Забирает одно доступное событие без двойной обработки конкурентами."""
    initialize_database()
    worker = worker_id or worker_identity()
    now = utcnow()
    engine = get_engine()
    with engine.begin() as conn:
        query = (
            select(agent_events)
            .where(
                agent_events.c.status.in_(["pending", "retry"]),
                agent_events.c.available_at <= now,
            )
            .order_by(agent_events.c.available_at, agent_events.c.id)
            .limit(1)
        )
        if engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = conn.execute(query).first()
        if not row:
            return None
        event = dict(row._mapping)
        updated = conn.execute(
            update(agent_events)
            .where(
                agent_events.c.id == int(event["id"]),
                agent_events.c.status.in_(["pending", "retry"]),
            )
            .values(
                status="processing",
                attempts=int(event["attempts"]) + 1,
                locked_at=now,
                locked_by=worker,
                updated_at=now,
            )
        )
        if updated.rowcount != 1:
            return None
        conn.execute(
            insert(agent_runs).values(
                event_id=int(event["id"]),
                worker_id=worker,
                status="processing",
                result_json="{}",
                error_text="",
                started_at=now,
                finished_at=None,
            )
        )
    return AgentEvent(
        id=int(event["id"]),
        event_key=str(event["event_key"]),
        event_type=str(event["event_type"]),
        subject_type=str(event["subject_type"]),
        subject_key=str(event["subject_key"]),
        payload=json.loads(str(event["payload_json"]) or "{}"),
        attempts=int(event["attempts"]) + 1,
    )


def complete_event(event_id: int, result: dict[str, Any] | None = None) -> None:
    now = utcnow()
    encoded = stable_json(result or {})
    with get_engine().begin() as conn:
        conn.execute(
            update(agent_events)
            .where(agent_events.c.id == event_id)
            .values(
                status="completed",
                locked_at=None,
                locked_by=None,
                last_error="",
                updated_at=now,
            )
        )
        run = conn.execute(
            select(agent_runs.c.id)
            .where(agent_runs.c.event_id == event_id, agent_runs.c.status == "processing")
            .order_by(agent_runs.c.id.desc())
            .limit(1)
        ).first()
        if run:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == int(run.id))
                .values(status="completed", result_json=encoded, finished_at=now)
            )


def fail_event(event_id: int, error: Exception | str, *, max_attempts: int = 5) -> None:
    now = utcnow()
    message = str(error)[:4000]
    with get_engine().begin() as conn:
        row = conn.execute(
            select(agent_events.c.attempts).where(agent_events.c.id == event_id)
        ).first()
        attempts = int(row.attempts) if row else max_attempts
        retry = attempts < max_attempts
        delay = min(60 * (2 ** max(attempts - 1, 0)), 3600)
        conn.execute(
            update(agent_events)
            .where(agent_events.c.id == event_id)
            .values(
                status="retry" if retry else "failed",
                available_at=now + timedelta(seconds=delay),
                locked_at=None,
                locked_by=None,
                last_error=message,
                updated_at=now,
            )
        )
        run = conn.execute(
            select(agent_runs.c.id)
            .where(agent_runs.c.event_id == event_id, agent_runs.c.status == "processing")
            .order_by(agent_runs.c.id.desc())
            .limit(1)
        ).first()
        if run:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == int(run.id))
                .values(status="failed", error_text=message, finished_at=now)
            )


def queue_status() -> dict[str, int]:
    initialize_database()
    with get_engine().connect() as conn:
        rows = conn.execute(select(agent_events.c.status)).all()
    result: dict[str, int] = {}
    for row in rows:
        key = str(row.status)
        result[key] = result.get(key, 0) + 1
    return result
