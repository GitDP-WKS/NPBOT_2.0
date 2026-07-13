from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.reviews import recent_decisions, submit_review, undo_decision
from tests.test_import_and_analysis import make_plan


def test_three_reviewers_required_and_admin_is_immediate(temp_db) -> None:
    plan = make_plan(
        "d" * 64,
        [
            {"res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
            {"res": "Пригородный район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
        ],
    )
    import_plan(plan)
    task = list_review_tasks("А")[0]
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
        "none_correct": False,
    }
    assert submit_review(task["id"], "А", selection, False)["applied"] is False
    assert submit_review(task["id"], "Б", selection, False)["applied"] is False
    assert submit_review(task["id"], "В", selection, False)["applied"] is True
    active = [row for row in knowledge_rows() if row["active"]]
    assert len(active) == 1
    assert active[0]["res_name"] == "Лаишевский район электрических сетей"
    assert active[0]["status"] == "human_verified"


def test_admin_can_undo_decision(temp_db) -> None:
    plan = make_plan(
        "e" * 64,
        [
            {"res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
            {"res": "Пригородный район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
        ],
    )
    import_plan(plan)
    task = list_review_tasks("Администратор")[0]
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
        "none_correct": False,
    }
    assert submit_review(task["id"], "Администратор", selection, True)["applied"] is True
    decision = recent_decisions()[0]
    undo_decision(decision["id"])
    reopened = list_review_tasks("Другой")
    assert reopened and reopened[0]["id"] == task["id"]
