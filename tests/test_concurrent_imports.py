from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from res_ai_v2.db import get_engine
from res_ai_v2.domain_schema import source_evidence
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
        "source_system": "112",
        "source_event_id": "112-technical-copy",
    }


def test_same_file_uploaded_simultaneously_is_saved_once(temp_db) -> None:
    plan = make_plan("f" * 64, [_row()])
    barrier = threading.Barrier(4)

    def upload() -> dict:
        barrier.wait()
        return import_plan(plan, actor="Параллельный тест", wait_for_agent=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [
            future.result(timeout=30)
            for future in [executor.submit(upload) for _ in range(4)]
        ]

    assert sum(not item["already_loaded"] for item in results) == 1
    assert sum(item["already_loaded"] for item in results) == 3
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(source_files)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(source_rows)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1


def test_different_copies_of_same_registry_do_not_become_independent(temp_db) -> None:
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
    assert sum(item["imported"] for item in results) == 1
    assert sum(item["technical_duplicates"] for item in results) == len(plans) - 1
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(source_files)) or 0) == len(plans)
        assert int(conn.scalar(select(func.count()).select_from(source_rows)) or 0) == len(plans)
        assert int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0) == 1
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1
        technical = int(
            conn.scalar(
                select(func.count()).select_from(source_evidence).where(
                    source_evidence.c.technical_duplicate.is_(True)
                )
            )
            or 0
        )
        observation = conn.execute(select(pit_observations)).one()
    assert technical == len(plans) - 1
    assert int(observation.occurrence_count) == 1
    assert int(observation.source_count) == 1
