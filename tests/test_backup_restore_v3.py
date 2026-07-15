from __future__ import annotations

import json

from sqlalchemy import func, select

from res_ai_v2.backup_service import backup_snapshot, inspect_backup, request_restore
from res_ai_v2.db import get_engine
from res_ai_v2.domain_schema import evidence_claims
from res_ai_v2.import_service import import_plan
from res_ai_v2.pit_schema import pit_observations
from res_ai_v2.repositories import knowledge_rows
from res_ai_v2.schema import source_files
from tests.test_import_and_analysis import make_plan

RES = "Лаишевский район электрических сетей"


def _plan(file_hash: str, locality: str, event_id: str):
    return make_plan(
        file_hash,
        [
            {
                "res": RES,
                "locality": locality,
                "district": "Лаишевский",
                "source_system": "112",
                "source_event_id": event_id,
            }
        ],
    )


def test_backup_contains_raw_pit_provenance_and_directives(temp_db) -> None:
    import_plan(_plan("71" * 32, "Усады", "backup-1"))
    snapshot = backup_snapshot()
    info = inspect_backup(snapshot)
    payload = json.loads(snapshot)
    assert info["format"] == "res_ai_v3_full_backup"
    assert info["row_count"] > 0
    assert len(payload["tables"]["pit_observations"]) == 1
    assert len(payload["tables"]["evidence_claims"]) == 1
    assert len(payload["tables"]["canonical_observations"]) == 1
    assert "knowledge_directives" in payload["tables"]


def test_restore_returns_raw_state_and_rebuilds_working_knowledge(temp_db) -> None:
    import_plan(_plan("72" * 32, "Усады", "restore-original"))
    snapshot = backup_snapshot()
    import_plan(_plan("73" * 32, "Столбище", "restore-extra"))
    assert len(knowledge_rows()) == 2

    result = request_restore(snapshot, actor="Тест восстановления", wait_for_agent=True)
    assert result["status"] == "completed"

    with get_engine().connect() as conn:
        files = int(conn.scalar(select(func.count()).select_from(source_files)) or 0)
        observations = int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0)
        evidence = int(conn.scalar(select(func.count()).select_from(evidence_claims)) or 0)
    assert files == 1
    assert observations == 1
    assert evidence == 1
    rows = knowledge_rows()
    assert len(rows) == 1
    assert rows[0]["locality"] == "Усады"
