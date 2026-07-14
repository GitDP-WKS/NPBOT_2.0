from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from res_ai_v2.db import get_engine
from res_ai_v2.event_bus import claim_next_event, complete_event, fail_event, publish_event
from res_ai_v2.event_schema import agent_events, agent_runs


def test_event_is_idempotent(temp_db: Path):
    first = publish_event("file_imported", "source_file", "abc", {"rows": 10})
    second = publish_event("file_imported", "source_file", "abc", {"rows": 10})
    assert first == second
    with get_engine().connect() as conn:
        assert conn.scalar(select(func.count()).select_from(agent_events)) == 1


def test_event_can_be_claimed_and_completed(temp_db: Path):
    event_id = publish_event("address_changed", "address", "42", {"address_ids": [42]})
    event = claim_next_event("test-worker")
    assert event is not None
    assert event.id == event_id
    assert claim_next_event("second-worker") is None
    complete_event(event.id, {"processed": 1})
    with get_engine().connect() as conn:
        status = conn.scalar(select(agent_events.c.status).where(agent_events.c.id == event_id))
        run_status = conn.scalar(select(agent_runs.c.status).where(agent_runs.c.event_id == event_id))
    assert status == "completed"
    assert run_status == "completed"


def test_failed_event_is_scheduled_for_retry(temp_db: Path):
    event_id = publish_event("human_confirmed", "review_task", "7", {})
    event = claim_next_event("test-worker")
    assert event is not None
    assert event.id == event_id
    fail_event(event.id, "temporary error", max_attempts=3)
    with get_engine().connect() as conn:
        row = conn.execute(select(agent_events).where(agent_events.c.id == event_id)).first()
    assert row is not None
    assert row.status == "retry"
    assert row.last_error == "temporary error"
