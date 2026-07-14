from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.review_queue import claim_review_task, release_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def _two_conflicts():
    return make_plan(
        "9" * 64,
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
            {
                "res": "Сабинский район электрических сетей",
                "locality": "Олуяз",
                "district": "Сабинский",
            },
            {
                "res": "Кукморский район электрических сетей",
                "locality": "Олуяз",
                "district": "Сабинский",
            },
        ],
    )


def test_two_reviewers_receive_different_tasks(temp_db) -> None:
    import_plan(_two_conflicts())

    first = claim_review_task("Иванов")
    second = claim_review_task("Петров")

    assert first is not None
    assert second is not None
    assert first["id"] != second["id"]
    assert claim_review_task("Иванов")["id"] == first["id"]


def test_released_task_becomes_available_to_another_reviewer(temp_db) -> None:
    import_plan(_two_conflicts())
    task = claim_review_task("Иванов")
    assert task is not None

    assert release_review_task(task["id"], "Иванов", task["lease_token"])
    claimed_again = claim_review_task("Петров")
    assert claimed_again is not None
    assert claimed_again["id"] == task["id"]


def test_vote_requires_the_current_lease(temp_db) -> None:
    import_plan(_two_conflicts())
    task = claim_review_task("Иванов")
    assert task is not None
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
    }

    try:
        submit_review_and_update_agent(
            task["id"],
            "Иванов",
            selection,
            False,
            "неверный-токен",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Чужой токен аренды должен быть отклонен")

    result = submit_review_and_update_agent(
        task["id"],
        "Иванов",
        selection,
        False,
        task["lease_token"],
    )
    assert result["applied"] is True
    assert result["agent_status"] == "completed"
