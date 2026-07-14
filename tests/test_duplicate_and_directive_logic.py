from __future__ import annotations

from res_ai_v2.admin_service import request_full_analysis
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def test_same_address_in_different_events_is_not_technical_duplicate(temp_db) -> None:
    result = import_plan(
        make_plan(
            "d1" * 32,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "text": "Нет света на первой улице",
                    "source_system": "112",
                    "source_event_id": "event-1",
                },
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "text": "Отключение электричества",
                    "source_system": "112",
                    "source_event_id": "event-2",
                },
            ],
        )
    )

    row = knowledge_rows()[0]
    assert row["status"] == "consistent"
    assert 0.0 < float(row["source_confidence"]) <= 100.0
    assert result["independent_evidence"] == 2
    tasks = list_review_tasks("Проверяющий")
    assert not [task for task in tasks if task["task_type"] == "duplicate_observation"]


def test_full_audit_does_not_reopen_unchanged_human_decision(temp_db) -> None:
    import_plan(
        make_plan(
            "d2" * 32,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "source_event_id": "decision-1",
                },
                {
                    "res": "Пригородный район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "source_event_id": "decision-2",
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
            "decision_type": "confirmed",
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
