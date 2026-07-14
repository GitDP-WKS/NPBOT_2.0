from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import stats
from tests.test_import_and_analysis import make_plan


def test_large_import_is_processed_in_batches(temp_db) -> None:
    rows = [
        {
            "res": "Лаишевский район электрических сетей",
            "locality": f"Тестовый населенный пункт {index}",
            "district": "Лаишевский",
        }
        for index in range(1500)
    ]

    result = import_plan(make_plan("7" * 64, rows))

    assert result["imported"] == 1500
    assert result["agent"]["target_status"] == "completed"
    assert result["agent"]["failed"] == 0
    assert stats()["addresses"] == 1500
