from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import list_review_tasks
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def test_new_conflicting_evidence_creates_a_new_task(temp_db) -> None:
    import_plan(
        make_plan(
            "e1" * 32,
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
    submit_review_and_update_agent(
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
    )
    assert list_review_tasks("Петров") == []

    import_plan(
        make_plan(
            "e2" * 32,
            [
                {
                    "res": "Пригородный район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                }
            ],
        )
    )

    tasks = list_review_tasks("Петров")
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "directive_challenge"
