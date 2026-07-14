from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update

from .db import get_engine, initialize_database, utcnow
from .event_bus import publish_event
from .event_schema import agent_events
from .normalize import stable_json
from .pit_schema import agent_daily_runs

SYSTEM_TIMEZONE = ZoneInfo("Europe/Moscow")


def current_run_date(now: datetime | None = None) -> str:
    moment = now or utcnow()
    return moment.astimezone(SYSTEM_TIMEZONE).date().isoformat()


def ensure_daily_audit(now: datetime | None = None) -> int | None:
    """Создает не более одного активного полного самоанализа на календарный день."""
    initialize_database()
    run_date = current_run_date(now)
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                agent_daily_runs.c.event_id,
                agent_daily_runs.c.status,
            ).where(agent_daily_runs.c.run_date == run_date)
        ).first()
        event_status = None
        if row and row.event_id:
            event_status = conn.scalar(
                select(agent_events.c.status).where(agent_events.c.id == int(row.event_id))
            )
    if row and row.status == "completed":
        return None
    if event_status in {"pending", "processing", "retry", "completed"}:
        return None

    retry_key = f":retry:{int(row.event_id)}" if row and row.event_id else ""
    event_id = publish_event(
        "daily_full_audit",
        "database",
        run_date,
        {"run_date": run_date},
        deduplication_key=f"daily:{run_date}{retry_key}",
    )
    now_utc = utcnow()
    with get_engine().begin() as conn:
        if row:
            conn.execute(
                update(agent_daily_runs)
                .where(agent_daily_runs.c.run_date == run_date)
                .values(
                    event_id=event_id,
                    status="scheduled",
                    started_at=None,
                    finished_at=None,
                    result_json="{}",
                    updated_at=now_utc,
                )
            )
        else:
            conn.execute(
                insert(agent_daily_runs).values(
                    run_date=run_date,
                    event_id=event_id,
                    status="scheduled",
                    started_at=None,
                    finished_at=None,
                    result_json="{}",
                    updated_at=now_utc,
                )
            )
    return event_id


def mark_daily_started(run_date: str, event_id: int) -> None:
    now = utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            update(agent_daily_runs)
            .where(
                agent_daily_runs.c.run_date == run_date,
                agent_daily_runs.c.event_id == event_id,
            )
            .values(status="running", started_at=now, updated_at=now)
        )


def mark_daily_finished(
    run_date: str,
    event_id: int,
    result: dict[str, Any],
    *,
    failed: bool = False,
) -> None:
    now = utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            update(agent_daily_runs)
            .where(
                agent_daily_runs.c.run_date == run_date,
                agent_daily_runs.c.event_id == event_id,
            )
            .values(
                status="failed" if failed else "completed",
                result_json=stable_json(result),
                finished_at=now,
                updated_at=now,
            )
        )


def latest_daily_audit() -> dict[str, Any] | None:
    initialize_database()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(agent_daily_runs).order_by(agent_daily_runs.c.run_date.desc()).limit(1)
        ).first()
    return dict(row._mapping) if row else None
