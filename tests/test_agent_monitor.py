from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from res_ai_v2.agent_monitor import agent_status, recover_stale_events
from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.event_bus import claim_next_event, publish_event
from res_ai_v2.event_schema import agent_events


def test_agent_status_counts_events(temp_db):
    publish_event("address_changed", "address", "1", {"address_ids": [1]})
    status = agent_status()
    assert status["counts"]["pending"] == 1
    assert status["healthy"] is True


def test_recover_stale_processing_event(temp_db):
    event_id = publish_event("address_changed", "address", "2", {"address_ids": [2]})
    event = claim_next_event("test-worker")
    assert event is not None
    with get_engine().begin() as conn:
        conn.execute(
            update(agent_events)
            .where(agent_events.c.id == event_id)
            .values(locked_at=utcnow() - timedelta(minutes=30))
        )
    assert recover_stale_events(max_age_minutes=15) == 1
    with get_engine().connect() as conn:
        row = conn.execute(select(agent_events).where(agent_events.c.id == event_id)).first()
    assert row is not None
    assert row.status == "retry"
    assert row.locked_by is None
