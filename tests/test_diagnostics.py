from __future__ import annotations

from sqlalchemy import insert

from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.diagnostics import run_diagnostics
from res_ai_v2.schema import address_mappings, addresses


def test_diagnostics_reports_healthy_empty_database(temp_db):
    result = run_diagnostics()
    assert result["healthy"] is True
    assert result["problems"] == []


def test_diagnostics_finds_unofficial_res(temp_db):
    now = utcnow()
    with get_engine().begin() as conn:
        address_id = int(
            conn.execute(
                insert(addresses).values(
                    address_key="x",
                    locality="Тест",
                    district="",
                    settlement="",
                    street="",
                    locality_key="тест",
                    district_key="",
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
                res_name="Несуществующий РЭС",
                branch_name="Неизвестный филиал",
                status="source_only",
                source_confidence=80.0,
                human_confirmations=0,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
    result = run_diagnostics()
    assert result["healthy"] is False
    assert result["counts"]["unofficial_res"] == 1
