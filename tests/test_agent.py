from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from res_ai_v2.agent import register_handler, run_agent_cycle
from res_ai_v2.db import get_engine
from res_ai_v2.event_bus import publish_event
from res_ai_v2.event_schema import agent_events


def test_agent_processes_registered_event(temp_db: Path):
    register_handler("test_event", lambda event: {"subject": event.subject_key})
    event_id = publish_event("test_event", "test", "42", {"value": 1})
    result = run_agent_cycle(max_events=10, worker_id="test-worker")
    assert result.processed == 1
    assert result.completed == 1
    assert result.failed == 0
    with get_engine().connect() as conn:
        status = conn.scalar(select(agent_events.c.status).where(agent_events.c.id == event_id))
    assert status == "completed"


def test_agent_retries_failed_event(temp_db: Path):
    def fail(_event):
        raise RuntimeError("broken")

    register_handler("broken_event", fail)
    event_id = publish_event("broken_event", "test", "7", {})
    result = run_agent_cycle(max_events=1, worker_id="test-worker")
    assert result.processed == 1
    assert result.completed == 0
    assert result.failed == 1
    with get_engine().connect() as conn:
        row = conn.execute(select(agent_events).where(agent_events.c.id == event_id)).first()
    assert row is not None
    assert row.status == "retry"
    assert "broken" in row.last_error
