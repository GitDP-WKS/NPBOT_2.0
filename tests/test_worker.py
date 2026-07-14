from __future__ import annotations

import threading

from sqlalchemy import select

from res_ai_v2.agent import register_handler
from res_ai_v2.db import get_engine
from res_ai_v2.event_bus import publish_event
from res_ai_v2.event_schema import agent_events
from res_ai_v2.worker import run_worker


def test_worker_processes_events_and_stops(temp_db) -> None:
    register_handler("worker_test", lambda event: {"subject": event.subject_key})
    event_id = publish_event("worker_test", "test", "1", {})
    stop_event = threading.Event()

    def stop_after_idle(_seconds: float) -> None:
        stop_event.set()

    run_worker(
        stop_event=stop_event,
        idle_seconds=0.1,
        max_events=10,
        stale_check_every=10,
        sleep=stop_after_idle,
    )

    with get_engine().connect() as conn:
        status = conn.scalar(select(agent_events.c.status).where(agent_events.c.id == event_id))
    assert status == "completed"


def test_worker_sleeps_when_queue_is_empty(temp_db) -> None:
    stop_event = threading.Event()
    calls: list[float] = []

    def stop_after_idle(seconds: float) -> None:
        calls.append(seconds)
        stop_event.set()

    run_worker(
        stop_event=stop_event,
        idle_seconds=0.25,
        max_events=10,
        stale_check_every=10,
        sleep=stop_after_idle,
    )

    assert calls == [0.25]
