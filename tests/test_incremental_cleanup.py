from __future__ import annotations

from sqlalchemy import select

from res_ai_v2.db import get_engine
from res_ai_v2.incremental_analyzer import analyze_changed_addresses
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import create_or_update_task, knowledge_rows
from res_ai_v2.schema import review_tasks
from tests.test_import_and_analysis import make_plan


def test_stale_missing_context_task_is_cancelled(temp_db) -> None:
    import_plan(
        make_plan(
            "9" * 64,
            [
                {
                    "res": "Лаишевский район электрических сетей",
                    "locality": "Столбище",
                    "district": "Лаишевский",
                }
            ],
        )
    )
    row = knowledge_rows()[0]
    task_id = create_or_update_task(
        task_key="stale-missing-context",
        task_type="missing_context",
        subject_type="mapping",
        subject_key=str(row["mapping_id"]),
        title="Устаревшее задание",
        payload={"mapping_id": row["mapping_id"], "address": {}},
        priority=80,
    )

    analyze_changed_addresses([row["address_id"]])

    with get_engine().connect() as conn:
        status = conn.scalar(select(review_tasks.c.status).where(review_tasks.c.id == task_id))
    assert status == "cancelled"
