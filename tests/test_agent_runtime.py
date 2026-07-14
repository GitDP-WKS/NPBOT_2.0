from __future__ import annotations

from res_ai_v2.agent import register_handler
from res_ai_v2.agent_runtime import run_opportunistic_tick
from res_ai_v2.daily_audit import latest_daily_audit
from res_ai_v2.event_bus import publish_event, queue_status


def test_application_tick_runs_daily_audit_on_empty_queue(temp_db) -> None:
    result = run_opportunistic_tick()

    assert result["processed"] == 1
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert result["skipped"] is False
    assert result["daily_event_id"] is not None
    assert latest_daily_audit()["status"] == "completed"


def test_application_tick_preserves_limit_for_existing_events(temp_db) -> None:
    register_handler("runtime_test", lambda event: {"subject": event.subject_key})
    for index in range(4):
        publish_event("runtime_test", "test", str(index), {"index": index})

    first = run_opportunistic_tick(max_events=2)
    assert first["processed"] == 2
    assert first["completed"] == 2
    assert first["failed"] == 0
    assert queue_status().get("pending") == 3

    second = run_opportunistic_tick(max_events=2)
    assert second["processed"] == 2
    assert second["completed"] == 2
    assert queue_status().get("completed") == 4
    assert queue_status().get("pending") == 1

    third = run_opportunistic_tick(max_events=2)
    assert third["processed"] == 1
    assert latest_daily_audit()["status"] == "completed"
