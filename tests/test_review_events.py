from __future__ import annotations

from sqlalchemy import func, select

from res_ai_v2.db import get_engine
from res_ai_v2.event_schema import agent_events
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import list_review_tasks
from res_ai_v2.review_service import submit_review_and_update_agent
from res_ai_v2.reviews import recent_decisions, undo_decision
from tests.test_import_and_analysis import make_plan


def test_same_selection_after_undo_creates_new_agent_event(temp_db) -> None:
    import_plan(
        make_plan(
            "8" * 64,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                },
                {
                    "res": "Пригородный район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                },
            ],
        )
    )
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
    }

    task = list_review_tasks("Администратор")[0]
    first = submit_review_and_update_agent(task["id"], "Администратор", selection, True)
    undo_decision(recent_decisions()[0]["id"])
    reopened = list_review_tasks("Администратор")[0]
    second = submit_review_and_update_agent(reopened["id"], "Администратор", selection, True)

    assert first["decision_id"] != second["decision_id"]
    assert first["agent_event_id"] != second["agent_event_id"]
    with get_engine().connect() as conn:
        count = int(
            conn.scalar(
                select(func.count())
                .select_from(agent_events)
                .where(agent_events.c.event_type == "human_confirmed")
            )
            or 0
        )
    assert count == 2
