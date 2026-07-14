from __future__ import annotations

import json

from sqlalchemy import select

from res_ai_v2.db import get_engine
from res_ai_v2.import_service import import_plan
from res_ai_v2.pit_schema import knowledge_generations
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from tests.test_import_and_analysis import make_plan


def test_regular_review_rebuilds_only_related_observations(temp_db) -> None:
    import_plan(
        make_plan(
            "c3" * 32,
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
    )
    task = claim_review_task("Иванов")
    assert task is not None

    result = submit_review_and_update_agent(
        task["id"],
        "Иванов",
        {
            "selected_res": [task["payload"]["options"][0]["res"]],
            "locality": task["payload"]["address"]["locality"],
            "district": task["payload"]["address"]["district"],
            "settlement": task["payload"]["address"]["settlement"],
            "street": task["payload"]["address"]["street"],
        },
        False,
        task["lease_token"],
    )
    assert result["agent_status"] == "completed"

    with get_engine().connect() as conn:
        generation = conn.execute(
            select(knowledge_generations)
            .order_by(knowledge_generations.c.id.desc())
            .limit(1)
        ).one()
    stats = json.loads(str(generation.stats_json))
    assert generation.full_rebuild is False
    assert stats["rows_scanned"] == 2
