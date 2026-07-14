from __future__ import annotations

from res_ai_v2.admin_service import request_full_analysis
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def test_same_address_with_different_texts_is_a_duplicate(temp_db) -> None:
    import_plan(
        make_plan(
            "d1" * 32,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "text": "Нет света на первой улице",
                },
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "text": "Отключение электричества",
                },
            ],
        )
    )

    row = knowledge_rows()[0]
    assert row["status"] == "source_only"
    assert row["source_confidence"] == 50.0
    tasks = list_review_tasks("Проверяющий")
    assert [task["task_type"] for task in tasks] == ["duplicate_observation"]


def test_full_audit_does_not_reopen_unchanged_human_decision(temp_db) -> None:
    import_plan(
        make_plan(
            "d2" * 32,
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
    )
    assert result["agent_status"] == "completed"
    assert list_review_tasks("Петров") == []

    audit = request_full_analysis(wait_for_agent=True)
    assert audit["status"] == "completed"
    assert list_review_tasks("Петров") == []
