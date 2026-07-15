from __future__ import annotations

import time

from res_ai_v2.agent import run_agent_cycle
from res_ai_v2.import_service import import_plan
from res_ai_v2.repositories import stats
from tests.test_import_and_analysis import make_plan


def test_registry_with_5000_rows_is_processed_in_batches(temp_db) -> None:
    rows = [
        {
            "res": "Лаишевский район электрических сетей",
            "locality": "Тестовый населенный пункт",
            "district": "Лаишевский",
            "street": f"Улица {index}",
            "text": f"Лаишевский район, тестовый населенный пункт, улица {index}",
        }
        for index in range(5000)
    ]
    started = time.perf_counter()
    imported = import_plan(
        make_plan("5" * 64, rows),
        wait_for_agent=False,
    )
    import_seconds = time.perf_counter() - started

    started = time.perf_counter()
    cycle = run_agent_cycle(max_events=20, worker_id="large-registry-test")
    analysis_seconds = time.perf_counter() - started

    assert imported["imported"] == 5000
    assert cycle.failed == 0
    assert stats()["addresses"] == 5000
    assert stats()["mappings"] == 5000
    assert import_seconds < 60
    assert analysis_seconds < 60
