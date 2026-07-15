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
                    "source_event_id": "pit-1",
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
    assert 45.0 <= float(row["source_confidence"]) <= 100.0


def test_copy_of_same_observation_does_not_change_trust_or_create_task(temp_db) -> None:
    row = {
        "res": "Лаишевский район электрических сетей",
        "locality": "Усады",
        "district": "Лаишевский",
        "source_system": "112",
        "source_event_id": "same-event",
    }
    first = import_plan(make_plan("2" * 64, [row]))
    before = knowledge_rows()[0]
    second = import_plan(make_plan("3" * 64, [row]))
    after = knowledge_rows()[0]
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["technical_duplicates"] == 1
    assert second["event_id"] is None
    with get_engine().connect() as conn:
        pit = conn.execute(select(pit_observations)).one()
        assert pit.occurrence_count == 1
        assert pit.source_count == 1
    assert after["source_confidence"] == before["source_confidence"]
    tasks = list_review_tasks("Проверяющий")
    assert not [task for task in tasks if task["task_type"] == "duplicate_observation"]


def test_reuploading_same_file_does_not_add_pit_occurrence(temp_db) -> None:
    plan = make_plan(
        "4" * 64,
        [
            {
                "res": "Лаишевский район электрических сетей",
                "locality": "Усады",
                "district": "Лаишевский",
                "source_event_id": "same-file",
            }
        ],
    )
    import_plan(plan)
    second = import_plan(plan)
    assert second["already_loaded"] is True
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1
