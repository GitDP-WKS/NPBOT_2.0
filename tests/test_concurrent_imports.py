from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from res_ai_v2.db import get_engine
from res_ai_v2.import_service import import_plan
from res_ai_v2.pit_schema import pit_observations, pit_occurrences
from res_ai_v2.schema import source_files, source_rows
from tests.test_import_and_analysis import make_plan


def _row() -> dict[str, str]:
    return {
        "res": "Лаишевский район электрических сетей",
        "locality": "Усады",
        "district": "Лаишевский",
        "text": "Нет электричества",
    }


def test_same_file_uploaded_simultaneously_is_saved_once(temp_db) -> None:
    plan = make_plan("f" * 64, [_row()])
    barrier = threading.Barrier(4)

    def upload() -> dict:
        barrier.wait()
        return import_plan(plan, actor="Параллельный тест", wait_for_agent=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [future.result(timeout=30) for future in [executor.submit(upload) for _ in range(4)]]

    assert sum(not item["already_loaded"] for item in results) == 1
    assert sum(item["already_loaded"] for item in results) == 3
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(source_files)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(source_rows)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1


def test_different_files_with_same_row_share_one_observation(temp_db) -> None:
    plans = [make_plan(f"{index:064x}", [_row()]) for index in range(1, 7)]
    barrier = threading.Barrier(len(plans))

    def upload(plan) -> dict:
        barrier.wait()
        return import_plan(plan, actor="Параллельный тест", wait_for_agent=False)

    with ThreadPoolExecutor(max_workers=len(plans)) as executor:
        results = [
            future.result(timeout=30)
            for future in [executor.submit(upload, plan) for plan in plans]
        ]

    assert all(not item["already_loaded"] for item in results)
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(source_files)) or 0) == len(plans)
        assert int(conn.scalar(select(func.count()).select_from(source_rows)) or 0) == len(plans)
        assert int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == len(plans)
        observation = conn.execute(select(pit_observations)).one()
    assert int(observation.occurrence_count) == len(plans)
    assert int(observation.source_count) == len(plans)
