from __future__ import annotations

from res_ai_v2.agent import _schedule_training_if_needed
from res_ai_v2.db import set_setting
from res_ai_v2.event_bus import AgentEvent


def _human_event(event_id: int) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        event_key=f"event-{event_id}",
        event_type="human_confirmed",
        subject_type="review_decision",
        subject_key=str(event_id),
        payload={},
        attempts=1,
    )


def test_training_event_can_be_scheduled_again_for_new_data_version(temp_db, monkeypatch) -> None:
    monkeypatch.setattr(
        "res_ai_v2.agent.load_settings",
        lambda: type("Settings", (), {"retrain_after_human_decisions": 3})(),
    )
    set_setting("human_decisions_since_training", "3")
    set_setting("data_version", "10")
    first = _schedule_training_if_needed(_human_event(1))
    duplicate = _schedule_training_if_needed(_human_event(2))
    assert first == duplicate

    set_setting("data_version", "11")
    second = _schedule_training_if_needed(_human_event(3))
    assert second is not None
    assert second != first
