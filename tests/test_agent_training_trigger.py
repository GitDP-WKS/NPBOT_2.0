from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from res_ai_v2.agent import run_agent_cycle
from res_ai_v2.db import get_engine, set_setting
from res_ai_v2.event_bus import publish_event
from res_ai_v2.event_schema import agent_events


def test_human_decision_schedules_training_event(temp_db: Path, monkeypatch):
    monkeypatch.setenv("RETRAIN_AFTER_DECISIONS", "1")
    set_setting("human_decisions_since_training", "1")
    publish_event(
        "human_confirmed",
        "review_task",
        "9",
        {"address_ids": []},
        deduplication_key="decision-9",
    )
    result = run_agent_cycle(max_events=1, worker_id="test-worker")
    assert result.completed == 1
    with get_engine().connect() as conn:
        row = conn.execute(
            select(agent_events.c.status).where(
                agent_events.c.event_type == "training_requested"
            )
        ).first()
    assert row is not None
    assert row.status == "pending"
