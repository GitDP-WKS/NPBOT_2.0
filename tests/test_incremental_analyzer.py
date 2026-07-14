from __future__ import annotations

from pathlib import Path

from sqlalchemy import insert, select

from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.incremental_analyzer import analyze_changed_addresses
from res_ai_v2.normalize import sha256_parts
from res_ai_v2.schema import address_mappings, addresses, review_tasks
from res_ai_v2.structure import CURRENT_STRUCTURE


def _add_address(locality: str, district: str, res: str) -> int:
    now = utcnow()
    key = sha256_parts([locality.lower(), district.lower(), "", ""])
    with get_engine().begin() as conn:
        address_id = int(
            conn.execute(
                insert(addresses).values(
                    address_key=key,
                    locality=locality,
                    district=district,
                    settlement="",
                    street="",
                    locality_key=locality.lower(),
                    district_key=district.lower(),
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
                res_name=res,
                branch_name=CURRENT_STRUCTURE[res],
                status="source_only",
                source_confidence=80.0,
                human_confirmations=0,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
    return address_id


def test_incremental_analysis_expands_same_locality_only(temp_db: Path):
    res_names = list(CURRENT_STRUCTURE)
    first = _add_address("Усады", "Лаишевский район", res_names[0])
    second = _add_address("Усады", "Высокогорский район", res_names[1])
    unrelated = _add_address("Константиновка", "Советский район", res_names[2])

    result = analyze_changed_addresses([first])
    assert result["rows"] == 2

    with get_engine().connect() as conn:
        unrelated_status = conn.scalar(
            select(address_mappings.c.status).where(address_mappings.c.address_id == unrelated)
        )
        second_status = conn.scalar(
            select(address_mappings.c.status).where(address_mappings.c.address_id == second)
        )
    assert unrelated_status == "source_only"
    assert second_status == "consistent"


def test_incremental_analysis_creates_conflict_task(temp_db: Path):
    res_names = list(CURRENT_STRUCTURE)
    address_id = _add_address("Тестовый", "Тестовый район", res_names[0])
    now = utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            insert(address_mappings).values(
                address_id=address_id,
                res_name=res_names[1],
                branch_name=CURRENT_STRUCTURE[res_names[1]],
                status="source_only",
                source_confidence=80.0,
                human_confirmations=0,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
    result = analyze_changed_addresses([address_id])
    assert result["conflicts"] == 1
    with get_engine().connect() as conn:
        task = conn.execute(
            select(review_tasks.c.id).where(
                review_tasks.c.task_type == "mapping_conflict",
                review_tasks.c.status == "open",
            )
        ).first()
    assert task is not None
