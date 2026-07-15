from __future__ import annotations

from sqlalchemy import func, insert, select

from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.normalize import sha256_parts, stable_json
from res_ai_v2.pit_bootstrap import bootstrap_current_knowledge
from res_ai_v2.pit_schema import knowledge_directives, pit_observations
from res_ai_v2.schema import address_mappings, addresses, review_decisions, review_tasks


def test_existing_knowledge_is_copied_to_pit_before_rebuild(temp_db) -> None:
    now = utcnow()
    address_key = sha256_parts(["усады", "лаишевский", "", ""])
    selection = {
        "selected_res": ["Лаишевский район электрических сетей"],
        "locality": "Усады",
        "district": "Лаишевский",
        "settlement": "",
        "street": "",
    }
    with get_engine().begin() as conn:
        address_id = int(
            conn.execute(
                insert(addresses).values(
                    address_key=address_key,
                    locality="Усады",
                    district="Лаишевский",
                    settlement="",
                    street="",
                    locality_key="усады",
                    district_key="лаишевский",
                    settlement_key="",
                    street_key="",
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]
        )
        conn.execute(
            insert(address_mappings).values(
                address_id=address_id,
                res_name="Лаишевский район электрических сетей",
                branch_name="Приволжские сети",
                status="human_verified",
                source_confidence=100.0,
                human_confirmations=1,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        task_id = int(
            conn.execute(
                insert(review_tasks).values(
                    task_key=sha256_parts(["legacy", address_key]),
                    task_type="mapping_conflict",
                    subject_type="address",
                    subject_key=address_key,
                    title="Старое решение",
                    payload_json=stable_json(
                        {
                            "address": {
                                "address_key": address_key,
                                "locality": "Усады",
                                "district": "Лаишевский",
                                "settlement": "",
                                "street": "",
                            },
                            "options": [],
                        }
                    ),
                    priority=100,
                    status="closed",
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]
        )
        conn.execute(
            insert(review_decisions).values(
                task_id=task_id,
                selection_json=stable_json(selection),
                applied_by="Администратор",
                before_json="{}",
                after_json=stable_json(selection),
                active=True,
                created_at=now,
                reversed_at=None,
            )
        )
        result = bootstrap_current_knowledge(conn, now)

    assert result["observations"] == 1
    assert result["directives"] == 1
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(knowledge_directives)) or 0) == 1
