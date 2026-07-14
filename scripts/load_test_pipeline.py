from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from res_ai_v2.admin_service import request_full_analysis  # noqa: E402
from res_ai_v2.agent import run_until_event  # noqa: E402
from res_ai_v2.db import get_engine, initialize_database  # noqa: E402
from res_ai_v2.domain_schema import evidence_claims, source_evidence  # noqa: E402
from res_ai_v2.import_service import import_plan  # noqa: E402
from res_ai_v2.importer import ImportPlan, SheetPlan  # noqa: E402
from res_ai_v2.pit_schema import pit_observations  # noqa: E402
from res_ai_v2.repositories import stats  # noqa: E402
from res_ai_v2.review_queue import claim_review_task  # noqa: E402
from res_ai_v2.review_service import submit_review_and_update_agent  # noqa: E402
from res_ai_v2.schema import review_tasks, source_files  # noqa: E402
from res_ai_v2.structure import CURRENT_STRUCTURE  # noqa: E402


def _row(index: int, *, prefix: str, res_name: str) -> dict[str, Any]:
    locality = f"{prefix} {index}"
    district = f"Район {index % 43}"
    return {
        "branch": CURRENT_STRUCTURE[res_name],
        "res": res_name,
        "known_res": True,
        "locality": locality,
        "district": district,
        "settlement": "",
        "street": f"Улица {index % 500}",
        "text": "",
        "record_number": str(index),
        "sheet_name": "Данные",
        "row_number": index + 2,
        "raw": {
            "res": res_name,
            "locality": locality,
            "district": district,
            "street": f"Улица {index % 500}",
            "source_system": "synthetic-load-registry",
            "source_event_id": f"{prefix}-{index}",
        },
    }


def make_plan(rows: int, *, file_hash: str, prefix: str, start: int = 0) -> ImportPlan:
    res_name = list(CURRENT_STRUCTURE)[0]
    prepared = [_row(start + index, prefix=prefix, res_name=res_name) for index in range(rows)]
    return ImportPlan(
        file_hash=file_hash,
        file_name=f"{prefix}-{rows}.xlsx",
        source_kind="address_registry",
        sheets=[
            SheetPlan(
                sheet_name="Данные",
                header_row=0,
                columns={},
                confidence={},
                all_columns=[],
                rows=prepared,
                warnings=[],
            )
        ],
        detected_rows=rows,
        warnings=[],
    )


def _memory_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return round(value / (1024 * 1024), 2)
    return round(value / 1024, 2)


def _db_size_bytes() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///"))
        return path.stat().st_size if path.exists() else 0
    return 0


def _timed(callable_):
    started = time.perf_counter()
    result = callable_()
    return result, round(time.perf_counter() - started, 4)


def _process_event(result: dict[str, Any]) -> tuple[dict[str, Any] | None, float]:
    event_id = result.get("event_id")
    if not event_id:
        return None, 0.0
    agent, elapsed = _timed(
        lambda: run_until_event(int(event_id), max_events=1000, worker_id="load-pipeline")
    )
    if agent["target_status"] != "completed":
        raise AssertionError(f"Событие {event_id} не завершено: {agent}")
    target = agent.get("target_result") or {}
    return (target.get("result") or {}).get("analysis"), elapsed


def _counts() -> dict[str, int]:
    with get_engine().connect() as conn:
        return {
            "source_files": int(conn.scalar(select(func.count()).select_from(source_files)) or 0),
            "observations": int(
                conn.scalar(select(func.count()).select_from(pit_observations)) or 0
            ),
            "independent_evidence": int(
                conn.scalar(select(func.count()).select_from(evidence_claims)) or 0
            ),
            "technical_duplicates": int(
                conn.scalar(
                    select(func.count()).select_from(source_evidence).where(
                        source_evidence.c.technical_duplicate.is_(True)
                    )
                )
                or 0
            ),
            "open_tasks": int(
                conn.scalar(
                    select(func.count()).select_from(review_tasks).where(
                        review_tasks.c.status == "open"
                    )
                )
                or 0
            ),
            "conflicts": int(
                conn.scalar(
                    select(func.count()).select_from(review_tasks).where(
                        review_tasks.c.status == "open",
                        review_tasks.c.task_type == "mapping_conflict",
                    )
                )
                or 0
            ),
        }


def _concurrent_uploads(start: int) -> dict[str, Any]:
    plans = [
        make_plan(
            250,
            file_hash=f"{start + index:064x}",
            prefix=f"parallel-{index}",
            start=start + index * 1000,
        )
        for index in range(4)
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda plan: import_plan(plan, actor="load-parallel", wait_for_agent=False),
                plans,
            )
        )
    analyses = []
    for result in results:
        analysis, _ = _process_event(result)
        analyses.append(analysis)
    return {
        "files": len(results),
        "imported": sum(int(item["imported"]) for item in results),
        "failed": sum(int((item.get("agent") or {}).get("failed", 0)) for item in results),
        "analyses": len([item for item in analyses if item]),
    }


def _parallel_operator_and_upload(start: int) -> dict[str, Any]:
    res_names = list(CURRENT_STRUCTURE)
    conflict_rows = [
        {
            **_row(start, prefix="operator-conflict", res_name=res_names[0]),
            "raw": {
                **_row(start, prefix="operator-conflict", res_name=res_names[0])["raw"],
                "source_event_id": "operator-conflict-a",
            },
        },
        {
            **_row(start, prefix="operator-conflict", res_name=res_names[1]),
            "raw": {
                **_row(start, prefix="operator-conflict", res_name=res_names[1])["raw"],
                "source_event_id": "operator-conflict-b",
            },
        },
    ]
    conflict_plan = ImportPlan(
        file_hash=f"{start + 900000:064x}",
        file_name="operator-conflict.xlsx",
        source_kind="address_registry",
        sheets=[SheetPlan("Данные", 0, {}, {}, [], conflict_rows, [])],
        detected_rows=2,
        warnings=[],
    )
    conflict_result = import_plan(conflict_plan, actor="load-operator", wait_for_agent=False)
    _process_event(conflict_result)
    task = claim_review_task("Нагрузочный оператор")
    if task is None:
        raise AssertionError("Не создано задание для параллельной работы оператора.")
    parallel_plan = make_plan(
        200,
        file_hash=f"{start + 900001:064x}",
        prefix="operator-parallel-upload",
        start=start + 910000,
    )

    def upload():
        return import_plan(parallel_plan, actor="load-operator-upload", wait_for_agent=False)

    def review():
        return submit_review_and_update_agent(
            int(task["id"]),
            "Нагрузочный оператор",
            {
                "decision_type": "confirmed",
                "selected_res": [res_names[0]],
            },
            False,
            str(task["lease_token"]),
            wait_for_agent=False,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        upload_future = executor.submit(upload)
        review_future = executor.submit(review)
        upload_result = upload_future.result()
        review_result = review_future.result()
    _process_event(upload_result)
    if review_result.get("agent_event_id"):
        review_agent = run_until_event(
            int(review_result["agent_event_id"]),
            max_events=1000,
            worker_id="load-review",
        )
        if review_agent["target_status"] != "completed":
            raise AssertionError("Решение оператора не обработано агентом.")
    return {
        "upload_imported": int(upload_result["imported"]),
        "review_applied": bool(review_result.get("applied")),
        "review_scope": review_result.get("recalculation_scope"),
    }


def run_pipeline(rows: int) -> dict[str, Any]:
    initialize_database()
    engine = get_engine()
    query_count = 0

    def before_cursor_execute(*_args, **_kwargs):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        plan = make_plan(rows, file_hash=f"{rows:064x}", prefix=f"base-{rows}")
        before_queries = query_count
        imported, pit_seconds = _timed(
            lambda: import_plan(plan, actor="load-pipeline", wait_for_agent=False)
        )
        pit_queries = query_count - before_queries
        if int(imported["imported"]) != rows:
            raise AssertionError(f"Ожидалось {rows} новых доказательств: {imported}")
        before_queries = query_count
        analysis, analysis_seconds = _process_event(imported)
        analysis_queries = query_count - before_queries

        repeated, repeat_seconds = _timed(
            lambda: import_plan(plan, actor="load-repeat", wait_for_agent=False)
        )
        if not repeated["already_loaded"]:
            raise AssertionError("Повторный импорт того же файла не распознан.")

        copied_plan = make_plan(
            rows,
            file_hash=f"{rows + 1:064x}",
            prefix=f"base-{rows}",
        )
        copied, copied_seconds = _timed(
            lambda: import_plan(copied_plan, actor="load-copy", wait_for_agent=False)
        )
        if int(copied["imported"]) != 0 or int(copied["technical_duplicates"]) != rows:
            raise AssertionError(f"Копия реестра учтена неверно: {copied}")

        small_plan = make_plan(
            100,
            file_hash=f"{rows + 2:064x}",
            prefix=f"small-after-{rows}",
            start=rows + 1,
        )
        small, small_pit_seconds = _timed(
            lambda: import_plan(small_plan, actor="load-small", wait_for_agent=False)
        )
        small_analysis, small_analysis_seconds = _process_event(small)

        concurrent, concurrent_seconds = _timed(
            lambda: _concurrent_uploads(rows + 1000000)
        )
        parallel, parallel_seconds = _timed(
            lambda: _parallel_operator_and_upload(rows + 2000000)
        )

        full_result, full_seconds = _timed(
            lambda: request_full_analysis("load-pipeline", wait_for_agent=True)
        )
        if full_result["status"] != "completed":
            raise AssertionError(f"Полный самоанализ не завершен: {full_result}")

        before_queries = query_count
        _, interface_seconds = _timed(stats)
        interface_queries = query_count - before_queries
        counts = _counts()
        return {
            "rows": rows,
            "pit_write_seconds": pit_seconds,
            "analysis_seconds": analysis_seconds,
            "repeat_same_file_seconds": repeat_seconds,
            "copy_registry_seconds": copied_seconds,
            "small_file_pit_seconds": small_pit_seconds,
            "small_file_analysis_seconds": small_analysis_seconds,
            "concurrent_uploads_seconds": concurrent_seconds,
            "parallel_operator_seconds": parallel_seconds,
            "full_rebuild_seconds": full_seconds,
            "interface_response_seconds": interface_seconds,
            "database_queries": {
                "pit_write": pit_queries,
                "analysis": analysis_queries,
                "interface": interface_queries,
                "total": query_count,
            },
            "initial_result": imported,
            "initial_analysis": analysis,
            "copy_result": copied,
            "small_analysis": small_analysis,
            "concurrent": concurrent,
            "parallel_operator": parallel,
            "counts": counts,
            "memory_peak_mb": _memory_mb(),
            "database_size_bytes": _db_size_bytes(),
        }
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = run_pipeline(args.rows)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
