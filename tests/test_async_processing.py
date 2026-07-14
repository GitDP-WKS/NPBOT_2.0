from __future__ import annotations

from res_ai_v2.agent import run_agent_cycle
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def test_file_can_be_saved_without_waiting_for_analysis(temp_db) -> None:
    result = import_plan(
        make_plan(
            "a1" * 32,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                }
            ],
        ),
        wait_for_agent=False,
    )
    assert result["agent"]["target_status"] == "queued"
    assert knowledge_rows() == []

    cycle = run_agent_cycle(max_events=20, worker_id="async-test")
    assert cycle.failed == 0
    assert knowledge_rows()[0]["res_name"] == "Лаишевский район электрических сетей"


def test_operator_gets_next_task_without_waiting_for_rebuild(temp_db) -> None:
    import_plan(
        make_plan(
            "b2" * 32,
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
    task = claim_review_task("Иванов")
    assert task is not None

    result = submit_review_and_update_agent(
        task["id"],
        "Иванов",
        {
            "selected_res": ["Лаишевский район электрических сетей"],
            "locality": "Усады",
            "district": "Лаишевский",
            "settlement": "",
            "street": "",
        },
        False,
        task["lease_token"],
        wait_for_agent=False,
    )
    assert result["agent_status"] == "queued"
    assert list_review_tasks("Петров") == []

    cycle = run_agent_cycle(max_events=20, worker_id="async-test")
    assert cycle.failed == 0
    active = [row for row in knowledge_rows() if row["active"]]
    assert len(active) == 1
    assert active[0]["res_name"] == "Лаишевский район электрических сетей"
