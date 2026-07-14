from __future__ import annotations

from sqlalchemy import func, select

from res_ai_v2.admin_service import request_full_analysis
from res_ai_v2.db import get_engine
from res_ai_v2.event_schema import agent_events


def test_manual_analysis_can_be_started_repeatedly(temp_db) -> None:
    first = request_full_analysis()
    second = request_full_analysis()

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first["event_id"] != second["event_id"]
    assert first["agent"]["failed"] == 0
    assert second["agent"]["failed"] == 0

    with get_engine().connect() as conn:
        count = int(
            conn.scalar(
                select(func.count())
                .select_from(agent_events)
                .where(agent_events.c.event_type == "full_analysis_requested")
            )
            or 0
        )
    assert count == 2
