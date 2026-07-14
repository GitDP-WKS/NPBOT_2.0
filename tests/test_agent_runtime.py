from __future__ import annotations

from res_ai_v2.agent import register_handler
from res_ai_v2.agent_runtime import run_opportunistic_tick
from res_ai_v2.event_bus import publish_event, queue_status


def test_application_tick_skips_empty_queue(temp_db) -> None:
    result = run_opportunistic_tick()
    assert result == {
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "skipped": True,
    }


def test_application_tick_processes_limited_number_of_events(temp_db) -> None:
    register_handler("runtime_test", lambda event: {"subject": event.subject_key})
    for index in range(4):
        publish_event("runtime_test", "test", str(index), {"index": index})

    first = run_opportunistic_tick(max_events=2)
    assert first["processed"] == 2
    assert first["completed"] == 2
    assert first["failed"] == 0
    assert queue_status().get("pending") == 2

    second = run_opportunistic_tick(max_events=2)
    assert second["processed"] == 2
    assert second["completed"] == 2
    assert queue_status().get("completed") == 4
