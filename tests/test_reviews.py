from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.review_service import submit_review_and_update_agent
from res_ai_v2.reviews import recent_decisions, undo_decision
from tests.test_import_and_analysis import make_plan


def _selection() -> dict:
    return {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
        "none_correct": False,
    }


def _conflict_plan(file_hash: str):
    return make_plan(
        file_hash,
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


def test_one_operator_decision_is_enough(temp_db) -> None:
    import_plan(_conflict_plan("d" * 64))
    task = list_review_tasks("А")[0]

    result = submit_review_and_update_agent(task["id"], "А", _selection(), False)

    assert result["applied"] is True
    assert result["required"] == 1
    assert result["agent_status"] == "completed"
    assert list_review_tasks("Б") == []
    active = [row for row in knowledge_rows() if row["active"]]
    assert len(active) == 1
    assert active[0]["res_name"] == "Лаишевский район электрических сетей"
    assert active[0]["status"] == "human_verified"


def test_admin_can_undo_decision(temp_db) -> None:
    import_plan(_conflict_plan("e" * 64))
    task = list_review_tasks("Администратор")[0]
    result = submit_review_and_update_agent(
        task["id"],
        "Администратор",
        _selection(),
        True,
    )
    assert result["applied"] is True

    decision = recent_decisions()[0]
    undo_decision(decision["id"])

    reopened = list_review_tasks("Другой")
    assert reopened and reopened[0]["id"] == task["id"]
