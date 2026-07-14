from __future__ import annotations

from sqlalchemy import func, select

from res_ai_v2.agent import register_handler, run_agent_cycle
from res_ai_v2.event_bus import publish_event
from res_ai_v2.event_schema import agent_events
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import list_review_tasks
from res_ai_v2.review_service import submit_review_and_update_agent
from res_ai_v2.db import get_engine
from tests.test_import_and_analysis import make_plan


def test_agent_processes_large_event_batch_without_loss(temp_db):
    register_handler("load_test", lambda event: {"key": event.subject_key})
    for index in range(500):
        publish_event("load_test", "test", str(index), {"index": index})
    result = run_agent_cycle(max_events=600, worker_id="load-worker")
    assert result.processed == 500
    assert result.completed == 500
    assert result.failed == 0
    with get_engine().connect() as conn:
        completed = int(
            conn.scalar(
                select(func.count())
                .select_from(agent_events)
                .where(agent_events.c.status == "completed")
            )
            or 0
        )
    assert completed == 500


def test_import_review_agent_end_to_end(temp_db):
    plan = make_plan(
        "f" * 64,
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
    imported = import_plan(plan)
    assert imported["agent"]["failed"] == 0
    task = list_review_tasks("Администратор")[0]
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
    }
    result = submit_review_and_update_agent(task["id"], "Администратор", selection, True)
    assert result["applied"] is True
    assert result["agent"]["failed"] == 0
    assert list_review_tasks("Другой") == []
