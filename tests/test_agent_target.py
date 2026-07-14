from __future__ import annotations

from res_ai_v2.agent import register_handler, run_until_event
from res_ai_v2.event_bus import publish_event


def test_run_until_event_processes_earlier_events_first(temp_db) -> None:
    register_handler("target_test", lambda event: {"subject": event.subject_key})
    publish_event("target_test", "test", "first", {})
    publish_event("target_test", "test", "second", {})
    target_id = publish_event("target_test", "test", "target", {})

    result = run_until_event(target_id, max_events=10, worker_id="target-worker")

    assert result["target_status"] == "completed"
    assert result["processed"] == 3
    assert result["completed"] == 3
    assert result["failed"] == 0
    assert result["target_result"]["event_id"] == target_id
