from __future__ import annotations

from sqlalchemy import func, select

from res_ai_v2.db import get_engine
from res_ai_v2.import_service import import_plan
from res_ai_v2.pit_schema import knowledge_generations, pit_observations, pit_occurrences
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from tests.test_import_and_analysis import make_plan


def test_import_is_saved_in_pit_and_agent_builds_knowledge(temp_db) -> None:
    result = import_plan(
        make_plan(
            "1" * 64,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Усады",
                    "district": "Лаишевский",
                }
            ],
        )
    )
    assert result["agent"]["failed"] == 0
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(knowledge_generations)) or 0) == 1
    row = knowledge_rows()[0]
    assert row["status"] == "consistent"
    assert row["source_confidence"] == 99.9


def test_repeated_observation_loses_prestige_and_creates_one_task(temp_db) -> None:
    row = {
        "res": "Лаишевский район электрических сетей",
        "locality": "Усады",
        "district": "Лаишевский",
    }
    import_plan(make_plan("2" * 64, [row]))
    result = import_plan(make_plan("3" * 64, [row]))
    assert result["agent"]["failed"] == 0
    with get_engine().connect() as conn:
        pit = conn.execute(select(pit_observations)).one()
        assert pit.occurrence_count == 2
        assert pit.source_count == 2
    knowledge = knowledge_rows()
    assert len(knowledge) == 1
    assert knowledge[0]["status"] == "source_only"
    assert knowledge[0]["source_confidence"] == 50.0
    tasks = list_review_tasks("Проверяющий")
    assert [task["task_type"] for task in tasks] == ["duplicate_observation"]


def test_reuploading_same_file_does_not_add_pit_occurrence(temp_db) -> None:
    plan = make_plan(
        "4" * 64,
        [
            {
                "res": "Лаишевский район электрических сетей",
                "locality": "Усады",
                "district": "Лаишевский",
            }
        ],
    )
    import_plan(plan)
    second = import_plan(plan)
    assert second["already_loaded"] is True
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1
